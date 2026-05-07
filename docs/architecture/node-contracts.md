# Node Contracts

Each node is a pure function: it receives `AgentState`, returns an updated `AgentState`. This page documents what each node reads, writes, and guarantees on exit.

---

## `load_context`

**Reads:** `config`, initial empty state

**Writes:** `cvs`, `raw_queries`, `companies`, `pdf_paths`, `errors`, `run_log`

**Guarantees:**
- `cvs` is a list of `{"name": str, "content": str, "path": str}` dicts, capped at `scoring.max_cvs` (default 5), sorted by filename.
- `raw_queries` contains only non-empty, non-comment lines from `query/job_queries.md`. Empty list if the file does not exist.
- `companies` contains only non-empty, non-comment lines from `query/company_list.md`. Empty list if the file does not exist.
- `pdf_paths` lists all `.pdf` files found in `query/resume/`.
- If neither CVs nor PDFs are found, an error is appended but the run continues — downstream nodes handle empty input gracefully.

---

## `convert_cvs`

**Reads:** `pdf_paths`, `cvs`, `config`

**Writes:** `cvs` (extended), `errors`, `run_log`

**Guarantees:**
- Each successfully converted PDF produces a `.md` file alongside the original and appends an entry to `cvs`.
- Conversion failures are non-fatal: the error is logged and that PDF is skipped.
- The `max_cvs` cap is re-applied after conversion.
- This node only runs when `pdf_paths` is non-empty (see conditional routing in [Overview](overview.md)).

---

## `generate_queries`

**Reads:** `raw_queries`, `cvs`, `config`

**Writes:** `queries`, `errors`, `run_log`

**Guarantees:**
- If `raw_queries` is non-empty, `queries` is set directly from `raw_queries` — no LLM call is made.
- If LLM generation fails, `queries` is `[]` and an error is appended. The pipeline continues; `search_jobs` logs a skip.
- `queries` contains only non-empty strings.

---

## `search_jobs`

**Reads:** `queries`, `config`, `errors`, `run_log`, `raw_jobs`

**Writes:** `raw_jobs` (extended and deduplicated), `errors`, `run_log`

**Guarantees:**
- All enabled, non-`fallback_only` connectors run in parallel before any fallback connector is consulted.
- `fallback_only` connectors only activate when the primary pass returned zero results.
- Every job in `raw_jobs` has a `job_id` — set by the connector or derived as a 16-char SHA-256 of `title|company|location`.
- Stale jobs (matching phrases like "posted last month", "il y a 2 mois", "30+ days ago") are filtered before the node exits.
- Deduplication by `job_id` runs within this node.
- A connector failure logs an error and is skipped — it does not abort the run.

---

## `search_companies`

**Reads:** `companies`, `cvs`, `config`, `raw_jobs`

**Writes:** `raw_jobs` (extended), `errors`, `run_log`

**Guarantees:**
- No-op if `companies` is empty or `search.enable_company_pages` is `false`.
- Each company is searched sequentially via `AnthropicWebSearchProvider`.
- A failed company search is non-fatal.

---

## `analyze_jobs`

**Reads:** `raw_jobs`, `cvs`, `config`

**Writes:** `scored_jobs`, `errors`, `run_log`

**Guarantees:**
- `scored_jobs` is sorted descending by `score` on exit.
- Every entry in `scored_jobs` has: `job_id`, `title`, `company`, `location`, `score` (int, 0–95), `best_cv`, `summary`, `recommendation`.
- Only jobs with `score >= scoring.min_score` (default 70) appear in `scored_jobs`.
- Scores are capped at `scoring.max_score` (default 95).
- CV compression is read from disk cache first — unchanged CVs are not re-compressed.
- Scoring mode is driven by `scoring.mode`: `llm`, `static`, or `hybrid`.

---

## `store_results`

**Reads:** `scored_jobs`, `config`

**Writes:** `stored_count`, `sheet_url`, `errors`, `run_log`

**Guarantees:**
- `stored_count` reflects only newly added jobs — deduplication runs inside the storage provider.
- `sheet_url` is populated when the storage provider exposes one (e.g. Google Drive). If the current run produces no URL, the last known URL is loaded from `.data/meta.json`.
- Storage failures are non-fatal — the run completes and notifications are still sent.

---

## `send_notifications`

**Reads:** `scored_jobs`, `stored_count`, `sheet_url`, `errors`, `config`

**Writes:** `notification_sent`, `errors`, `run_log`

**Guarantees:**
- Each configured channel is attempted independently — a failed Telegram send does not block an email send.
- `notification_sent` is `true` if at least one channel succeeded.
- The digest always shows the top 5 scoring jobs, regardless of total stored count.
- This node only runs when notifications are enabled (see conditional routing in [Overview](overview.md)).
