# Architecture Overview

AJSAA is a LangGraph pipeline. A single typed state object flows through eight nodes in sequence, with conditional edges that skip nodes when their preconditions are not met.

## Pipeline

```
run.py
  └─ build_graph().invoke(initial_state)
        │
        ▼
  load_context          Read CVs, queries, company list from disk
        │
        ├─ (no PDFs found) ──────────────────────────────┐
        ▼                                                 │
  convert_cvs           Extract text from PDFs            │
        │ ◄──────────────────────────────────────────────┘
        │
        ├─ (job_queries.md exists) ──────────────────────┐
        ▼                                                 │
  generate_queries      LLM generates search strings      │
        │ ◄──────────────────────────────────────────────┘
        │
        ▼
  search_jobs           Query all enabled connectors in parallel
        │
        ▼
  search_companies      LLM web-searches company career pages
        │
        ▼
  analyze_jobs          Compress CVs → score jobs → filter
        │
        ▼
  store_results         Deduplicate and persist
        │
        ├─ (notifications disabled) ─────────────────────┐
        ▼                                                 │
  send_notifications    Format and deliver digest         │
        │ ◄──────────────────────────────────────────────┘
        ▼
       END
```

## State flow

A single `AgentState` TypedDict is passed from node to node. No node calls another directly — all communication is through state. Each node returns `{**state, "key": new_value}`, making every node a pure function that is independently testable.

The state has four logical layers:

| Layer | Keys | Set by |
|---|---|---|
| Input | `cvs`, `raw_queries`, `companies`, `pdf_paths` | `load_context` |
| Generated | `queries` | `generate_queries` |
| Pipeline | `raw_jobs`, `scored_jobs` | `search_*`, `analyze_jobs` |
| Output | `stored_count`, `sheet_url`, `notification_sent` | `store_results`, `send_notifications` |
| Audit | `errors`, `run_log` | every node |

## Conditional routing

Three conditional edges make the pipeline adaptive:

**`_needs_convert_cvs`**
Skips `convert_cvs` if no PDF files are queued. On most runs, CVs are already `.md` files and this node is bypassed entirely.

**`_needs_generate_queries`**
Skips LLM query generation if `query/job_queries.md` exists. Users who maintain that file save ~1,200 tokens per run and get deterministic search strings.

**`_needs_notifications`**
Skips `send_notifications` if no channels are configured or `notifications.enabled` is `false`.

## Provider layer

Every major concern follows the same factory pattern. Swapping any component requires only a config change — no node code changes:

```
providers/<concern>/base.py       abstract interface
providers/<concern>/<impl>.py     concrete implementation
providers/<concern>/factory.py    build_<concern>(cfg) dispatcher
```

The four provider domains:

| Domain | Interface | Implementations |
|---|---|---|
| LLM | `BaseChatModel` (LangChain) | `anthropic`, `openai`, `claude_code_agent` |
| Search | `BaseJobBoardConnector` | `france_travail`, `adzuna`, `anthropic_web`, … |
| Storage | `BaseStorageProvider` | `local`, `google_drive`, `onedrive`, `dropbox` |
| Notifications | `BaseNotifier` | `telegram`, `email`, `slack`, `whatsapp` |

## File structure

```
AJSAA/
├── agent/
│   ├── graph.py              LangGraph pipeline definition
│   ├── state.py              AgentState TypedDict
│   └── nodes/                One file per pipeline node
├── providers/
│   ├── llm/                  LLM backends
│   ├── scoring/              Scorer implementations
│   ├── search/               Job board connectors
│   ├── storage/              Persistence backends
│   └── notifications/        Notification channels
├── query/
│   ├── job_queries.md        Search strings (one per line)
│   ├── company_list.md       Companies to check career pages
│   └── resume/               CV files — gitignored
├── templates/
│   └── cv_template.md        Canonical CV schema
├── docs/                     This documentation
├── tests/                    pytest suite
├── config.yaml               Behavioural config
└── run.py                    Entry point
```
