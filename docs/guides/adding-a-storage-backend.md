# Adding a Storage Backend

Storage providers implement a two-method abstract interface.

## The interface

```python
# providers/storage/base.py
class BaseStorageProvider(ABC):

    @abstractmethod
    def save(self, jobs: list[dict]) -> int:
        """Persist jobs. Return count of newly added (deduplicated) jobs."""

    @abstractmethod
    def load_all(self) -> list[dict]:
        """Return all stored jobs."""
```

## Example: Notion backend

### 1. Implement the provider

```python
# providers/storage/notion.py

import json
import os
import urllib.request

from providers.storage.base import BaseStorageProvider


class NotionProvider(BaseStorageProvider):
    def __init__(self, cfg: dict):
        self.token       = os.environ["NOTION_TOKEN"]
        self.database_id = cfg["notion_database_id"]
        self._headers    = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    def load_all(self) -> list[dict]:
        url  = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        req  = urllib.request.Request(url, method="POST", headers=self._headers,
                                      data=b"{}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        jobs = []
        for page in data.get("results", []):
            props = page["properties"]
            jobs.append({
                "job_id": props.get("job_id", {}).get("rich_text", [{}])[0]
                                 .get("plain_text", ""),
                # map remaining Notion properties to job dict fields
            })
        return jobs

    def save(self, jobs: list[dict]) -> int:
        existing_ids = {j["job_id"] for j in self.load_all()}
        new_jobs = [j for j in jobs if j["job_id"] not in existing_ids]
        for job in new_jobs:
            payload = json.dumps({
                "parent": {"database_id": self.database_id},
                "properties": {
                    "Title":   {"title": [{"text": {"content": job.get("title", "")}}]},
                    "Company": {"rich_text": [{"text": {"content": job.get("company", "")}}]},
                    "Score":   {"number": job.get("score", 0)},
                    "URL":     {"url": job.get("url", "")},
                    "job_id":  {"rich_text": [{"text": {"content": job["job_id"]}}]},
                },
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.notion.com/v1/pages",
                data=payload,
                method="POST",
                headers=self._headers,
            )
            urllib.request.urlopen(req, timeout=15)
        return len(new_jobs)
```

### 2. Register in the factory

```python
# providers/storage/factory.py
elif provider == "notion":
    from providers.storage.notion import NotionProvider
    return NotionProvider(cfg)
```

### 3. Configure

```yaml
# config.yaml
storage:
  provider: notion
  notion_database_id: "your-database-id-here"
```

```bash
# .env
NOTION_TOKEN=secret_xxxxx
```

## Deduplication contract

`save()` must return the count of **newly added** jobs, not total jobs received. The canonical pattern:

```python
def save(self, jobs: list[dict]) -> int:
    existing_ids = {j["job_id"] for j in self.load_all()}
    new_jobs = [j for j in jobs if j["job_id"] not in existing_ids]
    # ... persist new_jobs ...
    return len(new_jobs)
```

`store_results` uses this count to populate `stored_count` in state and to log the run summary.
