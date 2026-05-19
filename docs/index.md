# AJSAA — Autonomous Job Search AI Agent

AJSAA is a daily job search pipeline that runs on a schedule, searches multiple job boards, scores every result against your CV profiles, and delivers a ranked shortlist to your inbox or Telegram.

## What it does

1. Reads your CV(s) and search preferences from the `query/` directory
2. Queries France Travail, Adzuna, and other connectors in parallel
3. Scores every job against your CV in a single LLM call
4. Stores new matches and sends a digest with the top results

A typical run takes under 2 minutes and costs ~14,000 tokens when real API connectors are configured.

## Documentation

### Architecture
- [Overview](architecture/overview.md) — how the pipeline works end to end
- [Node Contracts](architecture/node-contracts.md) — what each step reads, writes, and guarantees
- [Design Decisions](architecture/design-decisions.md) — the why behind every major choice
- [Data Models](architecture/data-models.md) — AgentState, job schema
- [Scoring](architecture/scoring.md) — one-shot LLM scoring explained

### Guides
- [Getting Started](guides/getting-started.md) — installation, config, first run
- [Adding a Connector](guides/adding-a-connector.md) — plug in a new job board
- [Adding a Storage Backend](guides/adding-a-storage-backend.md) — persist results elsewhere
- [Adding a Notification Channel](guides/adding-a-notification-channel.md) — new delivery channel
- [Running in CI](guides/running-in-ci.md) — GitHub Actions / headless mode
- [Prompt Engineering](guides/prompt-engineering.md) — tuning scoring for your market

## Quick start

```bash
cp .env.example .env          # fill in API keys
python run.py                 # first run
python run.py --dry-run       # score jobs without writing to storage
```

Place your CV in `query/resume/` as a `.md` or `.pdf` file. See [Getting Started](guides/getting-started.md) for the full setup.
