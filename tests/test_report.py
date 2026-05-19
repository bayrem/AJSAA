"""Tests for the post-run HTML report generator.

Covers issue #61's rendering acceptance criteria:
  - Token-spend block renders grand total, per-model table, per-node details
  - Graceful when ``token_usage`` is empty ({}) or missing entirely
  - Unknown model names are rendered as-is (no crash, escaped for safety)
  - ``update_index`` migrates pre-#61 index files (adds Cost column to
    legacy rows so the new header lines up)

Tests write to a tmp_path-scoped CWD so they don't touch the real ``logs/``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from monitoring.monitoring_core.formatters import fmt_cost, fmt_tokens
from monitoring.web_monitoring import report


@pytest.fixture
def in_tmp_cwd(tmp_path: Path):
    """Run each test in an isolated working directory.

    ``scripts.report`` writes to relative paths (``logs/``, ``logs/runs/``).
    Pinning CWD to ``tmp_path`` keeps every test hermetic and lets us assert
    on file contents without racing real run artefacts.
    """
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


def _state_with_tokens() -> dict:
    """Realistic final state covering two models and two nodes."""
    return {
        "run_id": "abc12345",
        "timestamp": "2026-05-17 10:00 UTC",
        "stored_count": 3,
        "scored_jobs": [],
        "errors": [],
        "token_usage": {
            "by_model": {
                "claude-sonnet-4-6": {
                    "input_tokens": 8000,
                    "output_tokens": 1500,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cost_usd": 0.31,
                    "calls": 6,
                },
                "claude-haiku-4-5-20251001": {
                    "input_tokens": 3500,
                    "output_tokens": 800,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cost_usd": 0.11,
                    "calls": 2,
                },
            },
            "by_node": {
                "analyze_jobs": {
                    "input_tokens": 9000,
                    "output_tokens": 1800,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cost_usd": 0.36,
                    "calls": 6,
                },
                "generate_queries": {
                    "input_tokens": 2500,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cost_usd": 0.06,
                    "calls": 2,
                },
            },
            "grand_total": {
                "input_tokens": 11500,
                "output_tokens": 2300,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cost_usd": 0.42,
                "calls": 8,
            },
        },
    }


class TestTokenBlockHtml:
    def test_full_state_renders_grand_total(self):
        html = report._token_block_html(_state_with_tokens()["token_usage"])
        assert "Token spend" in html
        assert "$0.42" in html
        assert "11,500 new in" in html  # exact number in grand total line
        assert "2,300 out" in html
        assert "8 calls" in html

    def test_full_state_renders_per_model_table(self):
        html = report._token_block_html(_state_with_tokens()["token_usage"])
        assert "By model" in html
        assert "claude-sonnet-4-6" in html
        assert "claude-haiku-4-5-20251001" in html
        # Sonnet costs more so it must appear before haiku.
        assert html.index("claude-sonnet-4-6") < html.index("claude-haiku-4-5-20251001")

    def test_full_state_renders_per_node_details(self):
        html = report._token_block_html(_state_with_tokens()["token_usage"])
        assert "<details" in html
        assert "By node" in html
        assert "analyze_jobs" in html
        assert "generate_queries" in html

    def test_effective_compute_shown_when_cache_present(self):
        usage = {
            "grand_total": {
                "input_tokens": 36,
                "output_tokens": 1199,
                "cache_read_input_tokens": 138922,
                "cache_creation_input_tokens": 36285,
                "cost_usd": 0.07,
                "calls": 3,
            },
            "by_model": {},
            "by_node": {},
        }
        html = report._token_block_html(usage)
        # effective = 36 + 1199 + round(138922 * 0.1) = 36 + 1199 + 13892 = 15127 → "15k"
        assert "effective compute" in html
        assert "15k" in html

    def test_no_effective_compute_without_cache(self):
        html = report._token_block_html(_state_with_tokens()["token_usage"])
        # fixture has zero cache tokens → no effective compute line
        assert "effective compute" not in html

    def test_node_row_shows_in_out_cached_detail(self):
        # Pipeline table must show per-bucket breakdown, not a single total.
        node_data = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 5000,
            "cache_creation_input_tokens": 2000,
            "cost_usd": 0.04,
            "calls": 1,
        }
        html = report._node_row_html("search_jobs", {"search_jobs": 3.2}, {"search_jobs": node_data})
        assert "100 in" in html
        assert "50 out" in html
        # cache-read shown in green
        assert "5.0k cached" in html

    def test_node_row_no_cached_label_when_zero(self):
        node_data = {
            "input_tokens": 200,
            "output_tokens": 80,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cost_usd": 0.01,
            "calls": 1,
        }
        html = report._node_row_html("analyze_jobs", {"analyze_jobs": 1.5}, {"analyze_jobs": node_data})
        assert "200 in" in html
        assert "80 out" in html
        assert "cached" not in html

    def test_empty_token_usage_renders_placeholder(self):
        # Issue #61 acceptance: empty data must render gracefully, not crash.
        html = report._token_block_html({})
        assert "Token spend" in html
        assert "no LLM calls" in html
        assert "<table>" not in html

    def test_missing_token_usage_key_in_state(self, in_tmp_cwd):
        # ``generate_run_report`` must tolerate state without the key at all.
        state = {
            "run_id": "deadbeef",
            "timestamp": "2026-05-17 10:00 UTC",
            "stored_count": 0,
            "scored_jobs": [],
            "errors": [],
        }
        out = report.generate_run_report(state, duration_s=12.3, node_timings={})
        content = out.read_text(encoding="utf-8")
        assert "Token spend" in content
        assert "no LLM calls" in content

    def test_unknown_model_name_rendered_verbatim(self):
        # Pricing returns $0.00 for unknown models; the report still renders.
        usage = {
            "by_model": {
                "mystery-model-v9": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cost_usd": 0.0,
                    "calls": 1,
                },
            },
            "by_node": {},
            "grand_total": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.0,
                "calls": 1,
            },
        }
        html = report._token_block_html(usage)
        assert "mystery-model-v9" in html
        # Cost of $0.00 still renders (don't drop the row just because cost is 0).
        assert "$0.00" in html

    def test_html_escapes_malicious_model_name(self):
        usage = {
            "by_model": {
                '<script>alert("x")</script>': {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cost_usd": 0.0,
                    "calls": 1,
                },
            },
            "by_node": {},
            "grand_total": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0, "calls": 1},
        }
        html = report._token_block_html(usage)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestGenerateRunReport:
    def test_full_report_includes_token_block(self, in_tmp_cwd):
        state = _state_with_tokens()
        out = report.generate_run_report(state, duration_s=42.5, node_timings={"load_context": 1.2})
        content = out.read_text(encoding="utf-8")
        assert "AJSAA — Run abc12345" in content
        assert "Token spend" in content
        assert "$0.42" in content
        assert "claude-sonnet-4-6" in content

    def test_empty_token_usage_does_not_crash(self, in_tmp_cwd):
        state = _state_with_tokens()
        state["token_usage"] = {}
        out = report.generate_run_report(state, duration_s=1.0, node_timings={})
        content = out.read_text(encoding="utf-8")
        assert "Token spend" in content
        assert "no LLM calls" in content


class TestUpdateIndex:
    def test_fresh_index_has_correct_columns(self, in_tmp_cwd):
        # update_index rebuilds from runs.json — append first so the run appears.
        stats = {"queries": 5, "found": 10, "passed": 6, "new_saved": 3, "errors": 0,
                 "cost_usd": 0.42, "tokens_total": 14000}
        report.append_runs_json("abc12345", "2026-05-17 10:00 UTC", 42.5, stats)
        report.update_index("abc12345", "2026-05-17 10:00 UTC", 42.5, stats)

        content = (in_tmp_cwd / "logs" / "index.html").read_text(encoding="utf-8")
        assert "<th>Cost $</th>" in content
        assert "<th>Tokens consumed</th>" in content
        assert "<th>Status</th>" in content
        assert "$0.42" in content
        assert "14k" in content
        assert "abc12345" in content
        assert "✓ success" in content

    def test_missing_cost_and_tokens_render_em_dash(self, in_tmp_cwd):
        # Stats without cost_usd or tokens_total — both columns must show —.
        stats = {"queries": 5, "found": 10, "passed": 6, "new_saved": 3, "errors": 0}
        report.append_runs_json("abc12345", "2026-05-17 10:00 UTC", 42.5, stats)
        report.update_index("abc12345", "2026-05-17 10:00 UTC", 42.5, stats)

        content = (in_tmp_cwd / "logs" / "index.html").read_text(encoding="utf-8")
        assert "<th>Cost $</th>" in content
        # Both token and cost cells render as em-dash; row ends there (no extra link cell).
        assert "<td>—</td><td>—</td></tr>" in content

    def test_run_with_errors_shows_failed_status(self, in_tmp_cwd):
        stats = {"queries": 2, "found": 5, "passed": 0, "new_saved": 0, "errors": 1,
                 "cost_usd": 0.10, "tokens_total": 5000}
        report.append_runs_json("err12345", "2026-05-18 09:00 UTC", 30.0, stats)
        report.update_index("err12345", "2026-05-18 09:00 UTC", 30.0, stats)

        content = (in_tmp_cwd / "logs" / "index.html").read_text(encoding="utf-8")
        assert "✗ failed" in content
        assert "err12345" in content

    def test_repeated_runs_all_appear_once(self, in_tmp_cwd):
        # Rebuild-from-runs.json means all runs accumulate; header appears once.
        for i, run_id in enumerate(("run00001", "run00002", "run00003")):
            stats = {"queries": 1, "found": 1, "passed": 1, "new_saved": 1,
                     "errors": 0, "cost_usd": 0.05, "tokens_total": 1000 * (i + 1)}
            report.append_runs_json(run_id, "2026-05-17 10:00 UTC", 10.0, stats)
            report.update_index(run_id, "2026-05-17 10:00 UTC", 10.0, stats)

        content = (in_tmp_cwd / "logs" / "index.html").read_text(encoding="utf-8")
        assert content.count("<th>Cost $</th>") == 1
        assert "run00001" in content
        assert "run00002" in content
        assert "run00003" in content


class TestFormatHelpers:
    @pytest.mark.parametrize("n,expected", [
        (0, "0"),
        (999, "999"),
        (1000, "1.0k"),
        (1500, "1.5k"),
        (9999, "10.0k"),
        (14200, "14k"),
        (150_000, "150k"),
    ])
    def test_fmt_tokens(self, n, expected):
        assert fmt_tokens(n) == expected

    @pytest.mark.parametrize("cost,expected", [
        (0.0, "$0.00"),
        (0.0009, "$0.0009"),
        (0.42, "$0.42"),
        (12.345, "$12.35"),
    ])
    def test_fmt_cost(self, cost, expected):
        assert fmt_cost(cost) == expected
