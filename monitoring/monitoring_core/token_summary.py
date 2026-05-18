"""Run-end token usage summary formatter."""
from monitoring.monitoring_core.formatters import fmt_tokens

_MODEL_ALIASES: dict[str, str] = {
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5-20251001": "haiku",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
}


def model_alias(model: str) -> str:
    return _MODEL_ALIASES.get(model, model)


def format_token_summary(snapshot: dict) -> str:
    """One-line run-end summary: '$X.XX total · NNN in / MMM out · N calls (m1 $A, m2 $B)'.

    Models are sorted by descending cost so the biggest contributor reads first.
    """
    grand = snapshot.get("grand_total") or {}
    by_model = snapshot.get("by_model") or {}

    total_cost = float(grand.get("cost_usd", 0.0) or 0.0)
    in_tok = int(grand.get("input_tokens", 0) or 0)
    out_tok = int(grand.get("output_tokens", 0) or 0)
    calls = int(grand.get("calls", 0) or 0)

    parts: list[str] = []
    for model, entry in sorted(
        by_model.items(),
        key=lambda kv: float(kv[1].get("cost_usd", 0.0) or 0.0),
        reverse=True,
    ):
        cost = float(entry.get("cost_usd", 0.0) or 0.0)
        parts.append(f"{model_alias(model)} ${cost:.2f}")

    suffix = f" ({', '.join(parts)})" if parts else ""
    return (
        f"Tokens: ${total_cost:.2f} total · "
        f"{in_tok} in / {out_tok} out · "
        f"{calls} calls{suffix}"
    )


def format_footer_tokens(snapshot: dict) -> str:
    """Compact one-line token footer for the live TUI (refreshes at 4 Hz)."""
    grand = (snapshot or {}).get("grand_total") or {}
    in_tok = int(grand.get("input_tokens", 0) or 0)
    out_tok = int(grand.get("output_tokens", 0) or 0)
    calls = int(grand.get("calls", 0) or 0)
    cost = float(grand.get("cost_usd", 0.0) or 0.0)
    return (
        f"Tokens: {fmt_tokens(in_tok)} in / "
        f"{fmt_tokens(out_tok)} out · "
        f"${cost:.2f} · {calls} calls"
    )
