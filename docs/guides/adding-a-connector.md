# Adding a Search Connector

This guide adds a hypothetical RSS-based job board called `jobsfeed` as a working example.

## 1. Create the connector file

```python
# providers/search/connectors/jobsfeed.py

import hashlib
import logging
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)

_FEED_URL = "https://jobsfeed.example.com/rss"


class JobsFeedConnector(BaseSearchProvider):
    def __init__(self, cfg: dict):
        super().__init__(cfg)          # stores cfg as self.cfg
        self.api_key = os.environ.get("JOBSFEED_API_KEY", "")

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        if not self.api_key:
            logger.warning("JobsFeedConnector: JOBSFEED_API_KEY not set — skipping")
            return []

        # Strip the recency suffix AJSAA appends — use cfg for the date filter
        core_query = query.split(" last ")[0].strip()
        recency_days = self.cfg.get("recency_days", 3)

        try:
            params = urllib.parse.urlencode({
                "q": core_query,
                "days": recency_days,
                "key": self.api_key,
            })
            req = urllib.request.Request(
                f"{_FEED_URL}?{params}",
                headers={"User-Agent": "AJSAA/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                root = ET.fromstring(resp.read())
        except Exception as e:
            logger.error("JobsFeedConnector: request failed for '%s': %s", query, e)
            return []

        jobs = []
        for item in root.findall(".//item")[:max_results]:
            title   = item.findtext("title", "")
            company = item.findtext("company", "")
            url_job = item.findtext("link", "")
            job_id  = hashlib.sha256(
                f"{title}|{company}|{url_job}".lower().encode()
            ).hexdigest()[:16]

            jobs.append({
                "job_id":      job_id,
                "title":       title,
                "company":     company,
                "location":    item.findtext("location", ""),
                "url":         url_job,
                "description": item.findtext("description", "")[:1000],
                "source":      "jobsfeed",
                "date_found":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status":      "new",
            })

        logger.info("JobsFeedConnector: '%s' → %d results", query, len(jobs))
        return jobs
```

Key rules:
- `super().__init__(cfg)` stores `cfg` as `self.cfg` — use `self.cfg.get("recency_days", 3)` for the date filter.
- Strip the AJSAA recency suffix with `query.split(" last ")[0].strip()` — the connector handles date filtering natively.
- Return `[]` (not an exception) when credentials are missing.
- Cap description at 1,000 chars to avoid bloating state.

## 2. Register in the search factory

Open `agent/nodes/search_jobs.py` and add a builder + register it in the
dispatch dict inside `_get_search_provider`:

```python
def _make_jobsfeed(cfg):
    from providers.search.connectors.jobsfeed import JobsFeedConnector
    return JobsFeedConnector(cfg)


def _get_search_provider(name: str, llm, cfg: dict):
    builders = {
        # ...existing entries...
        "jobsfeed": lambda: _make_jobsfeed(cfg),
    }
    ...
```

Keeping the import inside the small `_make_*` helper preserves the lazy-load
pattern — unused connectors never pay for their dependencies.

Optionally add a default concurrency limit:

```python
_DEFAULT_MAX_CONCURRENT = {
    "france_travail": 3,
    "adzuna": 5,
    "anthropic_web": 1,
    "jobsfeed": 2,          # add this line
}
```

## 3. Enable in config

```yaml
search:
  connectors:
    - name: jobsfeed
      enabled: true
      max_results_per_query: 10
      max_concurrent: 2        # optional — overrides _DEFAULT_MAX_CONCURRENT
```

To make it a fallback (only fires when other connectors return nothing):

```yaml
    - name: jobsfeed
      enabled: true
      fallback_only: true
```

## 4. Add credentials to `.env`

```bash
JOBSFEED_API_KEY=your-key
```

## 5. Write tests

Add a test class to `tests/test_search_jobs.py` or a dedicated file. Mock `urllib.request.urlopen` and assert on the returned job dict structure. See `tests/test_search_jobs.py` for examples of how existing connectors are tested via the `_run_parallel` interface.
