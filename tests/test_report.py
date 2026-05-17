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

from scripts import report


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
        assert "11,500 in" in html
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
    def test_fresh_index_has_cost_column(self, in_tmp_cwd):
        report.update_index(
            "abc12345",
            "2026-05-17 10:00 UTC",
            42.5,
            {"queries": 5, "found": 10, "passed": 6, "new_saved": 3, "errors": 0, "cost_usd": 0.42},
        )
        content = (in_tmp_cwd / "logs" / "index.html").read_text(encoding="utf-8")
        assert "<th>Cost</th>" in content
        assert "$0.42" in content
        assert "abc12345" in content

    def test_missing_cost_renders_em_dash(self, in_tmp_cwd):
        # cost_usd missing from stats — e.g. a caller that doesn't know about
        # the new column. We render '—' so the column stays present.
        report.update_index(
            "abc12345",
            "2026-05-17 10:00 UTC",
            42.5,
            {"queries": 5, "found": 10, "passed": 6, "new_saved": 3, "errors": 0},
        )
        content = (in_tmp_cwd / "logs" / "index.html").read_text(encoding="utf-8")
        # The Cost column must still be present (header) and the row must have a placeholder.
        assert "<th>Cost</th>" in content
        # Last data cell before the link must be the em-dash.
        assert "<td>—</td><td><a" in content

    def test_legacy_index_is_migrated(self, in_tmp_cwd):
        # Simulate a pre-#61 index.html: legacy header + a row without a Cost cell.
        logs_dir = in_tmp_cwd / "logs"
        logs_dir.mkdir()
        legacy = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>AJSAA Runs</title></head>
<body>
<h1>AJSAA — All Runs</h1>
<table>
<thead><tr><th>Run ID</th><th>Date</th><th>Duration</th><th>Queries</th><th>Found</th><th>Passed</th><th>New saved</th><th>Errors</th><th></th></tr></thead>
<tbody>
<tr><td>oldrun01</td><td>2026-05-01 09:00 UTC</td><td>30s</td><td>4</td><td>8</td><td>5</td><td>2</td><td>0</td><td><a href="#">→</a></td></tr>
<!-- ROWS -->
</tbody>
</table>
</body>
</html>"""
        (logs_dir / "index.html").write_text(legacy, encoding="utf-8")

        report.update_index(
            "abc12345",
            "2026-05-17 10:00 UTC",
            42.5,
            {"queries": 5, "found": 10, "passed": 6, "new_saved": 3, "errors": 0, "cost_usd": 0.42},
        )

        content = (logs_dir / "index.html").read_text(encoding="utf-8")
        # New header has Cost column.
        assert "<th>Cost</th>" in content
        # Legacy row got an em-dash inserted in the Cost slot.
        assert "oldrun01" in content
        assert "<td>—</td><td><a href=\"#\">→</a></td>" in content
        # New row appears with its actual cost.
        assert "$0.42" in content
        # Insertion order is preserved: legacy row stays where it was; the new
        # row is inserted just before the marker (i.e. after the legacy row in
        # the file, since the marker sits at the bottom of <tbody>).
        assert content.index("oldrun01") < content.index("abc12345")

    def test_repeated_writes_do_not_double_migrate(self, in_tmp_cwd):
        # Once the header has Cost, further writes must NOT add more <th>Cost</th>.
        for run_id in ("run00001", "run00002", "run00003"):
            report.update_index(
                run_id,
                "2026-05-17 10:00 UTC",
                10.0,
                {"queries": 1, "found": 1, "passed": 1, "new_saved": 1, "errors": 0, "cost_usd": 0.05},
            )
        content = (in_tmp_cwd / "logs" / "index.html").read_text(encoding="utf-8")
        assert content.count("<th>Cost</th>") == 1


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
        assert report._fmt_tokens(n) == expected

    @pytest.mark.parametrize("cost,expected", [
        (0.0, "$0.00"),
        (0.0009, "$0.0009"),
        (0.42, "$0.42"),
        (12.345, "$12.35"),
    ])
    def test_fmt_cost(self, cost, expected):
        assert report._fmt_cost(cost) == expected
