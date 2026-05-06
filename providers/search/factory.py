def build_search(name: str, llm, cfg: dict):
    """Return a BaseSearchProvider for the given connector name."""
    name = name.lower()
    if name == "anthropic_web":
        from providers.search.web_search import AnthropicWebSearchProvider
        return AnthropicWebSearchProvider(llm, cfg)
    elif name == "apec":
        from providers.search.connectors.apec import APECConnector
        return APECConnector(cfg)
    elif name == "linkedin":
        from providers.search.connectors.linkedin import LinkedInConnector
        return LinkedInConnector(cfg)
    elif name == "indeed":
        from providers.search.connectors.indeed import IndeedConnector
        return IndeedConnector(cfg)
    elif name == "wttj":
        from providers.search.connectors.wttj import WTTJConnector
        return WTTJConnector(cfg)
    else:
        raise ValueError(f"Unknown search provider: '{name}'")
