# AJSAA — Autonomous Job Search AI Agent

A LangGraph-based agent that autonomously discovers, scores, and tracks job opportunities against your CV profiles — and notifies you of the best matches.

---

## What it does

1. **Loads context** — reads your CV files (`query/resume/`) and search queries (`query/job_queries.md`)
2. **Searches for jobs** — runs queries against real job board APIs (France Travail, Adzuna) with an LLM fallback
3. **Scores matches** — batch-scores each posting against your CVs using an LLM; keeps only jobs above a configurable threshold
4. **Stores results** — deduplicates by content-hash and writes to local JSON and/or cloud storage (Google Drive, OneDrive, Dropbox)
5. **Notifies you** — sends a digest to Telegram, Slack, email, or WhatsApp

## Architecture

```
run.py
  └── LangGraph Agent (agent/graph.py)
        ├── load_context      reads CVs, queries, and company list
        ├── convert_cvs       PDF → Markdown (if PDFs present)
        ├── generate_queries  LLM generates queries from CVs (if no file)
        ├── search_jobs       job board APIs + LLM fallback
        ├── search_companies  career page search per listed company
        ├── analyze_jobs      LLM scores jobs vs. CVs; keeps score ≥ threshold
        ├── store_results     writes .data/jobs.json + optional cloud sync
        └── send_notifications  email / Slack / Telegram / WhatsApp
```

Every provider is swappable via a single line in `config.yaml` — LLM, search connectors, storage backend, and notification channels all follow the same factory pattern.

## Quick start

```bash
# 1. Install
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Configure secrets
cp .env.template .env
# Edit .env — set ANTHROPIC_API_KEY at minimum

# 3. Add your CV
# Drop a PDF or .md file into query/resume/

# 4. Run
.venv/bin/python run.py

# Dry-run (scores jobs without writing to storage)
.venv/bin/python run.py --dry-run
```

## Configuration

All behaviour lives in `config.yaml` — no code changes needed to swap providers:

```yaml
llm:
  provider: claude_code_agent   # anthropic | openai | claude_code_agent

search:
  connectors:
    - name: france_travail       # free API — francetravail.io
    - name: adzuna               # free API — developer.adzuna.com
    - name: anthropic_web        # LLM fallback when real connectors return nothing
      fallback_only: true

scoring:
  min_score: 70                  # jobs below this are discarded (0–95 scale)

storage:
  provider: local                # local | google_drive | onedrive | dropbox

notifications:
  channels: [telegram]           # email | slack | telegram | whatsapp
```

## Tech stack

| Concern | Default |
|---|---|
| Orchestration | LangGraph |
| LLM interface | LangChain (Anthropic Claude / OpenAI) |
| Job boards | France Travail API, Adzuna API |
| Storage | Local JSON (Google Drive / OneDrive / Dropbox) |
| Notifications | Telegram (email / Slack / WhatsApp) |
