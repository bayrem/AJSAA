"""Tests for task-aware model routing in providers/llm/factory.py."""
from unittest.mock import MagicMock, patch

from providers.llm.factory import build_llm


def _cfg(extra: dict | None = None) -> dict:
    return {
        "provider": "anthropic",
        "scoring_model": "claude-sonnet-4-6",
        "search_model": "claude-haiku-4-5-20251001",
        "default_model": "claude-haiku-4-5-20251001",
        **(extra or {}),
    }


def _mock_provider(captured: list):
    """Return a mock AnthropicProvider class that records the model it was built with."""
    class FakeProvider:
        def __init__(self, cfg):
            captured.append(cfg.get("model"))

        def build(self):
            return MagicMock()

    return FakeProvider


class TestBuildLlmModelResolution:
    def _build(self, task, cfg_extra: dict | None = None):
        captured = []
        with patch("providers.llm.anthropic_provider.AnthropicProvider", _mock_provider(captured)):
            build_llm(_cfg(cfg_extra or {}), task=task)
        return captured[0]

    def test_scoring_task_uses_scoring_model(self):
        assert self._build("scoring") == "claude-sonnet-4-6"

    def test_search_task_uses_search_model(self):
        assert self._build("search") == "claude-haiku-4-5-20251001"

    def test_default_task_uses_default_model(self):
        assert self._build("default") == "claude-haiku-4-5-20251001"

    def test_unknown_task_falls_back_to_default_model(self):
        assert self._build("unknown_task") == "claude-haiku-4-5-20251001"

    def test_missing_task_model_falls_back_to_default_model(self):
        # No search_model key — should land on default_model
        cfg = _cfg()
        del cfg["search_model"]  # type: ignore[misc]
        captured = []
        with patch("providers.llm.anthropic_provider.AnthropicProvider", _mock_provider(captured)):
            build_llm(cfg, task="search")
        assert captured[0] == "claude-haiku-4-5-20251001"

    def test_missing_default_model_falls_back_to_legacy_model_key(self):
        cfg = {"provider": "anthropic", "model": "claude-opus-4-7"}
        captured = []
        with patch("providers.llm.anthropic_provider.AnthropicProvider", _mock_provider(captured)):
            build_llm(cfg, task="scoring")
        assert captured[0] == "claude-opus-4-7"

    def test_original_cfg_not_mutated(self):
        cfg = _cfg()
        original_model = cfg.get("model")
        with patch("providers.llm.anthropic_provider.AnthropicProvider", _mock_provider([])):
            build_llm(cfg, task="scoring")
        assert cfg.get("model") == original_model


class TestClaudeCodeProviderModelFlag:
    def test_model_passed_to_cli(self):

        from providers.llm.claude_code_provider import _invoke_claude_cli

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="result", stderr="")
            _invoke_claude_cli("hello", model="claude-haiku-4-5-20251001")
            cmd = mock_run.call_args[0][0]
            assert "--model" in cmd
            assert "claude-haiku-4-5-20251001" in cmd

    def test_no_model_flag_when_model_empty(self):
        from providers.llm.claude_code_provider import _invoke_claude_cli

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="result", stderr="")
            _invoke_claude_cli("hello", model="")
            cmd = mock_run.call_args[0][0]
            assert "--model" not in cmd
