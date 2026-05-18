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

**2. Configure your search**

Edit `config/search_config.yaml`. Set your target positions (max 2 per CV), locations, and the companies you want to monitor:

```yaml
# config/search_config.yaml
cvs:
  cv1:
    - "Senior Product Manager"
    - "Head of Product"

locations:
  - "Paris"
  - "Remote"

companies:
  - "Mistral AI"
  - name: "Hugging Face"
    hint: "greenhouse:huggingface"   # skip LLM — use ATS hint directly
```

AJSAA builds a cross-product of positions × locations and writes `query/job_queries.md` automatically — no manual editing needed.

**3. Configure the LLM provider**

The default uses the Claude CLI (no API key needed if you have Claude Pro):

```yaml
# config/config.yaml
llm:
  provider: claude_code_agent
```

To use the Anthropic API directly, set `provider: anthropic` and add `ANTHROPIC_API_KEY` to your Infisical secrets.

**4. Configure notifications**

Telegram is the simplest channel to set up. Get a bot token from [@BotFather](https://t.me/botfather) and find your chat ID, then add them to Infisical (env: dev).

```yaml
# config/config.yaml
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

Add companies to the `companies:` block in `config/search_config.yaml`. Three shapes are supported:

```yaml
companies:
  - "Doctolib"                              # LLM discovers ATS on first run, result cached
  - name: "Dataiku"
    hint: "greenhouse:dataiku"              # skips LLM — known ATS hint
  - name: "Contentsquare"
    url: "https://jobs.lever.co/contentsquare"  # skips LLM — direct URL
```

User-provided hints and URLs always override the cache. `query/company_list.md` is deprecated — if it still exists, it is ignored.

## Scheduling daily runs

```bash
# crontab -e
0 7 * * 1-5 cd /path/to/AJSAA && .venv/bin/python run.py >> logs/cron.log 2>&1
```

For GitHub Actions scheduling, see [Running in CI](running-in-ci.md).
