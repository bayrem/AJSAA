# Design Decisions

This page explains the *why* behind AJSAA's major architectural choices.

---

## Why LangGraph over a plain function pipeline?

A sequential `main()` calling functions would work, but LangGraph adds three things that matter for an agent:

**Typed state.** `AgentState` is a `TypedDict`. Every node's inputs and outputs are statically checkable. Adding a field means updating one file (`state.py`); mypy catches missing keys at CI time rather than at runtime.

**Declarative conditional routing.** Nodes are skipped via edge functions, not `if` blocks embedded inside other nodes. This keeps each node's logic self-contained and makes the graph inspectable as a data structure — you can visualise and reason about the flow without reading code.

**Observability without instrumentation.** LangGraph integrates with LangSmith out of the box. Tracing, step timing, and state snapshots at each node are available without any changes to node code.

The trade-off is a dependency on `langgraph` and the discipline of returning full state dicts rather than mutating in place — which is a feature: it makes every node trivially testable with a plain dict.

---

## Why content-hash deduplication over URL deduplication?

Job board URLs are unstable. The same posting often appears with different query parameters, redirect chains, or short-link wrappers across boards. Deduplicating on URL would store the same job multiple times.

Title + company + a board-specific ID is stable. The 16-character SHA-256 of `"{title}|{company}|{source_id}"` is the canonical `job_id`. If the board provides no ID, the hash of `title|company|location` is used. The same job appearing on both France Travail and Adzuna will deduplicate correctly as long as the title and company strings match — which they do in practice for the large majority of listings.

---

## Why batch scoring over per-job scoring?

Naïve scoring sends one LLM call per job. With 30 results across a run:

| Approach | LLM calls | Approx. latency | Relative cost |
|---|---|---|---|
| Per-job | 30 | ~90s | 30× |
| Batch of 10 | 3 | ~15s | 3× |

Batching groups 10 jobs into one prompt alongside all compressed CVs. The LLM compares every job against every profile in a single pass and returns a JSON array. This reduces API calls by ~90% and wall-clock time proportionally.

The trade-off: a malformed LLM response drops the entire batch. The implementation mitigates this by validating `job_index` bounds, catching JSON parse errors, and logging bad batches without crashing the run.

---

## Why `claude_code_agent` as the default LLM provider?

Running against the Anthropic API burns API credit on every development run. `claude_code_agent` routes LLM calls through the local Claude CLI, which runs against a Claude Pro subscription at zero marginal cost per call.

The provider implements LangChain's `BaseChatModel`, so the rest of the codebase is unaware of the difference. Switching to the direct API for production or CI requires only one config line:

```yaml
# Development — uses Claude CLI / Pro subscription
llm:
  provider: claude_code_agent

# Production / CI — uses Anthropic API key
llm:
  provider: anthropic
```

---

## Why the `fallback_only` pattern for LLM web search?

LLM-based web search works without any API credentials but costs ~6,000–12,000 tokens per run. Real API connectors (France Travail, Adzuna) return structured data at zero token cost but require registration.

The `fallback_only` flag makes the LLM connector an automatic graceful fallback:

- A new user with no API keys gets results immediately via the fallback.
- A configured user never pays LLM token cost for search.
- The switchover is automatic — no config change required as credentials are added.

---

## Why CV compression before scoring?

A full CV is typically 3,000–6,000 characters. Scoring 10 jobs against 2 CVs in one batch would put ~12,000+ characters of CV content into every prompt, pushing context windows and increasing cost significantly.

Compression runs once per CV per run using a structured extraction prompt:

```
YOE: X years
Role: current title
Skills: top 5 technical skills
Domain: top 3 domains
Metrics: top 3 quantified achievements
```

The output is ~200–300 characters. Context overhead per batch drops from ~12,000 to ~600 characters of CV content, making it viable to score 10 jobs × 3 CVs in a single call without approaching token limits.

The compression result is cached to disk, keyed by a SHA-256 content hash of the CV. If the CV file changes, the hash changes and the cache is transparently invalidated on the next run.

---

## Why one-shot LLM scoring from a JSONL checkpoint?

Scoring is decoupled from search via `query/jobs_found.jsonl`. `aggregate_jobs`
writes the checkpoint; `analyze_jobs` reads it. This means:

- The scoring step is independently runnable (`test_node.py analyze_jobs --from <state>`)
  without re-running the expensive search step.
- A single LLM call covers all jobs and all CVs — no per-batch CV repetition, no
  round-trip overhead between batches.
- The scored output in `query/jobs_scored.jsonl` is independently inspectable before
  it reaches the storage or notification steps.

A hybrid regex-profile approach was explored but removed: the profile quality
depended heavily on bootstrap sample size, and the complexity (profile extraction,
borderline escalation, cache invalidation) was not worth the token savings given that
the directive search + Tavily extract architecture already caps the job set at ~30
unique postings per run.

---

## Why per-connector semaphores for parallel search?

Running all `(connector, query)` pairs in a single `ThreadPoolExecutor` without throttling would hammer rate-limited APIs. France Travail enforces 3 requests per second sustained.

Each connector gets its own `Semaphore(n)` where `n` defaults to the connector's known rate limit (configurable via `max_concurrent` in config). All tasks share one thread pool but acquire their connector's semaphore before making the API call. This achieves maximum parallelism across connectors while respecting each board's individual limit.
