"""Tests for the LLM usage tracker.

Covers issue #60's acceptance criteria:
  - ``record()`` aggregates by model, by node, and into the grand total.
  - ``set_node()`` correctly attributes calls to the current node.
  - ``snapshot()`` returns an immutable deep copy.
  - Thread-safe under concurrent ``record()`` from multiple threads.
  - Unknown / unset node names do not raise.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from providers.llm import usage_tracker
from providers.llm.usage_tracker import (
    UsageCaptureHandler,
    UsageTracker,
    _normalise_usage_metadata,
)


@pytest.fixture
def tracker() -> UsageTracker:
    """Fresh tracker per test so state doesn't leak."""
    return UsageTracker()


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level tracker before each test that uses it."""
    usage_tracker.reset()
    yield
    usage_tracker.reset()


# ── Basic aggregation ───────────────────────────────────────────────────────


class TestRecordAggregation:
    def test_single_call_populates_grand_total(self, tracker: UsageTracker):
        tracker.record(
            "claude-sonnet-4-6",
            {"input_tokens": 100, "output_tokens": 50},
            cost_usd=0.001,
        )
        snap = tracker.snapshot()
        gt = snap["grand_total"]
        assert gt["input_tokens"] == 100
        assert gt["output_tokens"] == 50
        assert gt["cost_usd"] == pytest.approx(0.001)
        assert gt["calls"] == 1

    def test_two_calls_sum_into_grand_total(self, tracker: UsageTracker):
        tracker.record("claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50}, 0.001)
        tracker.record("gpt-4o", {"input_tokens": 200, "output_tokens": 100}, 0.002)
        gt = tracker.snapshot()["grand_total"]
        assert gt["input_tokens"] == 300
        assert gt["output_tokens"] == 150
        assert gt["cost_usd"] == pytest.approx(0.003)
        assert gt["calls"] == 2

    def test_by_model_groups_correctly(self, tracker: UsageTracker):
        tracker.record("claude-sonnet-4-6", {"input_tokens": 100}, 0.001)
        tracker.record("claude-sonnet-4-6", {"input_tokens": 200}, 0.002)
        tracker.record("gpt-4o", {"input_tokens": 50}, 0.0005)

        bm = tracker.snapshot()["by_model"]
        assert bm["claude-sonnet-4-6"]["input_tokens"] == 300
        assert bm["claude-sonnet-4-6"]["calls"] == 2
        assert bm["gpt-4o"]["input_tokens"] == 50
        assert bm["gpt-4o"]["calls"] == 1

    def test_cache_token_buckets_aggregated(self, tracker: UsageTracker):
        tracker.record(
            "claude-sonnet-4-6",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 200,
            },
            0.001,
        )
        gt = tracker.snapshot()["grand_total"]
        assert gt["cache_read_input_tokens"] == 1000
        assert gt["cache_creation_input_tokens"] == 200

    def test_missing_usage_keys_treated_as_zero(self, tracker: UsageTracker):
        # Provider sends only input/output — cache buckets default to 0
        # rather than raising.
        tracker.record("gpt-4o", {"input_tokens": 100, "output_tokens": 50}, 0.001)
        gt = tracker.snapshot()["grand_total"]
        assert gt["cache_read_input_tokens"] == 0
        assert gt["cache_creation_input_tokens"] == 0


# ── Per-node attribution ────────────────────────────────────────────────────


class TestNodeAttribution:
    def test_call_without_set_node_only_hits_by_model(self, tracker: UsageTracker):
        tracker.record("claude-sonnet-4-6", {"input_tokens": 100}, 0.001)
        snap = tracker.snapshot()
        assert snap["by_node"] == {}
        assert "claude-sonnet-4-6" in snap["by_model"]

    def test_set_node_attributes_subsequent_calls(self, tracker: UsageTracker):
        tracker.set_node("analyze_jobs")
        tracker.record("claude-sonnet-4-6", {"input_tokens": 100}, 0.001)
        snap = tracker.snapshot()
        assert "analyze_jobs" in snap["by_node"]
        assert snap["by_node"]["analyze_jobs"]["input_tokens"] == 100

    def test_clearing_node_stops_attribution(self, tracker: UsageTracker):
        tracker.set_node("analyze_jobs")
        tracker.record("claude-sonnet-4-6", {"input_tokens": 100}, 0.001)
        tracker.set_node(None)
        tracker.record("claude-sonnet-4-6", {"input_tokens": 50}, 0.0005)

        snap = tracker.snapshot()
        # Only the first call should be attributed to analyze_jobs.
        assert snap["by_node"]["analyze_jobs"]["input_tokens"] == 100
        assert snap["by_node"]["analyze_jobs"]["calls"] == 1
        # Both contribute to grand total.
        assert snap["grand_total"]["input_tokens"] == 150
        assert snap["grand_total"]["calls"] == 2

    def test_switching_nodes_routes_to_each(self, tracker: UsageTracker):
        tracker.set_node("search_jobs")
        tracker.record("gpt-4o", {"input_tokens": 100}, 0.001)
        tracker.set_node("analyze_jobs")
        tracker.record("claude-sonnet-4-6", {"input_tokens": 200}, 0.002)

        bn = tracker.snapshot()["by_node"]
        assert bn["search_jobs"]["input_tokens"] == 100
        assert bn["analyze_jobs"]["input_tokens"] == 200


# ── Snapshot immutability ───────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_is_deep_copy(self, tracker: UsageTracker):
        tracker.record("claude-sonnet-4-6", {"input_tokens": 100}, 0.001)
        snap = tracker.snapshot()
        # Mutating the snapshot should not affect tracker state.
        snap["by_model"]["claude-sonnet-4-6"]["input_tokens"] = 999
        snap["grand_total"]["calls"] = 999
        snap["by_model"]["forged"] = {"input_tokens": 5}

        fresh = tracker.snapshot()
        assert fresh["by_model"]["claude-sonnet-4-6"]["input_tokens"] == 100
        assert fresh["grand_total"]["calls"] == 1
        assert "forged" not in fresh["by_model"]


# ── Thread safety ───────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_records_sum_correctly(self, tracker: UsageTracker):
        """Many threads each recording N calls → totals must add up exactly."""
        threads = 8
        calls_per_thread = 200

        def worker():
            for _ in range(calls_per_thread):
                tracker.record(
                    "claude-sonnet-4-6",
                    {"input_tokens": 1, "output_tokens": 2},
                    0.0001,
                )

        with ThreadPoolExecutor(max_workers=threads) as pool:
            for _ in range(threads):
                pool.submit(worker)

        gt = tracker.snapshot()["grand_total"]
        expected_calls = threads * calls_per_thread
        assert gt["calls"] == expected_calls
        assert gt["input_tokens"] == expected_calls * 1
        assert gt["output_tokens"] == expected_calls * 2
        assert gt["cost_usd"] == pytest.approx(expected_calls * 0.0001)

    def test_per_thread_node_attribution(self, tracker: UsageTracker):
        """Each thread sets its own node — attribution must not bleed across."""
        # We use barrier-style sync so both threads have their node set
        # before either records. If set_node were process-global instead of
        # thread-local, one thread would steal the other's attribution.
        ready = threading.Barrier(2)
        done = threading.Event()

        def worker(node: str):
            tracker.set_node(node)
            ready.wait()
            for _ in range(50):
                tracker.record("claude-sonnet-4-6", {"input_tokens": 1}, 0.0)
            done.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(worker, "node_a")
            pool.submit(worker, "node_b")

        assert done.wait(5.0)
        bn = tracker.snapshot()["by_node"]
        # Each thread attributed all 50 of its calls to its own node.
        assert bn["node_a"]["calls"] == 50
        assert bn["node_b"]["calls"] == 50


# ── Module singleton ────────────────────────────────────────────────────────


class TestModuleSingleton:
    def test_functional_wrappers_route_to_singleton(self):
        usage_tracker.record("gpt-4o", {"input_tokens": 100}, 0.001)
        snap = usage_tracker.snapshot()
        assert "gpt-4o" in snap["by_model"]
        assert snap["grand_total"]["calls"] == 1

    def test_singleton_reset_clears_state(self):
        usage_tracker.record("gpt-4o", {"input_tokens": 100}, 0.001)
        usage_tracker.reset()
        snap = usage_tracker.snapshot()
        assert snap["by_model"] == {}
        assert snap["grand_total"]["calls"] == 0


# ── LangChain callback ──────────────────────────────────────────────────────


class TestUsageCaptureHandler:
    def test_normalise_usage_metadata_full_shape(self):
        # Matches the LangChain v1 nested shape used by ChatAnthropic.
        canonical = _normalise_usage_metadata({
            "input_tokens": 100,
            "output_tokens": 50,
            "input_token_details": {"cache_read": 10, "cache_creation": 5},
        })
        assert canonical == {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        }

    def test_normalise_usage_metadata_minimal_shape(self):
        # OpenAI typically reports only the two top-level counters.
        canonical = _normalise_usage_metadata({"input_tokens": 100, "output_tokens": 50})
        assert canonical["cache_read_input_tokens"] == 0
        assert canonical["cache_creation_input_tokens"] == 0

    def _build_llm_result(
        self,
        usage_metadata: dict | None,
        response_metadata: dict | None = None,
    ):
        """Build an :class:`LLMResult` using real LangChain types.

        The real ``ChatGeneration`` / ``AIMessage`` classes validate via
        pydantic, so we can't fake them with ``SimpleNamespace``. Returning
        the full LLMResult keeps the handler under test using the exact
        objects it would see in production.
        """
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult

        message = AIMessage(
            content="ok",
            response_metadata=response_metadata or {},
            usage_metadata=usage_metadata,  # type: ignore[arg-type]
        )
        gen = ChatGeneration(message=message)
        return LLMResult(generations=[[gen]], llm_output={})

    def test_handler_records_usage_on_llm_end(self):
        import uuid as _uuid

        result = self._build_llm_result(
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            },
            response_metadata={"model_name": "claude-sonnet-4-6"},
        )

        handler = UsageCaptureHandler(default_model="claude-sonnet-4-6")
        handler.on_llm_end(result, run_id=_uuid.uuid4())

        snap = usage_tracker.snapshot()
        assert snap["by_model"]["claude-sonnet-4-6"]["input_tokens"] == 100
        assert snap["by_model"]["claude-sonnet-4-6"]["output_tokens"] == 50
        # Cost should be > 0 because the model is in the price table.
        assert snap["by_model"]["claude-sonnet-4-6"]["cost_usd"] > 0

    def test_handler_falls_back_to_default_model(self):
        # If the response doesn't echo a model name, the handler should fall
        # back to the configured default.
        import uuid as _uuid

        result = self._build_llm_result(
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
            response_metadata={},  # no model field
        )

        handler = UsageCaptureHandler(default_model="gpt-4o-mini")
        handler.on_llm_end(result, run_id=_uuid.uuid4())

        snap = usage_tracker.snapshot()
        assert "gpt-4o-mini" in snap["by_model"]

    def test_handler_skips_message_without_usage(self):
        # Some LangChain paths return AIMessages with no usage_metadata.
        # The handler must silently skip them — no record, no exception.
        import uuid as _uuid

        result = self._build_llm_result(usage_metadata=None)

        handler = UsageCaptureHandler(default_model="gpt-4o")
        handler.on_llm_end(result, run_id=_uuid.uuid4())

        assert usage_tracker.snapshot()["grand_total"]["calls"] == 0
