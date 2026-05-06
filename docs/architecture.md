# AJSAA — Architecture & Design Reference

## Provider factory pattern

Every major concern (LLM, search, storage, notifications) follows the same factory pattern. To swap any component, no node code changes — only a config line:

```
providers/<concern>/base.py       — abstract interface
providers/<concern>/<impl>.py     — concrete provider
providers/<concern>/factory.py    — build_<concern>(cfg) dispatcher
```

`config.yaml` drives selection:

```yaml
llm:       { provider: anthropic }
storage:   { provider: google_drive }
notifications: { channels: [telegram, slack] }
```

## LangGraph state

All nodes read and write a single `AgentState` TypedDict (`agent/state.py`). Nodes never call each other directly — everything flows through state. Conditional edges (`_needs_convert_cvs`, `_needs_generate_queries`, `_needs_notifications`) skip nodes when their preconditions are not met, keeping each node pure and testable.

## Scoring design

### Rubric

| Score | Meaning |
|---|---|
| < 70 | Filtered out — not stored |
| 70–74 | Weak match — stretch role |
| 75–84 | Good match — worth applying |
| 85–94 | Strong match — prioritise |
| 95 | Near-perfect (capped — avoids inflated scores) |

### Batch scoring

Jobs are scored in batches of 10 per LLM call (configurable). Each batch includes all compressed CVs, so the LLM compares every job against every profile in one pass. This reduced per-job API calls by ~90% vs. the naive one-job-per-call approach.

### CV compression

Before scoring, each CV is compressed to ~600 tokens via a structured extraction prompt:
```
YOE: X years
Role: current/most recent title
Skills: top 5 technical skills
Domain: top 3 domains
Metrics: top 3 quantified achievements
```
Compression runs once per CV per pipeline execution; the result is reused across all batches.

### Scoring prompt

The scoring rubric is stored in `query/JOB_SCORING_PROMPT.md` and hot-swappable — edit the file, the next run picks it up without a code change.

## Search connector configurability

Each connector entry in `config.yaml` supports:

```yaml
connectors:
  - name: france_travail
    enabled: true                # false = skip without removing from config
    max_results_per_query: 10    # overrides global setting for this connector

  - name: anthropic_web
    enabled: true
    fallback_only: true          # only fires if all non-fallback connectors return 0 results
    max_results_per_query: 5
    max_queries: 5               # cap total queries to limit token cost
```

`fallback_only` is the key mechanism for graceful degradation: real API connectors run first; the LLM fallback only activates when they return nothing (e.g. missing API credentials).

## Deduplication

Jobs are keyed by a 16-character SHA-256 hash of `"{title}|{company}|{source_id}"`. `LocalJSONProvider.save()` loads existing records, computes the set of known IDs, and skips any job whose hash already exists. Subsequent runs on the same market therefore add zero duplicate rows.

## Token consumption (observed, v1.0.0)

| Step | Tokens (approx.) |
|---|---|
| CV compression (1 CV) | ~800 |
| Query generation (if no file) | ~1 200 |
| Web search fallback (5 queries × 5 results) | ~6 000 |
| Batch scoring (15 jobs, 1 CV, 2 batches) | ~12 000 |
| **Total (real connectors + credentials)** | **~14 000** |
| **Total (LLM fallback, no credentials)** | **~40–50 000** |

The single largest lever is registering France Travail + Adzuna credentials — it eliminates the LLM fallback and drops token use by ~70%.

---

## Configuration reference

| File | Purpose | Committed? |
|---|---|---|
| `config.yaml` | All behavioural settings | Yes |
| `.env` | All secrets | No |
| `query/job_queries.md` | Search queries (one per line) | Yes |
| `query/company_list.md` | Companies to check career pages | Yes |
| `query/resume/` | CV profiles (PDF or MD) | No — gitignored |
| `query/JOB_SCORING_PROMPT.md` | Scoring rubric (hot-swappable) | Yes |
| `.data/jobs.json` | Job store — source of truth | No |
| `scoring_profiles/*.json` | Learned scoring profiles (Phase 12) | No |
| `logs/` | Run logs | No |

## Environment variables reference

| Variable | Used when | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | `llm.provider: anthropic` | Claude completions |
| `OPENAI_API_KEY` | `llm.provider: openai` | GPT completions |
| `FRANCE_TRAVAIL_CLIENT_ID` | `connectors: france_travail` | OAuth2 client ID |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | `connectors: france_travail` | OAuth2 client secret |
| `ADZUNA_APP_ID` | `connectors: adzuna` | API app ID |
| `ADZUNA_APP_KEY` | `connectors: adzuna` | API app key |
| `EMAIL_FROM` | `channels: email` | Sender address |
| `EMAIL_TO` | `channels: email` | Recipient address |
| `EMAIL_SMTP_HOST` | `channels: email` | SMTP host (default: smtp.gmail.com) |
| `EMAIL_SMTP_PORT` | `channels: email` | SMTP port (default: 587) |
| `EMAIL_PASSWORD` | `channels: email` | App password |
| `SLACK_WEBHOOK_URL` | `channels: slack` | Incoming webhook URL |
| `TELEGRAM_BOT_TOKEN` | `channels: telegram` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | `channels: telegram` | Target chat ID |
