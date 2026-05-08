"""Circuit breaker for external connectors.

State is persisted to .data/circuit_breakers.json so failures accumulate
across runs. A connector is "open" (skipped) after max_failures consecutive
failures and remains open until cooldown_seconds have elapsed.
"""
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_FILE = Path(".data/circuit_breakers.json")
_DEFAULT_MAX_FAILURES = 3
_DEFAULT_COOLDOWN = 86_400  # 24 hours


def _load() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("circuit_breaker: failed to persist state: %s", e)


def is_open(name: str, max_failures: int = _DEFAULT_MAX_FAILURES,
            cooldown_seconds: int = _DEFAULT_COOLDOWN) -> bool:
    """Return True if the connector should be skipped this run."""
    state = _load()
    entry = state.get(name, {})
    failures = entry.get("consecutive_failures", 0)
    opened_at = entry.get("opened_at")

    if failures < max_failures:
        return False

    if opened_at and (time.time() - opened_at) >= cooldown_seconds:
        logger.info("circuit_breaker: '%s' cooldown elapsed — resetting", name)
        record_success(name)
        return False

    logger.warning("circuit_breaker: '%s' is OPEN (%d failures) — skipping", name, failures)
    return True


def record_success(name: str) -> None:
    state = _load()
    if name in state:
        del state[name]
        _save(state)


def record_failure(name: str, max_failures: int = _DEFAULT_MAX_FAILURES,
                   cooldown_seconds: int = _DEFAULT_COOLDOWN) -> None:
    state = _load()
    entry = state.setdefault(name, {"consecutive_failures": 0})
    entry["consecutive_failures"] += 1
    failures = entry["consecutive_failures"]

    if failures >= max_failures and "opened_at" not in entry:
        entry["opened_at"] = time.time()
        logger.error(
            "circuit_breaker: '%s' opened after %d consecutive failures — "
            "will retry after %dh",
            name, failures, cooldown_seconds // 3600,
        )

    _save(state)
