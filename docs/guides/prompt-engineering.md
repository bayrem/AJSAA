# Prompt Engineering Guide

Two prompts drive AJSAA's scoring quality: the batch scoring prompt and the hybrid profile extraction prompt. This guide explains how to tune both for your market and profile.

---

## Batch scoring prompt

Located in `providers/scoring/llm_scorer.py`. Sent to the LLM with each batch of 10 jobs.

### What the LLM receives

- All compressed CVs (200–300 chars each)
- Up to 10 job descriptions (first 300 chars each)
- Explicit instructions: score 0–{max_score}, only return jobs scoring ≥ {min_score}

### What you can tune

**Score ceiling and threshold**

Controlled via `config.yaml` — no prompt editing required:

```yaml
scoring:
  min_score: 70    # jobs below this are discarded
  max_score: 95    # scores are capped here
```

Raising `min_score` reduces noise but may miss borderline matches. Lowering it surfaces more results to review manually.

**Calibration examples**

If the LLM scores systematically too high or too low, add calibration examples to the prompt:

```python
prompt = f"""Score these {len(batch)} jobs against the CV profiles below.
...
Calibration:
- A generic "Chef de Produit" posting with no AI/data context: score 52
- A Data PM role with explicit ML pipeline ownership: score 82
- An AI Product Manager role at a data platform company: score 88
..."""
```

**Language coverage**

The LLM handles French/English prompts well, but explicit signal phrases prevent ambiguity for niche roles. Add market-specific context after the main rules:

```python
prompt += """
Market context (France):
- "chef de produit data" = Data Product Manager — score as PM role
- "chargé de projet" = Project Coordinator — NOT a PM role, score lower
- "alternance" = apprenticeship — negative signal for senior roles
"""
```

---

## Hybrid profile extraction prompt

Located in `providers/scoring/hybrid_scorer.py`. Called once per CV to distil LLM scoring results into a reusable regex profile.

### Profile structure

```json
{
  "positive_signals": [
    {"pattern": "data platform|plateforme de données", "weight": 15},
    {"pattern": "product manager|chef de produit", "weight": 12}
  ],
  "negative_signals": [
    {"pattern": "junior|stagiaire|alternance|alternant", "weight": -50}
  ],
  "domain_bonus": {
    "intelligence artificielle en production": 8
  },
  "uncertainty_band": [65, 82]
}
```

### Calibration rules

| Rule | Value | Reason |
|---|---|---|
| Sum of positive weights | 40–55 | Below 40 = even strong matches fall below threshold |
| Individual positive weight | 8–18 | Prevents one signal from dominating |
| `domain_bonus` entries | max 2, ≤ 8 each | Reserved for highly specific JD terms |
| `negative_signals` | 3–5 entries | Cover both French and English variants |

### Signals must be grounded in job description language

The most common mistake is using CV tech-stack keywords as signals. They almost never appear in PM job descriptions.

| Wrong (CV language) | Right (JD language) |
|---|---|
| `hadoop\|kafka\|airflow` | `data platform\|plateforme de données` |
| `mlops\|kubeflow` | `cycle de vie des modèles\|model lifecycle` |
| `gcp\|bigquery` | `infrastructure cloud\|cloud-native` |

To identify the right signals: read the top-scoring JDs from the bootstrap run and look for phrases that appear in those but not in the low-scoring ones.

### Triggering a profile refresh

Delete the relevant file and run in `hybrid` mode:

```bash
rm scoring_profiles/cv1_technical_pm.json
python run.py
```

AJSAA detects the missing profile and runs a full LLM bootstrap pass on the next run.

### Adjusting the uncertainty band

```yaml
scoring:
  uncertainty_band: [65, 82]   # default
```

- **Wider band** (e.g. `[55, 85]`): more jobs go back to LLM — higher accuracy, higher token cost
- **Narrower band** (e.g. `[70, 75]`): only near-threshold jobs get a second opinion — fewer LLM calls

After a profile change, widen the band temporarily to let the LLM re-calibrate borderline cases, then narrow it back once the profile is stable.
