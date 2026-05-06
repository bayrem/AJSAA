def build_llm(cfg: dict):
    """Return a LangChain BaseChatModel for the configured provider."""
    provider = cfg.get("provider", "anthropic").lower()

    if provider == "anthropic":
        from providers.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg).build()
    elif provider == "openai":
        from providers.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(cfg).build()
    elif provider == "claude_code_agent":
        from providers.llm.claude_code_provider import ClaudeCodeProvider
        return ClaudeCodeProvider(cfg).build()
    else:
        raise ValueError(f"Unknown LLM provider: '{provider}'. Supported: anthropic, openai, claude_code_agent")
