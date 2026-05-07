# Getting Started

## Prerequisites

- Python 3.11+
- A Claude Pro subscription (for the default `claude_code_agent` provider), **or** an Anthropic API key

## Installation

```bash
git clone https://github.com/bayrem/AJSAA.git
cd AJSAA
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Minimum configuration

**1. Add your CV**

Place your CV in `query/resume/` as a `.md` file. Use `templates/cv_template.md` as a starting point. PDF files are also accepted — they are converted to `.md` on first run.

```
query/resume/cv_technical_pm.md
```

**2. Set your search queries**

Edit `query/job_queries.md`. One query per line, `#` for comments:

```
# query/job_queries.md
Technical Product Manager Paris
Data Product Manager remote France
AI Product Manager plateforme données
```

If this file is absent, AJSAA generates queries from your CV automatically.

**3. Configure the LLM provider**

The default uses the Claude CLI (no API key needed if you have Claude Pro):

```yaml
# config.yaml
llm:
  provider: claude_code_agent
```

To use the Anthropic API directly, set `provider: anthropic` and add `ANTHROPIC_API_KEY` to `.env`.

**4. Configure notifications**

Telegram is the simplest channel to set up. Get a bot token from [@BotFather](https://t.me/botfather) and find your chat ID:

```bash
# In .env
TELEGRAM_BOT_TOKEN=your-token
TELEGRAM_CHAT_ID=your-chat-id
```

```yaml
# config.yaml
notifications:
  enabled: true
  channels: [telegram]
```

## First run

```bash
python run.py --dry-run   # score jobs without writing to storage (safe to test)
python run.py             # full run
```

Logs are written to `logs/job_search.log`. Scored jobs are stored in `.data/jobs.json`.

## Optional: real job board connectors

Without API credentials, AJSAA falls back to LLM web search (~40,000 tokens/run). Registering free connectors drops this to ~14,000 tokens.

**France Travail** (official French government job API — free):
Register at [francetravail.io/data/api](https://francetravail.io/data/api)

```bash
# In .env
FRANCE_TRAVAIL_CLIENT_ID=your-id
FRANCE_TRAVAIL_CLIENT_SECRET=your-secret
```

**Adzuna** (global aggregator, strong France coverage — free):
Register at [developer.adzuna.com](https://developer.adzuna.com)

```bash
# In .env
ADZUNA_APP_ID=your-id
ADZUNA_APP_KEY=your-key
```

## Optional: company career page search

Add company names to `query/company_list.md`. AJSAA will search their career pages via LLM web search on each run:

```
# query/company_list.md
Doctolib
Dataiku
Contentsquare
```

## Scheduling daily runs

```bash
# crontab -e
0 7 * * 1-5 cd /path/to/AJSAA && .venv/bin/python run.py >> logs/cron.log 2>&1
```

For GitHub Actions scheduling, see [Running in CI](running-in-ci.md).
