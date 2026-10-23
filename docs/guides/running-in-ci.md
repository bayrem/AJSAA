# Running in CI / Headless Mode

The default `claude_agent_sdk` provider bills your Claude subscription through a logged-in `claude` session, which a CI runner doesn't have. In GitHub Actions or any headless environment, keep the same provider and switch it to API billing. The Agent SDK ships its own Claude Code binary, so nothing else needs installing.

## Config change

The only required change is `llm.billing`:

```yaml
# config.yaml (or a config.ci.yaml passed via --config flag)
llm:
  provider: claude_agent_sdk
  billing: api                   # bills the Anthropic API via AJSAA_ANTHROPIC_API_KEY
  scoring_model: claude-sonnet-4-6
  search_model: claude-haiku-4-5-20251001
  default_model: claude-haiku-4-5-20251001
```

Don't use `provider: anthropic` for full runs: the plain LangChain provider has no web search tool, so the `anthropic_web` connector would return nothing.

## GitHub Actions workflow

```yaml
# .github/workflows/daily-search.yml

name: Daily Job Search

on:
  schedule:
    - cron: "0 7 * * 1-5"    # weekdays at 07:00 UTC
  workflow_dispatch:           # allow manual trigger

jobs:
  search:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run AJSAA
        env:
          AJSAA_ANTHROPIC_API_KEY:       ${{ secrets.AJSAA_ANTHROPIC_API_KEY }}
          FRANCE_TRAVAIL_CLIENT_ID:      ${{ secrets.FRANCE_TRAVAIL_CLIENT_ID }}
          FRANCE_TRAVAIL_CLIENT_SECRET:  ${{ secrets.FRANCE_TRAVAIL_CLIENT_SECRET }}
          ADZUNA_APP_ID:                 ${{ secrets.ADZUNA_APP_ID }}
          ADZUNA_APP_KEY:                ${{ secrets.ADZUNA_APP_KEY }}
          TELEGRAM_BOT_TOKEN:            ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:              ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python run.py
```

## Required secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `AJSAA_ANTHROPIC_API_KEY` | Your Anthropic API key (non-default name: see design decisions) |
| `FRANCE_TRAVAIL_CLIENT_ID` | France Travail OAuth2 client ID |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | France Travail OAuth2 client secret |
| `ADZUNA_APP_ID` | Adzuna app ID |
| `ADZUNA_APP_KEY` | Adzuna app key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

## CV confidentiality in CI

`query/resume/` is gitignored — CVs are never committed. In CI, you have two options:

**Option A — Base64 secret (simple, for small CVs)**

```bash
# Encode locally
base64 -w0 query/resume/cv_technical_pm.md
```

Add the output as a secret `CV_B64`, then decode in the workflow:

```yaml
- name: Restore CV
  run: |
    mkdir -p query/resume
    echo "${{ secrets.CV_B64 }}" | base64 -d > query/resume/cv_technical_pm.md
```

**Option B — Private cloud storage (recommended for multiple CVs)**

Store CVs in a private S3 bucket, Google Drive folder, or Dropbox, and download them in a workflow step before running AJSAA.

## Persisting job history across runs

`.data/jobs.json` is the deduplication source of truth. Without persistence, every CI run re-stores all jobs. Options:

- Commit `.data/jobs.json` to a private branch (simplest)
- Use the `google_drive` storage provider — the file lives in Drive and is fetched on each run
- Upload/download from S3 as a workflow artifact
