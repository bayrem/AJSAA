"""Circuit breaker for external connectors.

A simple per-connector breaker that opens after ``max_failures`` consecutive
failures and stays open for ``cooldown_seconds`` before automatically resetting.
State is persisted to ``.data/circuit_breakers.json`` so failures accumulate
across runs (otherwise a once-a-day cron would never trip the breaker).

State shape::

    {
      "<connector_name>": {
        "consecutive_failures": int,
        "opened_at": float  # epoch seconds; only present once breaker has opened
      }
    }

Public API (kept stable — used directly by search_jobs and others):
  - ``is_open(name, ...)``        — check before issuing a request
  - ``record_success(name)``      — call after a successful request
  - ``record_failure(name, ...)`` — call after a failed request
"""
import logging
import time
from pathlib import Path

from providers.utils import JsonCache

logger = logging.getLogger(__name__)


# Persisted state lives under .data/ (gitignored) so it survives between runs
# but isn't committed.
_CACHE = JsonCache(Path(".data/circuit_breakers.json"))

_DEFAULT_MAX_FAILURES = 3
_DEFAULT_COOLDOWN = 86_400  # 24 hours


def is_open(
    name: str,
    max_failures: int = _DEFAULT_MAX_FAILURES,
    cooldown_seconds: int = _DEFAULT_COOLDOWN,
) -> bool:
    """Return True if the named connector should currently be skipped.

    The breaker is open when at least ``max_failures`` consecutive failures
    have been recorded AND the cooldown window has not yet elapsed. After
    cooldown the breaker auto-closes by deleting its state entry.
    """
    state = _CACHE.load()
    entry = state.get(name, {})
    failures = entry.get("consecutive_failures", 0)
    opened_at = entry.get("opened_at")

    if failures < max_failures:
        return False

    # Breaker is open — check whether the cooldown has elapsed
    if opened_at and (time.time() - opened_at) >= cooldown_seconds:
        logger.info("circuit_breaker: '%s' cooldown elapsed — resetting", name)
        record_success(name)  # wipes the entry
        return False

    logger.warning("circuit_breaker: '%s' is OPEN (%d failures) — skipping", name, failures)
    return True


def record_success(name: str) -> None:
    """Clear any failure streak for the named connector."""
    state = _CACHE.load()
    if name in state:
        del state[name]
        _CACHE.save(state)


def record_failure(
    name: str,
    max_failures: int = _DEFAULT_MAX_FAILURES,
    cooldown_seconds: int = _DEFAULT_COOLDOWN,
) -> None:
    """Increment the failure counter and open the breaker if the threshold is hit."""
    state = _CACHE.load()
    entry = state.setdefault(name, {"consecutive_failures": 0})
    entry["consecutive_failures"] += 1
    failures = entry["consecutive_failures"]

    # Stamp the breaker's open time exactly once — re-stamping on later
    # failures would reset the cooldown clock and could keep it permanently open.
    if failures >= max_failures and "opened_at" not in entry:
        entry["opened_at"] = time.time()
        logger.error(
            "circuit_breaker: '%s' opened after %d consecutive failures — "
            "will retry after %dh",
            name, failures, cooldown_seconds // 3600,
        )

    _CACHE.save(state)
