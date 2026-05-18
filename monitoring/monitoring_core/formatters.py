"""Shared formatting utilities used by both TUI and web monitoring."""
from typing import Any


def fmt_tokens(n: int) -> str:
    """Compact token count (e.g. 14237 -> '14.2k')."""
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1000:.0f}k"


def fmt_cost(cost: float) -> str:
    """USD cost string. Uses 4 decimals under $0.01 so tiny costs aren't '$0.00'."""
    if cost == 0:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def safe_int(value: Any) -> int:
    """Coerce value to int with a 0 default — providers may emit None."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    """Coerce value to float with a 0.0 default."""
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
