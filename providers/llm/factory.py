"""LLM-provider factory — picks the right backend and the right model.

Two pieces of state are resolved here:

  1. **Provider**  — anthropic / openai / claude_code_agent. Selected via
     ``cfg["provider"]``; defaults to anthropic.
  2. **Model**     — task-aware. Different tasks have different cost vs.
     quality trade-offs; the factory routes:

       - ``scoring`` → ``cfg["scoring_model"]`` (capable model — Sonnet)
       - ``search``  → ``cfg["search_model"]``  (cheap model — Haiku)
       - ``default`` → ``cfg["default_model"]`` or legacy ``cfg["model"]``

     Missing keys fall back down the chain so partial configs still work.
"""

# Module-level default — kept consistent with config.yaml so test fixtures
# stay in sync. Always upgrade this string when bumping the project default.
_FALLBACK_MODEL = "claude-sonnet-4-6"


def build_llm(cfg: dict, task: str = "default"):
    """Return a configured LangChain ``BaseChatModel`` for the given task.

    Args:
        cfg: The ``llm`` slice of config.yaml.
        task: Task name used for model selection. Anything not matching
            ``scoring`` or ``search`` falls through to ``default``.

    Returns:
        An instance ready for ``.invoke([HumanMessage(...)])``.

    Raises:
        ValueError: If ``provider`` is not recognised.
    """
    # Task-aware model resolution: try the task-specific key first, then
    # the default key, then the legacy ``model`` key for back-compat.
    resolved_model = (
        cfg.get(f"{task}_model")
        or cfg.get("default_model")
        or cfg.get("model", _FALLBACK_MODEL)
    )

    # Build a new dict so we don't mutate the caller's config — tests rely
    # on this invariant.
    resolved_cfg = {**cfg, "model": resolved_model}

    provider = resolved_cfg.get("provider", "anthropic").lower()

    if provider == "anthropic":
        from providers.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(resolved_cfg).build()
    elif provider == "openai":
        from providers.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(resolved_cfg).build()
    elif provider == "claude_code_agent":
        from providers.llm.claude_code_provider import ClaudeCodeProvider
        return ClaudeCodeProvider(resolved_cfg).build()
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            "Supported: anthropic, openai, claude_code_agent"
        )
