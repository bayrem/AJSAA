"""Thread-safe accumulator for per-call LLM token usage and dollar cost.

Every LLM provider records each successful call through this module. The
tracker exposes three views over the same underlying data:

  - ``by_model`` — totals per model name (e.g. ``claude-sonnet-4-6``)
  - ``by_node``  — totals per LangGraph node, attributed via thread-local
                   state set by ``agent/graph.py``'s ``_safe`` wrapper
  - ``grand_total`` — pipeline-wide sum

Why module-level singleton: the providers don't know about LangGraph and
shouldn't have to. A module singleton plus thin functional wrappers
(``record``, ``set_node``, ``snapshot``) keeps the call sites trivial and
lets the graph layer wire node attribution in one place.

Why thread-local for the current node: LangGraph nodes run sequentially today,
but if anything in the future fans out (parallel search nodes, batched
scoring with threads), per-thread node tracking gives correct attribution
without any further coordination. The cost is one tiny ``threading.local``
that single-threaded code never touches.

Thread safety: a single ``RLock`` guards every mutating operation against
concurrent providers. Reads use the same lock (cheap — snapshots are taken
at run end, not per call).
"""
from __future__ import annotations

import copy
import logging
import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# Canonical zero-valued usage shape every entry starts from. Kept as a
# template so adding a new bucket is one-line and call sites stay correct.
_EMPTY_ENTRY: dict[str, Any] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cost_usd": 0.0,
    "calls": 0,
}


class UsageTracker:
    """Thread-safe accumulator. See module docstring for design rationale."""

    def __init__(self) -> None:
        # ``by_model`` and ``by_node`` are populated lazily — only keys that
        # actually saw a call appear, so the snapshot stays small.
        self._by_model: dict[str, dict[str, Any]] = {}
        self._by_node: dict[str, dict[str, Any]] = {}
        self._total: dict[str, Any] = dict(_EMPTY_ENTRY)
        self._lock = threading.RLock()
        # Current node name is per-thread so concurrent nodes don't trample
        # each other's attribution. ``None`` means "no node context"
        # (e.g. calls made directly from run.py).
        self._tls = threading.local()

    # ── public API ──────────────────────────────────────────────────────────

    def record(self, model: str, usage: dict, cost_usd: float) -> None:
        """Record one successful LLM call.

        Args:
            model: Exact model identifier (used as the ``by_model`` key).
            usage: Canonical usage dict — see ``providers/llm/pricing.py``
                for the four token bucket keys. Missing keys are treated as
                zero.
            cost_usd: Cost in USD for *this single call*. Provided by the
                caller because some providers (Claude Code CLI) bill us
                natively and the price table fallback would be wrong.
        """
        # Coerce token counts to int with 0 defaults. Providers can omit
        # zero-valued buckets and we shouldn't poison the totals because of it.
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        cr_tok = int(usage.get("cache_read_input_tokens", 0) or 0)
        cc_tok = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cost = float(cost_usd or 0.0)

        with self._lock:
            self._accumulate(self._bucket(self._by_model, model), in_tok, out_tok, cr_tok, cc_tok, cost)
            node = getattr(self._tls, "node", None)
            if node:
                self._accumulate(self._bucket(self._by_node, node), in_tok, out_tok, cr_tok, cc_tok, cost)
            self._accumulate(self._total, in_tok, out_tok, cr_tok, cc_tok, cost)

    def set_node(self, node_name: str | None) -> None:
        """Set or clear the current node for the calling thread.

        Called by the graph's ``_safe`` wrapper before/after each node so that
        any LLM calls inside the node are attributed correctly.
        """
        self._tls.node = node_name

    def snapshot(self) -> dict:
        """Return a deep copy of all accumulated data, safe to embed in state."""
        with self._lock:
            return {
                "by_model": copy.deepcopy(self._by_model),
                "by_node": copy.deepcopy(self._by_node),
                "grand_total": copy.deepcopy(self._total),
            }

    def reset(self) -> None:
        """Wipe the tracker. Useful for tests; not used in production."""
        with self._lock:
            self._by_model.clear()
            self._by_node.clear()
            self._total = dict(_EMPTY_ENTRY)
            self._tls.node = None

    # ── internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _bucket(store: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
        """Return the entry for ``key``, creating a zero entry on first use."""
        entry = store.get(key)
        if entry is None:
            entry = dict(_EMPTY_ENTRY)
            store[key] = entry
        return entry

    @staticmethod
    def _accumulate(
        entry: dict[str, Any],
        in_tok: int,
        out_tok: int,
        cr_tok: int,
        cc_tok: int,
        cost: float,
    ) -> None:
        """Add one call's worth of tokens and cost into ``entry`` in place."""
        entry["input_tokens"] += in_tok
        entry["output_tokens"] += out_tok
        entry["cache_read_input_tokens"] += cr_tok
        entry["cache_creation_input_tokens"] += cc_tok
        entry["cost_usd"] += cost
        entry["calls"] += 1


# Module-level singleton — providers call ``record(...)`` directly, no need
# to plumb the tracker through the factory.
_TRACKER = UsageTracker()


def record(model: str, usage: dict, cost_usd: float) -> None:
    """Thin functional wrapper around :meth:`UsageTracker.record`."""
    _TRACKER.record(model, usage, cost_usd)


def set_node(node_name: str | None) -> None:
    """Thin functional wrapper around :meth:`UsageTracker.set_node`."""
    _TRACKER.set_node(node_name)


def snapshot() -> dict:
    """Thin functional wrapper around :meth:`UsageTracker.snapshot`."""
    return _TRACKER.snapshot()


def reset() -> None:
    """Reset the singleton — exposed for tests only."""
    _TRACKER.reset()


# ── LangChain callback handler ──────────────────────────────────────────────


class UsageCaptureHandler(BaseCallbackHandler):
    """LangChain callback that records token usage on every LLM completion.

    Attached to ``ChatAnthropic`` / ``ChatOpenAI`` at construction time via
    the ``callbacks`` parameter. On every ``on_llm_end`` we walk the response
    generations, pull ``usage_metadata`` off each ``AIMessage`` (LangChain's
    canonical token-count shape), look up the model from ``response_metadata``
    if available, and emit one ``usage_tracker.record(...)`` call per
    completion.

    Cost is computed via :mod:`providers.llm.pricing` because the SDK does
    not bill us directly — we infer cost from the published per-million-token
    rate card.
    """

    def __init__(self, default_model: str) -> None:
        super().__init__()
        # ``default_model`` is the model the provider was configured with —
        # used when the SDK response doesn't echo a model field (rare).
        self._default_model = default_model

    def on_llm_end(  # noqa: D401 — LangChain interface name
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Record one ``usage_tracker.record`` call per AIMessage with usage data."""
        # Lazy import to avoid a hard dependency cycle: pricing depends on
        # nothing, but importing it at module top-level would force every
        # consumer of usage_tracker to pull in the pricing table at import
        # time. Doing it here keeps startup cheap.
        from providers.llm.pricing import compute_cost

        try:
            for gen_list in response.generations:
                for gen in gen_list:
                    message = getattr(gen, "message", None)
                    if message is None:
                        continue
                    usage_metadata = getattr(message, "usage_metadata", None)
                    if not usage_metadata:
                        continue

                    # ``response_metadata`` is the LangChain-canonical place
                    # for the model id. Fall back to the configured default
                    # if the provider didn't fill it in.
                    resp_meta = getattr(message, "response_metadata", {}) or {}
                    model = (
                        resp_meta.get("model_name")
                        or resp_meta.get("model")
                        or self._default_model
                    )

                    canonical = _normalise_usage_metadata(usage_metadata)
                    cost = compute_cost(model, canonical)
                    record(model, canonical, cost)
        except Exception as exc:  # pragma: no cover — defensive only
            logger.debug("UsageCaptureHandler failed: %s", exc)


def _normalise_usage_metadata(meta: dict) -> dict:
    """Translate LangChain's ``usage_metadata`` to our canonical 4-bucket shape.

    LangChain v1 exposes ``input_tokens`` / ``output_tokens`` at the top level
    and nests cache details under ``input_token_details``:

      ``{"input_tokens": int, "output_tokens": int,
         "input_token_details": {"cache_read": int, "cache_creation": int}}``

    Different providers populate different subsets; missing keys default to 0.
    """
    details = meta.get("input_token_details") or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0) or 0),
        "output_tokens": int(meta.get("output_tokens", 0) or 0),
        "cache_read_input_tokens": int(details.get("cache_read", 0) or 0),
        "cache_creation_input_tokens": int(details.get("cache_creation", 0) or 0),
    }
