def build_llm(cfg: dict, task: str = "default"):
    """Return a LangChain BaseChatModel for the configured provider.

    task controls which model is selected:
      scoring  → cfg["scoring_model"]
      search   → cfg["search_model"]
      default  → cfg["default_model"] or cfg["model"]
    Falls back down the chain if a key is absent.
    """
    resolved_model = (
        cfg.get(f"{task}_model")
        or cfg.get("default_model")
        or cfg.get("model", "claude-sonnet-4-6")
    )
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
        raise ValueError(f"Unknown LLM provider: '{provider}'. Supported: anthropic, openai, claude_code_agent")
