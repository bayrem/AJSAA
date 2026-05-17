"""Per-model price table and cost computation for LLM usage tracking.

The numbers here are the public per-million-token prices for the four models
AJSAA actually routes traffic through. They are *not* fetched at runtime — a
hardcoded table is simpler, deterministic across runs, and easy to diff in PR
review. The trade-off is that the table can go stale; the
``Prices verified`` comment above the table is the canary.

Pricing model conventions:
  - Costs are in USD per million tokens (the unit every vendor publishes).
  - The four token buckets we track are ``input``, ``output``,
    ``cache_read``, ``cache_create``. Providers without prompt caching
    (currently OpenAI for non-write reads) report zeros for the cache fields.
  - Unknown models log a warning *once* and return ``0.0``. Cost computation
    must never raise — a missing price entry should not crash the pipeline.

The cost numbers from the Claude Code CLI are *not* derived from this table —
that path reads ``total_cost_usd`` directly out of the CLI's JSON response.
This module is the fallback for providers that don't bill us natively.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


# Prices verified 2026-05-17 — re-check at https://docs.anthropic.com/en/docs/about-claude/models/all-models
# and https://platform.openai.com/docs/pricing for OpenAI models.
PRICES_PER_MTOKEN: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":         {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_create": 3.75},
    "claude-haiku-4-5-20251001": {"input": 1.0,  "output": 5.0,  "cache_read": 0.1,  "cache_create": 1.25},
    "gpt-4o":                    {"input": 2.5,  "output": 10.0, "cache_read": 1.25, "cache_create": 0.0},
    "gpt-4o-mini":               {"input": 0.15, "output": 0.6,  "cache_read": 0.075, "cache_create": 0.0},
}

# Models we've already warned about — keeps the log spam to one line per model
# instead of one per call. The lock guards the set against concurrent updates
# when multiple nodes share a tracker on different threads.
_WARNED_UNKNOWN: set[str] = set()
_WARN_LOCK = threading.Lock()


def _warn_once(model: str) -> None:
    """Log a warning for an unknown model exactly once per process."""
    with _WARN_LOCK:
        if model in _WARNED_UNKNOWN:
            return
        _WARNED_UNKNOWN.add(model)
    logger.warning(
        "pricing: no price table entry for model '%s' — cost will be reported as $0.00",
        model,
    )


def compute_cost(model: str, usage: dict) -> float:
    """Return the USD cost for one LLM call given its token usage breakdown.

    Args:
        model: The exact model identifier returned by the provider (e.g.
            ``"claude-sonnet-4-6"``). Matched case-sensitively against
            :data:`PRICES_PER_MTOKEN`.
        usage: Canonical usage dict with int counts in the four token buckets
            ``input_tokens``, ``output_tokens``, ``cache_read_input_tokens``,
            ``cache_creation_input_tokens``. Missing keys default to 0.

    Returns:
        The cost in USD, or ``0.0`` if the model isn't in the price table.

    Never raises — a price miss is a logging event, not an error.
    """
    rates = PRICES_PER_MTOKEN.get(model)
    if rates is None:
        _warn_once(model)
        return 0.0

    # Coerce to int with 0 default — providers occasionally omit keys for
    # zero-valued buckets, and a missing key shouldn't poison the cost.
    in_tok = int(usage.get("input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)
    cr_tok = int(usage.get("cache_read_input_tokens", 0) or 0)
    cc_tok = int(usage.get("cache_creation_input_tokens", 0) or 0)

    # All rates are per-million tokens, so the divisor is constant.
    total = (
        in_tok * rates["input"]
        + out_tok * rates["output"]
        + cr_tok * rates["cache_read"]
        + cc_tok * rates["cache_create"]
    ) / 1_000_000.0
    return total
