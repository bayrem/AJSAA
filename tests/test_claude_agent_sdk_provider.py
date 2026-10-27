"""Tests for the Claude Agent SDK provider — billing guard, options, retries.

No real SDK calls: ``_collect_result`` (the only function that talks to the
SDK) is replaced by an async fake returning a duck-typed ResultMessage.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from providers.llm import usage_tracker
from providers.llm.claude_agent_sdk_provider import (
    API_KEY_SECRET,
    DEFAULT_SYSTEM_PROMPT,
    SEARCH_TOOLS,
    ClaudeAgentSDKChatModel,
    build_options,
    build_sdk_env,
    run_blocking,
    run_prompt,
    split_messages,
)

_MODULE = "providers.llm.claude_agent_sdk_provider"


def _result(
    text: str = "ok", is_error: bool = False, errors=None, cost: float = 0.01, model_usage=None,
) -> SimpleNamespace:
    """Duck-typed stand-in for claude_agent_sdk.ResultMessage."""
    return SimpleNamespace(
        result=text,
        is_error=is_error,
        errors=errors,
        subtype="error_during_execution" if is_error else "success",
        api_error_status=None,
        total_cost_usd=cost,
        usage={"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 5},
        model_usage=model_usage,
    )


def _fake_collect(*results):
    """Return an async replacement for _collect_result yielding ``results`` in order."""
    queue = list(results)

    async def fake(prompt, options):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return fake


@pytest.fixture(autouse=True)
def _reset_tracker():
    usage_tracker.reset()
    yield
    usage_tracker.reset()


# ── Billing guard ───────────────────────────────────────────────────────────


class TestBuildSdkEnv:
    def test_api_mode_passes_key_under_standard_name(self, monkeypatch):
        monkeypatch.setenv(API_KEY_SECRET, "sk-test")
        assert build_sdk_env("api") == {"ANTHROPIC_API_KEY": "sk-test"}

    def test_api_mode_without_secret_fails_fast(self, monkeypatch):
        monkeypatch.delenv(API_KEY_SECRET, raising=False)
        with pytest.raises(RuntimeError, match=API_KEY_SECRET):
            build_sdk_env("api")

    def test_subscription_mode_blanks_inherited_api_key(self, monkeypatch):
        # An inherited key would silently switch the run to API billing.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leaked")
        assert build_sdk_env("subscription") == {"ANTHROPIC_API_KEY": ""}

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            build_sdk_env("free")


# ── Options ─────────────────────────────────────────────────────────────────


class TestBuildOptions:
    def _opts(self, allow_tools: bool):
        return build_options(model="m", system_prompt="s", allow_tools=allow_tools, max_turns=7, billing="subscription")

    def test_search_gets_only_web_tools(self):
        opts = self._opts(True)
        assert opts.tools == SEARCH_TOOLS
        assert opts.allowed_tools == SEARCH_TOOLS
        assert opts.max_turns == 7

    def test_non_search_gets_no_tools(self):
        opts = self._opts(False)
        assert opts.tools == []
        assert opts.max_turns is None

    def test_user_settings_never_loaded(self):
        # [] — not None — is what stops ~/.claude settings, CLAUDE.md and hooks loading.
        assert self._opts(False).setting_sources == []
        assert self._opts(False).skills == []

    def test_only_explicit_mcp_servers_load(self):
        # Without strict mode a subscription login loads every claude.ai
        # connector — ~51k extra tokens per Sonnet call (measured).
        assert self._opts(False).strict_mcp_config is True


# ── Message splitting ───────────────────────────────────────────────────────


class TestSplitMessages:
    def test_system_message_becomes_system_prompt(self):
        system, prompt = split_messages([SystemMessage(content="be terse"), HumanMessage(content="hi")])
        assert system == "be terse"
        assert prompt == "hi"

    def test_default_system_prompt_when_none_given(self):
        system, _ = split_messages([HumanMessage(content="hi")])
        assert system == DEFAULT_SYSTEM_PROMPT

    def test_prior_ai_turn_is_labelled(self):
        _, prompt = split_messages([HumanMessage(content="q"), AIMessage(content="a"), HumanMessage(content="retry")])
        assert prompt == "q\n\n[Assistant]\na\n\nretry"


# ── run_prompt ──────────────────────────────────────────────────────────────


class TestRunPrompt:
    def _run(self, *results):
        with patch(f"{_MODULE}._collect_result", _fake_collect(*results)), patch(f"{_MODULE}.time.sleep") as sleep:
            text = run_prompt("p", options=None, timeout=5, model_label="claude-test")
        return text, sleep

    def test_success_returns_text_and_records_usage(self):
        text, _ = self._run(_result("hello", cost=0.25))
        assert text == "hello"
        snap = usage_tracker.snapshot()
        assert snap["by_model"]["claude-test"]["input_tokens"] == 100
        assert snap["grand_total"]["cost_usd"] == pytest.approx(0.25)

    def test_per_model_breakdown_preferred_over_aggregate(self):
        # Claude Code's own housekeeping call on a small model shows up separately.
        self._run(_result(model_usage={
            "claude-sonnet-4-6": {"inputTokens": 900, "outputTokens": 50, "costUSD": 0.02},
            "claude-haiku-5-5": {"inputTokens": 1200, "outputTokens": 10, "costUSD": 0.0001},
        }))
        by_model = usage_tracker.snapshot()["by_model"]
        assert set(by_model) == {"claude-sonnet-4-6", "claude-haiku-5-5"}
        assert by_model["claude-sonnet-4-6"]["input_tokens"] == 900
        assert usage_tracker.snapshot()["grand_total"]["cost_usd"] == pytest.approx(0.0201)

    def test_transient_failure_is_retried_with_backoff(self):
        text, sleep = self._run(RuntimeError("rate limited"), _result("second time"))
        assert text == "second time"
        assert sleep.call_count == 1

    def test_error_result_is_retried(self):
        text, _ = self._run(_result(is_error=True, errors=["overloaded"]), _result("ok"))
        assert text == "ok"

    def test_auth_error_aborts_without_retry(self):
        with patch(f"{_MODULE}._collect_result", _fake_collect(RuntimeError("Not logged in"), _result())), \
             patch(f"{_MODULE}.time.sleep") as sleep:
            with pytest.raises(RuntimeError, match="Not logged in"):
                run_prompt("p", options=None, timeout=5, model_label="m")
        assert sleep.call_count == 0

    def test_all_attempts_failing_raises(self):
        with patch(f"{_MODULE}._collect_result", _fake_collect(*[_result("")] * 3)), patch(f"{_MODULE}.time.sleep"):
            with pytest.raises(RuntimeError, match="empty result"):
                run_prompt("p", options=None, timeout=5, model_label="m")

    def test_missing_cli_is_permanent(self):
        from claude_agent_sdk import CLINotFoundError

        with patch(f"{_MODULE}._collect_result", _fake_collect(CLINotFoundError("gone"), _result())):
            with pytest.raises(RuntimeError, match="not available"):
                run_prompt("p", options=None, timeout=5, model_label="m")


# ── Async bridging and LangChain shim ───────────────────────────────────────


def test_run_blocking_works_inside_a_running_event_loop():
    # M2 tool handlers run inside the SDK's event loop and call sync code.
    async def inner() -> int:
        return 42

    async def outer() -> int:
        return run_blocking(inner())

    assert asyncio.run(outer()) == 42


def test_chat_model_invoke_round_trip():
    model = ClaudeAgentSDKChatModel(model="claude-test", billing="subscription")
    with patch(f"{_MODULE}._collect_result", _fake_collect(_result("scored"))):
        response = model.invoke([SystemMessage(content="json only"), HumanMessage(content="score these")])
    assert response.content == "scored"


def test_factory_routes_claude_agent_sdk():
    from providers.llm.factory import build_llm

    llm = build_llm({"provider": "claude_agent_sdk", "search_model": "claude-test"}, task="search")
    assert isinstance(llm, ClaudeAgentSDKChatModel)
    assert llm.allow_tools is True
    assert llm.model == "claude-test"
