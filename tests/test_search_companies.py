"""Tests for agent/nodes/search_companies and agent/nodes/load_context company parsing."""
from unittest.mock import MagicMock, patch

from agent.nodes.load_context import _parse_companies
from agent.nodes.search_companies import _compute_companies_hash

# ── _parse_companies ──────────────────────────────────────────────────────────

class TestParseCompanies:
    def test_plain_string_no_inline_hint(self):
        names, hints = _parse_companies(["Mistral AI"])
        assert names == ["Mistral AI"]
        assert hints == {}

    def test_hint_entry_produces_inline_hint(self):
        names, hints = _parse_companies([
            {"name": "Hugging Face", "hint": "greenhouse:huggingface"}
        ])
        assert names == ["Hugging Face"]
        assert hints == {"Hugging Face": "greenhouse:huggingface"}

    def test_url_entry_adds_url_prefix(self):
        names, hints = _parse_companies([
            {"name": "Criteo", "url": "https://jobs.lever.co/criteo"}
        ])
        assert names == ["Criteo"]
        assert hints == {"Criteo": "url:https://jobs.lever.co/criteo"}

    def test_url_entry_already_prefixed_not_double_prefixed(self):
        """If someone writes url: url:https://..., we don't double-prefix."""
        names, hints = _parse_companies([
            {"name": "Acme", "url": "url:https://acme.jobs/"}
        ])
        assert hints["Acme"] == "url:https://acme.jobs/"

    def test_mixed_shapes_all_parsed(self):
        raw = [
            "Mistral AI",
            {"name": "Hugging Face", "hint": "greenhouse:huggingface"},
            {"name": "Criteo", "url": "https://jobs.lever.co/criteo"},
        ]
        names, hints = _parse_companies(raw)
        assert names == ["Mistral AI", "Hugging Face", "Criteo"]
        assert "Mistral AI" not in hints
        assert hints["Hugging Face"] == "greenhouse:huggingface"
        assert hints["Criteo"] == "url:https://jobs.lever.co/criteo"

    def test_dict_entry_missing_name_is_skipped(self):
        names, hints = _parse_companies([{"hint": "greenhouse:orphan"}])
        assert names == []
        assert hints == {}

    def test_non_string_non_dict_entry_is_skipped(self):
        names, hints = _parse_companies([42, None])
        assert names == []


# ── _compute_companies_hash ───────────────────────────────────────────────────

class TestComputeCompaniesHash:
    def test_deterministic(self):
        assert _compute_companies_hash(["A", "B"]) == _compute_companies_hash(["A", "B"])

    def test_order_independent(self):
        assert _compute_companies_hash(["B", "A"]) == _compute_companies_hash(["A", "B"])

    def test_different_list_different_hash(self):
        assert _compute_companies_hash(["A"]) != _compute_companies_hash(["B"])

    def test_returns_16_hex_chars(self):
        h = _compute_companies_hash(["Acme"])
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ── search_companies.run — user hint overrides cache ─────────────────────────

def _make_state(companies: list, company_hints: dict) -> dict:
    return {
        "companies": companies,
        "company_hints": company_hints,
        "cvs": [],
        "raw_jobs": [],
        "errors": [],
        "run_log": [],
        "config": {
            "search": {"enable_company_pages": True, "recency_days": 3},
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        },
    }


class TestSearchCompaniesRun:
    """Behavioural tests for search_companies.run using mocked IO."""

    def _mock_load_raw_cache(self, stored_hints: dict):
        """Return a mock that supplies stored hints plus a matching hash."""
        companies = list(stored_hints.keys())
        from agent.nodes.search_companies import _compute_companies_hash
        h = _compute_companies_hash(companies)
        data = {"_companies_hash": h, **stored_hints}
        return MagicMock(return_value=data)

    def test_user_hint_overrides_cache(self):
        """User-provided hint from YAML must be used even if cache has a different value."""
        from agent.nodes import search_companies

        user_hint = "greenhouse:huggingface"
        cached_hint = "url:https://old.hf.co/jobs"

        # load_context has already merged user hint into company_hints
        state = _make_state(
            companies=["Hugging Face"],
            company_hints={"Hugging Face": user_hint},
        )

        search_results = [{"title": "ML Engineer", "company": "Hugging Face"}]

        with (
            patch.object(search_companies, "_load_raw_cache", return_value={
                "_companies_hash": "",
                "Hugging Face": cached_hint,
            }),
            patch.object(search_companies, "_save_hint"),
            patch.object(search_companies, "_update_companies_hash"),
            patch.object(search_companies, "_search_with_hint", return_value=search_results) as mock_search,
            patch("providers.llm.factory.build_llm", return_value=MagicMock()),
        ):
            result = search_companies.run(state)

        # Must have been called with the user-provided hint, not the cached one
        mock_search.assert_called_once()
        _, used_hint, *_ = mock_search.call_args[0]
        assert used_hint == user_hint
        assert result["raw_jobs"] == search_results

    def test_url_hint_skips_llm_discovery(self):
        """A company with a url: hint must never call _discover_url."""
        from agent.nodes import search_companies

        state = _make_state(
            companies=["Criteo"],
            company_hints={"Criteo": "url:https://jobs.lever.co/criteo"},
        )

        with (
            patch.object(search_companies, "_load_raw_cache", return_value={
                "_companies_hash": _compute_companies_hash(["Criteo"]),
            }),
            patch.object(search_companies, "_update_companies_hash"),
            patch.object(search_companies, "_search_with_hint", return_value=[]) as mock_search,
            patch.object(search_companies, "_discover_url") as mock_discover,
            patch("providers.llm.factory.build_llm", return_value=MagicMock()),
        ):
            search_companies.run(state)

        mock_discover.assert_not_called()
        mock_search.assert_called_once()
        call_args = mock_search.call_args[0]
        assert call_args[0] == "Criteo"
        assert call_args[1] == "url:https://jobs.lever.co/criteo"

    def test_hash_match_skips_llm_discovery_for_cached_company(self):
        """When the hash matches and a company is already in the cache, skip LLM."""
        from agent.nodes import search_companies

        companies = ["Dataiku"]
        current_hash = _compute_companies_hash(companies)

        state = _make_state(companies=companies, company_hints={})

        with (
            patch.object(search_companies, "_load_raw_cache", return_value={
                "_companies_hash": current_hash,
                "Dataiku": "greenhouse:dataiku",
            }),
            patch.object(search_companies, "_update_companies_hash"),
            patch.object(search_companies, "_search_with_hint", return_value=[]),
            patch.object(search_companies, "_discover_url") as mock_discover,
            patch("providers.llm.factory.build_llm", return_value=MagicMock()),
        ):
            result = search_companies.run(state)

        mock_discover.assert_not_called()
        assert "greenhouse:dataiku" in str(result["run_log"])

    def test_hash_mismatch_triggers_llm_discovery_for_unknown_company(self):
        """When the hash doesn't match, companies with no cached hint call LLM."""
        from agent.nodes import search_companies

        companies = ["New Company"]
        state = _make_state(companies=companies, company_hints={})

        with (
            patch.object(search_companies, "_load_raw_cache", return_value={
                "_companies_hash": "stale_hash_xxxx",
            }),
            patch.object(search_companies, "_save_hint"),
            patch.object(search_companies, "_update_companies_hash"),
            patch.object(search_companies, "_search_with_hint", return_value=[]),
            patch.object(search_companies, "_discover_url", return_value="none") as mock_discover,
            patch("providers.llm.factory.build_llm", return_value=MagicMock()),
        ):
            search_companies.run(state)

        mock_discover.assert_called_once()
        assert mock_discover.call_args[0][0] == "New Company"

    def test_hint_none_company_is_skipped(self):
        """Companies with hint=none produce no jobs and no errors."""
        from agent.nodes import search_companies

        state = _make_state(
            companies=["Doctrine"],
            company_hints={"Doctrine": "none"},
        )

        with (
            patch.object(search_companies, "_load_raw_cache", return_value={
                "_companies_hash": _compute_companies_hash(["Doctrine"]),
                "Doctrine": "none",
            }),
            patch.object(search_companies, "_update_companies_hash"),
            patch.object(search_companies, "_search_with_hint") as mock_search,
            patch("providers.llm.factory.build_llm", return_value=MagicMock()),
        ):
            result = search_companies.run(state)

        mock_search.assert_not_called()
        assert result["raw_jobs"] == []
        assert result["errors"] == []
