"""Tests for the Claude Code CLI provider — retry, backoff, JSON parsing.

Covers issue #58: the scorer used to silently drop batches when the CLI hit a
rate limit. These tests verify that the provider now:

  - Uses ``--output-format json`` so error information is recoverable.
  - Retries transient failures with exponential backoff (sleeps between attempts).
  - Aborts immediately on auth errors (no retry budget burned).
  - Falls back to legacy text-mode parsing when stdout isn't valid JSON, so
    historical callers / pre-existing tests keep working.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from providers.llm import usage_tracker
from providers.llm.claude_code_provider import (
    _BACKOFF_SECONDS,
    _backoff_seconds,
    _extract_usage_from_cli_json,
    _invoke_claude_cli,
    _parse_cli_response,
)


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a MagicMock matching the shape of ``subprocess.run``'s return value."""
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _json_success(text: str) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text})


def _json_error(subtype: str, message: str = "") -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": True, "message": message})


# ── _backoff_seconds ────────────────────────────────────────────────────────


class TestBackoffSeconds:
    """The schedule should be 0 on attempt 1, then grow on each retry."""

    def test_attempt_one_has_no_wait(self):
        assert _backoff_seconds(1) == 0.0

    def test_attempt_two_uses_first_backoff_entry(self):
        # 5s base, ±20% jitter → 4.0 .. 6.0
        for _ in range(20):
            v = _backoff_seconds(2)
            assert 4.0 <= v <= 6.0

    def test_attempt_three_uses_second_backoff_entry(self):
        for _ in range(20):
            v = _backoff_seconds(3)
            assert 12.0 <= v <= 18.0

    def test_attempt_four_uses_third_backoff_entry(self):
        for _ in range(20):
            v = _backoff_seconds(4)
            assert 36.0 <= v <= 54.0

    def test_attempts_beyond_schedule_reuse_last_entry(self):
        # Beyond the configured 3 entries, the schedule shouldn't crash —
        # it just keeps using the longest wait.
        last_base = _BACKOFF_SECONDS[-1]
        v = _backoff_seconds(99)
        assert last_base * 0.8 <= v <= last_base * 1.2


# ── _parse_cli_response ─────────────────────────────────────────────────────


class TestParseCliResponse:
    def test_json_success_returns_result_text(self):
        stdout = _json_success("hello world")
        content, err, is_auth = _parse_cli_response(stdout, "", 0)
        assert content == "hello world"
        assert err is None
        assert is_auth is False

    def test_json_error_returns_diagnostic(self):
        stdout = _json_error("rate_limit_exceeded", "too many requests")
        content, err, is_auth = _parse_cli_response(stdout, "", 1)
        assert content is None
        assert err is not None
        assert "rate_limit_exceeded" in err
        assert is_auth is False

    def test_json_auth_error_flagged(self):
        # The auth-hint matcher should fire on the subtype OR the message.
        stdout = _json_error("authentication_error", "invalid api key")
        _, _, is_auth = _parse_cli_response(stdout, "", 1)
        assert is_auth is True

    def test_json_success_with_empty_result_is_treated_as_failure(self):
        # Some "successful" responses still return no content — retry-worthy.
        stdout = json.dumps({"is_error": False, "result": "", "subtype": "success"})
        content, err, is_auth = _parse_cli_response(stdout, "", 0)
        assert content is None
        assert err is not None
        assert is_auth is False

    def test_non_json_stdout_with_zero_exit_succeeds(self):
        # Backward compatibility: old tests / legacy callers that mocked
        # plain-text stdout should still work.
        content, err, _ = _parse_cli_response("plain text", "", 0)
        assert content == "plain text"
        assert err is None

    def test_non_json_stdout_with_nonzero_exit_fails(self):
        content, err, _ = _parse_cli_response("garbage", "some stderr", 1)
        assert content is None
        assert err is not None
        assert "some stderr" in err

    def test_empty_output_with_nonzero_exit_fails_with_marker(self):
        # The observed real-world rate-limit signature: exit 1, empty everywhere.
        content, err, is_auth = _parse_cli_response("", "", 1)
        assert content is None
        assert err is not None
        assert "(no output)" in err
        assert is_auth is False  # Still transient — retry is correct


# ── _invoke_claude_cli ──────────────────────────────────────────────────────


class TestInvokeClaudeCli:
    """End-to-end tests for the retry loop with mocked subprocess + sleep."""

    def _patches(self):
        """Return the standard set of context managers for CLI invocation tests."""
        return [
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("providers.llm.claude_code_provider.subprocess.run"),
            patch("providers.llm.claude_code_provider.time.sleep"),
        ]

    def test_output_format_json_is_always_passed(self):
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(0, _json_success("hi"))
            _invoke_claude_cli("test")
            cmd = mock_run.call_args[0][0]
            assert "--output-format" in cmd
            assert "json" in cmd

    def test_success_on_first_attempt_returns_content(self):
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(0, _json_success("hello"))
            assert _invoke_claude_cli("test") == "hello"
            assert mock_run.call_count == 1

    def test_transient_then_success_uses_backoff(self):
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run, \
             patch("providers.llm.claude_code_provider.time.sleep") as mock_sleep:
            mock_run.side_effect = [
                _mock_run(1, _json_error("rate_limit_exceeded")),
                _mock_run(0, _json_success("ok")),
            ]
            assert _invoke_claude_cli("test") == "ok"
            # Slept exactly once — before the retry, not before the first attempt.
            assert mock_sleep.call_count == 1
            assert mock_sleep.call_args[0][0] > 0

    def test_all_attempts_fail_raises_with_last_error(self):
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run, \
             patch("providers.llm.claude_code_provider.time.sleep"):
            mock_run.return_value = _mock_run(1, _json_error("rate_limit_exceeded", "throttled"))
            with pytest.raises(RuntimeError, match="rate_limit_exceeded"):
                _invoke_claude_cli("test", retries=2)
            assert mock_run.call_count == 3  # 1 initial + 2 retries

    def test_auth_error_aborts_without_retry(self):
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run, \
             patch("providers.llm.claude_code_provider.time.sleep"):
            mock_run.return_value = _mock_run(
                1, _json_error("authentication_error", "invalid api key"),
            )
            with pytest.raises(RuntimeError):
                _invoke_claude_cli("test", retries=5)
            # No retries — auth errors are permanent.
            assert mock_run.call_count == 1

    def test_observed_empty_stdout_failure_retries(self):
        """The real signature from issue #58: exit 1, no stdout, no stderr."""
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run, \
             patch("providers.llm.claude_code_provider.time.sleep") as mock_sleep:
            # First two attempts: the silent rate-limit signature.
            # Third: recovery.
            mock_run.side_effect = [
                _mock_run(1, "", ""),
                _mock_run(1, "", ""),
                _mock_run(0, _json_success("recovered")),
            ]
            assert _invoke_claude_cli("test", retries=2) == "recovered"
            assert mock_run.call_count == 3
            assert mock_sleep.call_count == 2

    def test_legacy_text_stdout_still_succeeds(self):
        """Backwards-compat: a non-JSON success response is still treated as success."""
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(0, "plain text response")
            assert _invoke_claude_cli("test") == "plain text response"


# ── Usage capture (issue #60) ───────────────────────────────────────────────


def _json_success_with_usage(text: str, model: str = "claude-sonnet-4-6") -> str:
    """JSON success blob with the ``usage`` + ``total_cost_usd`` fields the CLI emits."""
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "model": model,
        "usage": {
            "input_tokens": 123,
            "output_tokens": 45,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 200,
        },
        "total_cost_usd": 0.0042,
    })


class TestExtractUsageFromCliJson:
    def test_returns_none_for_empty_stdout(self):
        assert _extract_usage_from_cli_json("") is None

    def test_returns_none_for_non_json(self):
        assert _extract_usage_from_cli_json("plain text not json") is None

    def test_returns_none_when_usage_missing(self):
        stdout = json.dumps({"is_error": False, "result": "hi"})
        assert _extract_usage_from_cli_json(stdout) is None

    def test_returns_canonical_shape(self):
        stdout = _json_success_with_usage("hello")
        extracted = _extract_usage_from_cli_json(stdout)
        assert extracted is not None
        model, usage, cost = extracted
        assert model == "claude-sonnet-4-6"
        assert usage == {
            "input_tokens": 123,
            "output_tokens": 45,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 200,
        }
        assert cost == pytest.approx(0.0042)

    def test_missing_cost_defaults_to_zero(self):
        # Older CLI builds may not include total_cost_usd.
        stdout = json.dumps({
            "is_error": False,
            "result": "hi",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
        extracted = _extract_usage_from_cli_json(stdout)
        assert extracted is not None
        _, _, cost = extracted
        assert cost == 0.0


class TestInvokeClaudeCliRecordsUsage:
    """Verify the CLI invocation records token usage into the singleton tracker."""

    def test_successful_call_records_usage(self):
        usage_tracker.reset()
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(0, _json_success_with_usage("hello"))
            _invoke_claude_cli("test")

        snap = usage_tracker.snapshot()
        assert snap["grand_total"]["calls"] == 1
        assert snap["grand_total"]["input_tokens"] == 123
        assert snap["grand_total"]["output_tokens"] == 45
        # Cost comes straight from the CLI's total_cost_usd, not the price table.
        assert snap["grand_total"]["cost_usd"] == pytest.approx(0.0042)
        assert "claude-sonnet-4-6" in snap["by_model"]
        usage_tracker.reset()

    def test_failed_call_does_not_record(self):
        usage_tracker.reset()
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run, \
             patch("providers.llm.claude_code_provider.time.sleep"):
            mock_run.return_value = _mock_run(1, _json_error("rate_limit_exceeded"))
            with pytest.raises(RuntimeError):
                _invoke_claude_cli("test", retries=1)

        snap = usage_tracker.snapshot()
        # No records — recording is only on successful return.
        assert snap["grand_total"]["calls"] == 0
        usage_tracker.reset()

    def test_legacy_text_response_does_not_record(self):
        """Non-JSON success path is backwards-compatible but skips usage recording."""
        usage_tracker.reset()
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("providers.llm.claude_code_provider.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(0, "plain text response")
            assert _invoke_claude_cli("test") == "plain text response"

        snap = usage_tracker.snapshot()
        # The call succeeded but had no JSON usage block — record was skipped.
        assert snap["grand_total"]["calls"] == 0
        usage_tracker.reset()
