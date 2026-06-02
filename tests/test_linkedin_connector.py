"""Tests for providers/search/connectors/linkedin.py.

All tests are unit-level — no network calls, no linkedin-api import, no MCP server.
The linkedin-api and mcp packages are guarded behind lazy imports in the connector,
so these tests run cleanly even when the packages are installed but creds are absent.
"""
from unittest.mock import MagicMock, patch

from providers.search.connectors.linkedin import LinkedInConnector

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_connector(email: str = "user@example.com", password: str = "secret") -> LinkedInConnector:
    """Return a connector with fake credentials and suppressed MCP cmd."""
    c = LinkedInConnector({})
    c.email = email
    c.password = password
    return c


def _voyager_item(
    title: str = "Product Manager",
    urn: str = "urn:li:fsd_jobPosting:123456789",
    location: str = "Paris, France",
    company_name: str = "Acme Corp",
) -> dict:
    """Build a minimal voyager API response item."""
    return {
        "title": title,
        "entityUrn": urn,
        "formattedLocation": location,
        "companyDetails": {
            "com.linkedin.voyager.dash.jobs.UnboundedFollowingCompany": {
                "companyResolutionResult": {"name": company_name},
            }
        },
        "description": {"text": "Great role, apply now."},
    }


# ── Missing credentials ───────────────────────────────────────────────────────

class TestMissingCredentials:
    def test_no_email_returns_empty(self):
        c = _make_connector(email="", password="secret")
        assert c.search("PM Paris") == []

    def test_no_password_returns_empty(self):
        c = _make_connector(email="user@example.com", password="")
        assert c.search("PM Paris") == []

    def test_both_missing_returns_empty(self):
        c = _make_connector(email="", password="")
        assert c.search("PM Paris") == []


# ── Recency suffix stripping ──────────────────────────────────────────────────

class TestRecencySuffix:
    def test_strips_last_n_days(self):
        c = _make_connector()
        captured = {}

        def fake_primary(q, n):
            captured["query"] = q
            return []

        c._search_primary = fake_primary
        c._search_mcp = lambda q, n: []
        c.search("Product Manager Paris last 3 days", max_results=5)
        assert captured["query"] == "Product Manager Paris"

    def test_no_suffix_unchanged(self):
        c = _make_connector()
        captured = {}

        def fake_primary(q, n):
            captured["query"] = q
            return []

        c._search_primary = fake_primary
        c._search_mcp = lambda q, n: []
        c.search("Product Manager Paris", max_results=5)
        assert captured["query"] == "Product Manager Paris"


# ── Primary path ─────────────────────────────────────────────────────────────

class TestPrimaryPath:
    def test_success_returns_mapped_jobs(self):
        c = _make_connector()
        mock_client = MagicMock()
        mock_client.search_jobs.return_value = [_voyager_item()]
        c._client = mock_client

        with patch("providers.search.connectors.linkedin.Linkedin", return_value=mock_client, create=True):
            # _client already set; _search_primary won't re-init
            results = c._search_primary("Product Manager Paris", 5)

        assert len(results) == 1
        job = results[0]
        assert job["title"] == "Product Manager"
        assert job["company"] == "Acme Corp"
        assert job["location"] == "Paris, France"
        assert job["url"] == "https://www.linkedin.com/jobs/view/123456789/"
        assert job["source"] == "linkedin"
        assert job["status"] == "new"
        assert len(job["job_id"]) == 16

    def test_fallback_triggered_on_primary_exception(self):
        c = _make_connector()
        fallback_result = [{"title": "Fallback Job", "url": "https://example.com"}]

        def raise_on_primary(q, n):
            raise ConnectionError("LinkedIn down")

        c._search_primary = raise_on_primary
        c._search_mcp = lambda q, n: fallback_result

        results = c.search("PM Paris")
        assert results == fallback_result

    def test_empty_title_item_skipped(self):
        c = _make_connector()
        item = _voyager_item(title="")
        assert c._map_primary_result(item) is None

    def test_missing_urn_yields_empty_url(self):
        item = _voyager_item()
        item["entityUrn"] = ""
        c = _make_connector()
        result = c._map_primary_result(item)
        assert result is not None
        assert result["url"] == ""


# ── _map_primary_result field extraction ─────────────────────────────────────

class TestMapPrimaryResult:
    def test_extracts_all_canonical_fields(self):
        c = _make_connector()
        result = c._map_primary_result(_voyager_item())
        assert result is not None
        for field in ("job_id", "title", "company", "location", "url", "description", "source", "date_found", "status"):
            assert field in result

    def test_description_as_plain_string(self):
        item = _voyager_item()
        item["description"] = "Plain text description"
        c = _make_connector()
        result = c._map_primary_result(item)
        assert result["description"] == "Plain text description"

    def test_description_capped_at_1000_chars(self):
        item = _voyager_item()
        item["description"] = {"text": "x" * 2000}
        c = _make_connector()
        result = c._map_primary_result(item)
        assert len(result["description"]) == 1000

    def test_job_id_is_deterministic(self):
        c = _make_connector()
        r1 = c._map_primary_result(_voyager_item())
        r2 = c._map_primary_result(_voyager_item())
        assert r1["job_id"] == r2["job_id"]


# ── MCP fallback path ─────────────────────────────────────────────────────────

class TestMCPFallback:
    def test_mcp_failure_returns_empty(self):
        c = _make_connector()

        async def fail_async(*a, **kw):
            raise RuntimeError("MCP not available")

        c._search_primary = MagicMock(side_effect=RuntimeError("auth error"))
        # Patch the async method so asyncio.run receives a proper coroutine that raises
        with patch.object(c, "_search_mcp_async", fail_async):
            results = c.search("PM Paris")
        assert results == []

    def test_parse_mcp_results_extracts_job_ids(self):
        import json

        c = _make_connector()
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text=json.dumps({"job_ids": ["111", "222", "333"]}))]

        jobs = c._parse_mcp_results(mock_result, max_results=10, query="PM Paris")
        assert len(jobs) == 3
        assert jobs[0]["url"] == "https://www.linkedin.com/jobs/view/111/"
        assert jobs[0]["source"] == "linkedin_mcp"
        assert jobs[0]["status"] == "new"

    def test_parse_mcp_results_respects_max_results(self):
        import json

        c = _make_connector()
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text=json.dumps({"job_ids": ["1", "2", "3", "4", "5"]}))]

        jobs = c._parse_mcp_results(mock_result, max_results=2, query="PM Paris")
        assert len(jobs) == 2

    def test_parse_mcp_results_bad_json_returns_empty(self):
        c = _make_connector()
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text="not valid json {{")]

        jobs = c._parse_mcp_results(mock_result, max_results=5, query="PM Paris")
        assert jobs == []

    def test_parse_mcp_results_empty_content_returns_empty(self):
        c = _make_connector()
        mock_result = MagicMock()
        mock_result.content = []

        jobs = c._parse_mcp_results(mock_result, max_results=5, query="PM Paris")
        assert jobs == []

    def test_both_paths_fail_returns_empty(self):
        # _search_mcp catches its own errors and returns [] — simulate that outcome
        c = _make_connector()
        c._search_primary = MagicMock(side_effect=RuntimeError("primary down"))
        c._search_mcp = MagicMock(return_value=[])
        results = c.search("PM Paris")
        assert results == []
