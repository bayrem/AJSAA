# Data Models

---

## AgentState

The single TypedDict that flows through every node. Defined in `agent/state.py`.

```python
class AgentState(TypedDict):
    run_id:            str
    timestamp:         str
    config:            dict          # full config.yaml contents

    # Input layer (set by load_context)
    cvs:               list[dict]    # [{"name", "content", "path"}]
    raw_queries:       list[str]     # lines from query/job_queries.md
    companies:         list[str]     # lines from query/company_list.md
    pdf_paths:         list[str]     # PDF files found in query/resume/

    # Generated
    queries:           list[str]     # final search strings

    # Search results
    raw_jobs:          list[dict]    # all jobs before scoring

    # Analysis
    scored_jobs:       list[dict]    # jobs that passed min_score, sorted desc

    # Output
    stored_count:      int
    sheet_url:         Optional[str]
    notification_sent: bool

    # Audit
    errors:            list[str]     # non-fatal errors accumulated across nodes
    run_log:           list[str]     # human-readable run trace
```

---

## Job record

Every job dict in `raw_jobs` and `scored_jobs` follows this schema. Fields marked *(scored)* are added by `analyze_jobs`.

| Field | Type | Source |
|---|---|---|
| `job_id` | `str` (16-char hex) | connector or SHA-256 hash |
| `title` | `str` | connector |
| `company` | `str` | connector |
| `location` | `str` | connector |
| `url` | `str` | connector |
| `description` | `str` (≤1,000 chars) | connector |
| `source` | `str` | connector (e.g. `france_travail`) |
| `date_found` | `str` (ISO 8601) | connector |
| `status` | `str` | default `"new"` |
| `score` | `int` (0–95) | *(scored)* LLM or static scorer |
| `best_cv` | `str` | *(scored)* name of best-matching CV |
| `summary` | `str` | *(scored)* one-sentence reasoning |
| `recommendation` | `str` | *(scored)* `APPLY` / `CONSIDER` / `SKIP` |

---

## Compressed CV

Produced by `providers/scoring/cv_cache.py`. Used as the LLM scoring context instead of the full CV.

```
YOE: 12 years
Role: Technical Product Manager
Skills: LangGraph, Python, Hadoop, Kafka, GCP
Domain: Data platforms, AI enablement, Internal tools
Metrics: 73% incident reduction, 99.6% SLA, ×3.5 deployment capacity
```

Cached to `.data/cv_cache/{name}_{hash}.txt`. Invalidated automatically when CV content changes.

---

## Scoring profile

Produced by the hybrid scorer's profile extraction step. Stored in `scoring_profiles/{cv_name}.json`.

```json
{
  "cv": "cv1_technical_pm",
  "cv_hash": "d0f092361644b729",
  "positive_signals": [
    {"pattern": "data platform|plateforme de données", "weight": 15},
    {"pattern": "product manager|chef de produit", "weight": 12},
    {"pattern": "roadmap|feuille de route", "weight": 10}
  ],
  "negative_signals": [
    {"pattern": "junior|stagiaire|alternance|alternant", "weight": -50},
    {"pattern": "commercial|vente|sales", "weight": -40}
  ],
  "domain_bonus": {
    "intelligence artificielle en production": 8
  },
  "uncertainty_band": [65, 82]
}
```

The profile is invalidated (and re-bootstrapped on next run) when the CV content hash changes — editing your CV automatically triggers a profile refresh.

---

## Configuration reference

| File | Purpose | Committed |
|---|---|---|
| `config.yaml` | All behavioural settings | Yes |
| `.env` | All secrets | No |
| `query/job_queries.md` | Search strings (one per line) | Yes |
| `query/company_list.md` | Companies to check career pages | Yes |
| `query/resume/` | CV files (.md or .pdf) | No — gitignored |
| `.data/jobs.json` | Job store — source of truth | No |
| `.data/cv_cache/` | Compressed CV cache | No |
| `.data/meta.json` | Last run metadata (sheet URL, timestamp) | No |
| `scoring_profiles/` | Learned static scoring profiles | No |
| `logs/` | Run logs | No |
| `templates/cv_template.md` | CV authoring schema | Yes |

---

## Environment variables

| Variable | Required when | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | `llm.provider: anthropic` | Claude API access |
| `OPENAI_API_KEY` | `llm.provider: openai` | OpenAI API access |
| `FRANCE_TRAVAIL_CLIENT_ID` | connector `france_travail` enabled | OAuth2 client ID |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | connector `france_travail` enabled | OAuth2 client secret |
| `ADZUNA_APP_ID` | connector `adzuna` enabled | API app ID |
| `ADZUNA_APP_KEY` | connector `adzuna` enabled | API app key |
| `TELEGRAM_BOT_TOKEN` | channel `telegram` enabled | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | channel `telegram` enabled | Target chat ID |
| `SLACK_WEBHOOK_URL` | channel `slack` enabled | Incoming webhook URL |
| `EMAIL_FROM` | channel `email` enabled | Sender address |
| `EMAIL_TO` | channel `email` enabled | Recipient address |
| `EMAIL_SMTP_HOST` | channel `email` enabled | SMTP host (default: smtp.gmail.com) |
| `EMAIL_SMTP_PORT` | channel `email` enabled | SMTP port (default: 587) |
| `EMAIL_PASSWORD` | channel `email` enabled | App password |
