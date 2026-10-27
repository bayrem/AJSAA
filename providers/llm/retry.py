"""Shared retry policy for subscription-backed LLM providers.

Both the legacy ``claude -p`` provider and the Claude Agent SDK provider hit
the same Claude Code rate limits, so they share one backoff schedule and one
list of "don't bother retrying" auth-error hints. Keeping them here means the
SDK provider survives the deletion of the legacy CLI provider (planned in M2).
"""
import random

# Per-attempt backoff schedule (seconds). Index 0 = wait before attempt 2,
# index 1 = wait before attempt 3, etc. Tuned to the ~65s recovery window
# observed for the Claude Code subscription's per-minute Sonnet rate limit
# in issue #58 — three retries (5 + 15 + 45 = 65s) cover the worst case.
BACKOFF_SECONDS = (5.0, 15.0, 45.0)
JITTER_PCT = 0.2  # ±20% on each delay to avoid synchronised retry storms

# Substrings (case-insensitive) we look for in error messages to decide
# whether to keep retrying. Auth errors are permanent — no point burning the
# retry budget. Other errors are treated as transient by default.
AUTH_ERROR_HINTS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "auth_",
    "login required",
    "not logged in",
)


def backoff_seconds(attempt: int) -> float:
    """Return seconds to sleep before the given attempt (1-indexed).

    Attempt 1 has no wait. Attempts 2+ pull from ``BACKOFF_SECONDS`` with
    ±20% jitter. Attempts beyond the schedule reuse the last entry. Returns
    0 for attempt 1 so callers can use a uniform ``if wait > 0: sleep(wait)``
    pattern.
    """
    if attempt <= 1:
        return 0.0
    idx = min(attempt - 2, len(BACKOFF_SECONDS) - 1)
    base = BACKOFF_SECONDS[idx]
    jitter = base * JITTER_PCT
    return base + random.uniform(-jitter, jitter)


def is_auth_error(message: str) -> bool:
    """True when an error message looks like a permanent authentication failure."""
    lowered = message.lower()
    return any(hint in lowered for hint in AUTH_ERROR_HINTS)
