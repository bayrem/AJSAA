# Scoring

AJSAA supports three scoring modes, selectable via `scoring.mode` in `config.yaml`. All modes share the same threshold, cap, and output schema.

## Score rubric

| Score | Meaning |
|---|---|
| < 70 | Filtered out — not stored |
| 70–74 | Weak match — stretch role |
| 75–84 | Good match — worth applying |
| 85–94 | Strong match — prioritise |
| 95 | Near-perfect (capped to avoid inflated scores) |

---

## Mode: `llm` (default)

Every job in every run is scored by the LLM. Jobs are sent in batches of 10 alongside all compressed CVs. The LLM returns a JSON array with scores and one-sentence reasoning per job.

**When to use:** When you want maximum accuracy and don't yet have a scoring profile. Costs ~12,000 tokens per run for a typical result set.

**Token cost:** ~800–1,200 per batch of 10 jobs.

---

## Mode: `static`

Jobs are scored using a pre-built regex profile. No LLM calls are made during scoring.

The static scorer computes:

```
score = 50 (baseline)
      + sum(weight for each positive_signal that matches the job description)
      + sum(weight for each negative_signal that matches)
      + sum(bonus for each domain_bonus term that matches)

clamped to [0, 95]
```

Pattern matching is case-insensitive and applied to the combined `title + description` text.

**When to use:** After running in `hybrid` mode at least once to bootstrap a profile. Zero LLM tokens per run.

**Prerequisite:** A valid `scoring_profiles/{cv_name}.json` must exist. If it doesn't, the node logs an error and returns no jobs. Run in `hybrid` mode first to generate the profile.

---

## Mode: `hybrid`

The default for production use. Combines LLM accuracy on first run with static efficiency on subsequent runs.

```
First run per CV:
  LLM scores all jobs
        └─ Profile extraction: top + bottom results → distilled regex profile
        └─ Profile saved to scoring_profiles/

Subsequent runs:
  Static scorer handles all jobs
        ├─ Score > uncertainty_band.high  → kept as-is (certain pass)
        ├─ Score < uncertainty_band.low   → filtered as-is (certain fail)
        └─ Score within band              → escalated to LLM for second opinion
```

**`uncertainty_band`** (default `[60, 80]`): jobs scoring in this range are re-scored by the LLM. A wider band means more LLM calls; a narrower band means only near-threshold jobs go back to the LLM.

**Profile invalidation:** The profile is keyed by CV content hash. Edit your CV and the profile is automatically invalidated — the next run bootstraps a fresh profile from the new content.

**When to use:** Daily production runs where you want near-zero token cost after the first bootstrap run.

---

## CV compression

All scoring modes use compressed CVs to stay within token limits. Before scoring, each CV is reduced to a ~200-character summary:

```
YOE: 12 years
Role: Technical Product Manager
Skills: LangGraph, Python, Hadoop, Kafka, GCP
Domain: Data platforms, AI enablement, Internal tools
Metrics: 73% incident reduction, 99.6% SLA, ×3.5 deployment capacity
```

The compressed version is cached to disk by content hash. Unchanged CVs are never re-compressed across runs.

---

## Token consumption (observed)

| Step | Approx. tokens |
|---|---|
| CV compression — 1 CV, first run | ~800 |
| Query generation (if no file) | ~1,200 |
| Batch scoring — 15 jobs, 1 CV | ~6,000 |
| Web search fallback — 5 queries | ~12,000 |
| **Total (real connectors configured)** | **~14,000** |
| **Total (LLM fallback, no credentials)** | **~40–50,000** |

The single largest lever is registering France Travail and Adzuna credentials — it eliminates the LLM web search fallback and drops token use by ~70%.
