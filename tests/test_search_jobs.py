"""Tests for agent/nodes/search_jobs — parallel execution and rate-limit semaphores."""
import time
from threading import Semaphore
from unittest.mock import MagicMock, patch

from agent.nodes.search_jobs import (
    _filter_recent,
    _make_job_id,
    _parse_connector_cfg,
    _run_parallel,
    _search_one,
)

# ── _parse_connector_cfg ──────────────────────────────────────────────────────

class TestParseConnectorCfg:
    def test_string_entry(self):
        cfg = _parse_connector_cfg("france_travail")
        assert cfg["name"] == "france_travail"
        assert cfg["enabled"] is True
        assert cfg["fallback_only"] is False
        assert cfg["max_concurrent"] is None

    def test_dict_with_max_concurrent(self):
        cfg = _parse_connector_cfg({"name": "adzuna", "max_concurrent": 5})
        assert cfg["name"] == "adzuna"
        assert cfg["max_concurrent"] == 5

    def test_dict_disabled(self):
        cfg = _parse_connector_cfg({"name": "linkedin", "enabled": False})
        assert cfg["enabled"] is False


# ── _search_one ───────────────────────────────────────────────────────────────

class TestSearchOne:
    def _make_provider(self, results=None):
        provider = MagicMock()
        provider.search.return_value = results or [{"title": "PM", "company": "Acme"}]
        return provider

    def test_happy_path_returns_results_and_log(self):
        provider = self._make_provider([{"title": "PM"}])
        sem = Semaphore(1)
        results, log, error = _search_one(provider, "test_connector", "PM Paris", 5, sem)
        assert results == [{"title": "PM"}]
        assert "test_connector" in log
        assert "PM Paris" in log
        assert error is None

    def test_query_has_recency_suffix(self):
        provider = self._make_provider()
        sem = Semaphore(1)
        _search_one(provider, "c", "PM Paris", 5, sem)
        called_query = provider.search.call_args[0][0]
        assert called_query.endswith("posted last week")

    def test_provider_exception_returns_error(self):
        provider = MagicMock()
        provider.search.side_effect = RuntimeError("timeout")
        sem = Semaphore(1)
        results, log, error = _search_one(provider, "c", "q", 5, sem)
        assert results == []
        assert log is None
        assert "timeout" in error

    def test_semaphore_limits_concurrency(self):
        """Two threads sharing a Semaphore(1) must not overlap."""
        sem = Semaphore(1)
        overlap_detected = []
        inside = []

        def slow_search(query, max_results):
            inside.append(1)
            if len(inside) > 1:
                overlap_detected.append(True)
            time.sleep(0.05)
            inside.pop()
            return []

        provider = MagicMock()
        provider.search.side_effect = slow_search

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_search_one, provider, "c", "q1", 5, sem)
            f2 = pool.submit(_search_one, provider, "c", "q2", 5, sem)
            f1.result()
            f2.result()

        assert not overlap_detected, "Semaphore failed to prevent overlap"


# ── _run_parallel ─────────────────────────────────────────────────────────────

class TestRunParallel:
    def _make_connector_cfg(self, name="mock_connector", max_concurrent=2):
        return {
            "name": name,
            "enabled": True,
            "fallback_only": False,
            "max_results_per_query": 5,
            "max_queries": None,
            "max_concurrent": max_concurrent,
        }

    def test_results_aggregated_across_queries(self):
        provider = MagicMock()
        provider.search.side_effect = lambda q, max_results: [{"title": q}]

        run_log: list = []
        errors: list = []

        with patch("agent.nodes.search_jobs._get_search_provider", return_value=provider):
            results = _run_parallel(
                [self._make_connector_cfg()],
                ["query1", "query2"],
                llm=MagicMock(),
                search_cfg={"max_results_per_query": 5},
                run_log=run_log,
                errors=errors,
            )

        assert len(results) == 2
        titles = {r["title"] for r in results}
        assert "query1 posted last week" in titles
        assert "query2 posted last week" in titles
        assert not errors

    def test_connector_init_failure_logged(self):
        run_log: list = []
        errors: list = []

        with patch("agent.nodes.search_jobs._get_search_provider", side_effect=ValueError("bad creds")):
            results = _run_parallel(
                [self._make_connector_cfg()],
                ["q"],
                llm=MagicMock(),
                search_cfg={},
                run_log=run_log,
                errors=errors,
            )

        assert results == []
        assert any("bad creds" in e for e in errors)

    def test_empty_queries_returns_empty(self):
        run_log: list = []
        errors: list = []

        with patch("agent.nodes.search_jobs._get_search_provider", return_value=MagicMock()):
            results = _run_parallel(
                [self._make_connector_cfg()],
                [],
                llm=MagicMock(),
                search_cfg={},
                run_log=run_log,
                errors=errors,
            )

        assert results == []

    def test_max_queries_limits_tasks(self):
        provider = MagicMock()
        provider.search.return_value = []

        run_log: list = []
        errors: list = []

        cfg = self._make_connector_cfg()
        cfg["max_queries"] = 2

        with patch("agent.nodes.search_jobs._get_search_provider", return_value=provider):
            _run_parallel(
                [cfg],
                ["q1", "q2", "q3", "q4"],
                llm=MagicMock(),
                search_cfg={},
                run_log=run_log,
                errors=errors,
            )

        assert provider.search.call_count == 2

    def test_multiple_connectors_run_in_parallel(self):
        """Wall-clock time with two slow connectors should be < 2× single connector time."""
        call_times: list[float] = []

        def slow_search(query, max_results):
            call_times.append(time.time())
            time.sleep(0.1)
            return [{"title": "job"}]

        provider_a = MagicMock()
        provider_a.search.side_effect = slow_search
        provider_b = MagicMock()
        provider_b.search.side_effect = slow_search

        def get_provider(name, llm, cfg):
            return provider_a if name == "connector_a" else provider_b

        cfg_a = self._make_connector_cfg("connector_a")
        cfg_b = self._make_connector_cfg("connector_b")

        run_log: list = []
        errors: list = []

        start = time.time()
        with patch("agent.nodes.search_jobs._get_search_provider", side_effect=get_provider):
            _run_parallel(
                [cfg_a, cfg_b],
                ["q1"],
                llm=MagicMock(),
                search_cfg={},
                run_log=run_log,
                errors=errors,
            )
        elapsed = time.time() - start

        # Two 0.1s tasks in parallel should finish well under 0.15s (serial = 0.2s)
        assert elapsed < 0.18, f"Expected parallel execution, took {elapsed:.2f}s"


# ── _filter_recent ────────────────────────────────────────────────────────────

class TestFilterRecent:
    def test_removes_stale_jobs(self):
        jobs = [
            {"title": "PM", "description": "posted last month"},
            {"title": "PM", "description": "posted last week"},
        ]
        kept = _filter_recent(jobs)
        assert len(kept) == 1
        assert kept[0]["description"] == "posted last week"

    def test_french_stale_signal(self):
        jobs = [{"title": "Chef de projet", "description": "il y a 2 mois"}]
        assert _filter_recent(jobs) == []

    def test_all_recent_passes(self):
        jobs = [{"title": "PM", "description": "posted yesterday"}]
        assert len(_filter_recent(jobs)) == 1


# ── _make_job_id ──────────────────────────────────────────────────────────────

class TestMakeJobId:
    def test_deterministic(self):
        job = {"title": "PM", "company": "Acme", "location": "Paris"}
        assert _make_job_id(job) == _make_job_id(job)

    def test_length(self):
        job = {"title": "PM", "company": "Acme", "location": "Paris"}
        assert len(_make_job_id(job)) == 16

    def test_different_jobs_different_ids(self):
        j1 = {"title": "PM", "company": "Acme", "location": "Paris"}
        j2 = {"title": "PM", "company": "Beta", "location": "Paris"}
        assert _make_job_id(j1) != _make_job_id(j2)
