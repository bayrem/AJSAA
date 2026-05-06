# AJSAA — Project Context for Claude Code

## What This Is
Autonomous Job Search AI Agent. A LangGraph-based agent that searches for job postings, scores them against CV profiles, stores results locally, and optionally syncs to Google Drive via MCP.

## Architecture

```
run.py
  │
  └── agent/graph.py  (LangGraph StateGraph)
        │
        ├── load_context      → reads query/resume/*.md, query/job_queries.md, query/company_list.md
        ├── convert_cvs       → PDF → MD (only if PDFs present)
        ├── generate_queries  → LLM generates queries from CVs (only if no job_queries.md)
        ├── search_jobs       → web search + job board connectors
        ├── search_companies  → career page search per listed company
        ├── analyze_jobs      → LLM scores each job vs. all CVs
        ├── store_results     → writes to .data/jobs.json + .data/meta.json + cloud sync
        └── send_notifications → email / Slack / Telegram
```

## Running

**Pipeline only:**
```bash
.venv/bin/python run.py
```

**Dry-run:**
```bash
.venv/bin/python run.py --dry-run
```

**Full agent run (pipeline + Google Drive sync via MCP):**
After pipeline completes, sync .data/jobs.json to Google Drive as a spreadsheet:
1. Run `.venv/bin/python run.py`
2. Read `.data/jobs.json`
3. Convert to CSV format
4. Use Google Drive MCP `create_file` to upload as a spreadsheet named "AJSAA Job Search"
5. Sheet URL is persisted to `.data/meta.json` automatically

## Google Drive Sync (MCP)

When invoked in this Claude Code session:
1. Run the pipeline: `.venv/bin/python run.py`
2. Load jobs: read `.data/jobs.json`
3. Build CSV with headers: date_found, job_id, title, company, location, url, best_cv, score, summary, status
4. Search Drive for existing "AJSAA Job Search" sheet (use `search_files`)
5. Create/replace the spreadsheet using `create_file` with the CSV content

## Key Files

| File | Purpose |
|---|---|
| `config.yaml` | All behavioural config — safe to commit |
| `.env` | All secrets — never commit |
| `query/job_queries.md` | Search queries (one per line) |
| `query/company_list.md` | Companies to check career pages |
| `query/resume/` | CV files (PDF or MD) — gitignored |
| `.data/jobs.json` | Local job store — source of truth — gitignored |
| `.data/meta.json` | Sheet URL + last run metadata — gitignored |
| `.docs/roadmap.md` | Working roadmap — gitignored, never commit |

## Provider Extension Points

All providers follow a factory pattern. To swap any component:
- **LLM**: subclass `providers/llm/base.py`, register in `providers/llm/factory.py`, set `llm.provider` in config
- **Search**: subclass `providers/search/connectors/base.py`, register in `providers/search/factory.py`
- **Storage**: subclass `providers/storage/base.py`, register in `providers/storage/factory.py`
- **Notifications**: subclass `providers/notifications/base.py`, register in `providers/notifications/factory.py`
