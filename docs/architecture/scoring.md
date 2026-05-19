# Scoring

AJSAA scores every discovered job in a single LLM call against all loaded CV profiles.
Jobs below `min_score` are dropped; the rest are sorted descending and written to
`query/jobs_scored.jsonl` before being handed to the storage and notification steps.

## Score rubric

| Score | Meaning |
|---|---|
| < 70 | Filtered out — not stored |
| 70–74 | Weak match — stretch role |
| 75–84 | Good match — worth applying |
| 85–94 | Strong match — prioritise |
| 95 | Near-perfect (capped to avoid inflated scores) |

---

## How it works

```
query/jobs_found.jsonl   ← written by aggregate_jobs
        ↓
  analyze_jobs node
        ↓  (1 LLM call — all jobs + all CVs)
        ↓
query/jobs_scored.jsonl  ← each line = original job + score, best_cv,
                            recommendation, reasoning
```

1. `analyze_jobs` reads from `query/jobs_found.jsonl` (the search checkpoint).
2. All compressed CVs are sent alongside all job descriptions in one prompt.
3. The LLM returns a JSON array — one entry per job that passes `min_score`.
4. Results are sorted descending, written to `query/jobs_scored.jsonl`, and
   passed to `store_results` via `scored_jobs` in the agent state.

---

## Output schema per job

Each scored job dict has these fields appended to the original search fields:

| Field | Type | Description |
|---|---|---|
| `score` | int 0–95 | Overall fit score |
| `best_cv` | str | Name of the CV that scored highest for this job |
| `recommendation` | str | `APPLY`, `CONSIDER`, or `SKIP` |
| `reasoning` | str | One-sentence justification from the LLM |

---

## Tuning thresholds

Controlled via `config/score_config.yaml` — no prompt editing required:

```yaml
scoring:
  min_score: 70    # jobs below this are discarded
  max_score: 95    # scores are capped here
```

---

## CV compression

Before scoring, each CV is reduced to a ~200-character summary to stay within
token limits. The compressed version is cached to disk by content hash —
unchanged CVs are not re-compressed across runs.

```
YOE: 12 years
Role: Technical Product Manager
Skills: LangGraph, Python, Hadoop, Kafka, GCP
Domain: Data platforms, AI enablement, Internal tools
Metrics: 73% incident reduction, 99.6% SLA, ×3.5 deployment capacity
```

---

## Customising the scoring prompt

The instructions block is loaded from `query/JOB_SCORING_PROMPT.md`.
Edit that file to change scoring philosophy, priorities, or anti-hallucination
rules. The output schema (`job_index`, `best_cv`, `score`, `recommendation`,
`reasoning`) is always appended by code — do not add it to the prompt file.

---

## Token consumption (observed)

| Step | Approx. tokens |
|---|---|
| CV compression — 1 CV, first run | ~800 |
| Query generation (if no file) | ~1,200 |
| One-shot scoring — 15 jobs, 1 CV | ~6,000 |
| Web search (directive + Tavily) | ~8,000 |
| **Total typical run** | **~16,000** |
