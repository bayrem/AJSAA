# Prompt Engineering Guide

One prompt drives AJSAA's scoring quality: the scoring prompt sent to the LLM with all discovered jobs.

---

## Scoring prompt

The instructions block is loaded from `query/JOB_SCORING_PROMPT.md`. The code appends the CV block, job data, rules, and output schema — you only control the instructions.

### What the LLM receives

- A `SystemMessage` enforcing JSON-only output (no prose)
- Your instructions from `query/JOB_SCORING_PROMPT.md`
- All compressed CVs (200–300 chars each)
- All job descriptions (first 1,000 chars each), wrapped in `<job_data>` tags
- Explicit rules: score 0–{max_score}, return only jobs scoring ≥ {min_score}

### What you can tune

**Score ceiling and threshold**

Controlled via `config/score_config.yaml` — no prompt editing required:

```yaml
scoring:
  min_score: 70    # jobs below this are discarded
  max_score: 95    # scores are capped here
```

Raising `min_score` reduces noise but may miss borderline matches. Lowering it surfaces more results to review manually.

**Scoring philosophy** — edit `query/JOB_SCORING_PROMPT.md`

The default instructions prioritise: technical skills → domain experience → seniority → preferred skills → soft skills. Change the order or weight if your market rewards different signals.

**Calibration examples**

If the LLM scores systematically too high or too low, add calibration examples to the prompt file:

```
Calibration:
- A generic "Chef de Produit" posting with no AI/data context: score 52
- A Data PM role with explicit ML pipeline ownership: score 82
- An AI Product Manager role at a data platform company: score 88
```

**Language coverage**

The LLM handles French/English prompts well, but explicit signal phrases prevent ambiguity for niche roles:

```
Market context (France):
- "chef de produit data" = Data Product Manager — score as PM role
- "chargé de projet" = Project Coordinator — NOT a PM role, score lower
- "alternance" = apprenticeship — negative signal for senior roles
```

---

## Anti-injection framing

Job descriptions come from third-party boards and may contain adversarial content. The prompt wraps job data in `<job_data>` tags and the LLM is instructed to treat everything inside as plain text, not instructions. A `SystemMessage` is always prepended to reinforce JSON-only output mode.

Do not remove the `<job_data>` wrapper or the anti-injection preamble from `_build_prompt` in `providers/scoring/llm_scorer.py`.
