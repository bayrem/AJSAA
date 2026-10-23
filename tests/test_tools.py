"""Tests for the v3 spoke tools and the run store.

Every external dependency (LLM, Tavily, ATS APIs) is patched at its source
module, because the tools import them lazily inside each function.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from agent.run_store import CANDIDATES, DISCARDED, SCORED, RunStore
from agent.tools.cv import convert_cv
from agent.tools.scoring import fetch_posting, score_batch
from agent.tools.search import ats_search, save_candidates, validate_urls
from agent.tools.server import build_tools, tool_names


@pytest.fixture
def store(tmp_path) -> RunStore:
    return RunStore("test-run", root=tmp_path)


# Clearly different titles/companies — near-identical ones would (correctly)
# be collapsed by the semantic dedup in save_candidates.
_TITLES = ["Senior Product Manager", "Head of Data Platform", "AI Product Lead", "Director of Analytics"]
_COMPANIES = ["Mistral", "Criteo", "Doctolib", "Alan"]


def _job(n: int, **extra) -> dict:
    return {
        "title": _TITLES[n % 4],
        "company": _COMPANIES[n % 4],
        "location": "Paris",
        "url": f"https://example.com/jobs/{n}",
        "description": "Lead the data platform roadmap.",
        "source": "test",
        **extra,
    }


# ── Run store ───────────────────────────────────────────────────────────────


def test_run_store_round_trips_jsonl_and_cvs(store):
    store.write_jsonl(CANDIDATES, [{"a": 1}, {"b": "é"}])
    assert store.read_jsonl(CANDIDATES) == [{"a": 1}, {"b": "é"}]
    assert store.read_jsonl("missing.jsonl") == []
    store.save_cv("cv1", "# CV")
    assert store.load_cvs()[0]["name"] == "cv1"


# ── convert_cv ──────────────────────────────────────────────────────────────


class TestConvertCv:
    def test_markdown_is_copied(self, store, tmp_path):
        md = tmp_path / "senior_pm.md"
        md.write_text("# Senior PM", encoding="utf-8")
        assert "senior_pm" in convert_cv(store, str(md))
        assert store.load_cvs()[0]["content"] == "# Senior PM"

    def test_pdf_is_converted(self, store, tmp_path):
        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF")
        with patch("agent.tools.cv.pdf_to_markdown", return_value="extracted text"):
            convert_cv(store, str(pdf))
        assert store.load_cvs()[0]["content"] == "extracted text"

    def test_empty_pdf_text_is_an_error(self, store, tmp_path):
        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF")
        with patch("agent.tools.cv.pdf_to_markdown", return_value="  "), pytest.raises(ValueError, match="scanned"):
            convert_cv(store, str(pdf))

    def test_unsupported_format_rejected(self, store, tmp_path):
        doc = tmp_path / "cv.docx"
        doc.write_text("x")
        with pytest.raises(ValueError, match="Unsupported"):
            convert_cv(store, str(doc))


# ── save_candidates / validate_urls / ats_search ────────────────────────────


class TestSaveCandidates:
    def test_second_round_merges_without_duplicates(self, store):
        save_candidates(store, [_job(1), _job(2)])
        summary = save_candidates(store, [_job(2), _job(3)])
        assert [j["url"] for j in store.read_jsonl(CANDIDATES)] == [
            "https://example.com/jobs/1", "https://example.com/jobs/2", "https://example.com/jobs/3",
        ]
        assert summary.startswith("1 new candidates saved")

    def test_every_candidate_gets_a_job_id(self, store):
        save_candidates(store, [_job(1)])
        assert store.read_jsonl(CANDIDATES)[0]["job_id"]

    def test_stale_postings_dropped(self, store):
        save_candidates(store, [_job(1, description="Posted 2 months ago")])
        assert store.read_jsonl(CANDIDATES) == []

    def test_cap_keeps_earlier_rounds(self, store):
        save_candidates(store, [_job(1), _job(2)], max_jobs=2)
        save_candidates(store, [_job(3)], max_jobs=2)
        assert len(store.read_jsonl(CANDIDATES)) == 2


def test_validate_urls_saves_only_validated_jobs(store):
    with patch("providers.search.url_validator.validate_and_enrich", return_value=[_job(1)]) as v:
        summary = validate_urls(store, {}, [{"url": "https://example.com/jobs/1"}, {"no_url": True}])
    assert v.call_args.args[0] == [{"url": "https://example.com/jobs/1"}]
    assert summary.startswith("1/1 URLs validated")
    assert len(store.read_jsonl(CANDIDATES)) == 1


class TestAtsSearch:
    def test_ats_hints_fetched_and_others_skipped(self, store):
        connector = MagicMock()
        connector.fetch.return_value = [_job(1, company="")]
        with patch("providers.search.connectors.ats.build_ats_connector", return_value=connector):
            summary = ats_search(store, {}, {"HF": "greenhouse:huggingface", "Other": "url:https://x.io"})
        connector.fetch.assert_called_once()
        assert "HF: 1 jobs" in summary and "Other: skipped" in summary
        # Company filled in only when the board left it blank.
        assert store.read_jsonl(CANDIDATES)[0]["company"] == ""

    def test_failing_board_does_not_sink_the_others(self, store):
        bad, good = MagicMock(), MagicMock()
        bad.fetch.side_effect = RuntimeError("503")
        good.fetch.return_value = [_job(2)]
        with patch("providers.search.connectors.ats.build_ats_connector", side_effect=[bad, good]):
            summary = ats_search(store, {}, {"A": "lever:a", "B": "ashby:b"})
        assert "A: failed (503)" in summary and "B: 1 jobs" in summary


# ── score_batch / fetch_posting ─────────────────────────────────────────────


class TestScoreBatch:
    _cfg = {"llm": {"provider": "claude_agent_sdk"}, "scoring": {"min_score": 70, "borderline_margin": 5}}

    def _seed(self, store):
        store.save_cv("cv1", "# CV")
        store.write_jsonl(CANDIDATES, [_job(1, job_id="a"), _job(2, job_id="b"), _job(3, job_id="c")])

    def _run(self, store, passed, discarded):
        with patch("providers.llm.factory.build_llm"), \
             patch("providers.scoring.cv_cache.get_or_compress", return_value="compressed"), \
             patch("providers.scoring.llm_scorer.score_jobs_batch", return_value=(passed, discarded)):
            return score_batch(store, self._cfg)

    def test_writes_sorted_tables_and_flags_borderline(self, store):
        self._seed(store)
        summary = self._run(
            store,
            passed=[{"job_id": "a", "score": 72}, {"job_id": "b", "score": 90}],
            discarded=[{"job_id": "c", "score": 66}],
        )
        assert [j["job_id"] for j in store.read_jsonl(SCORED)] == ["b", "a"]
        assert store.read_jsonl(DISCARDED)[0]["job_id"] == "c"
        assert "2 passed" in summary and "Borderline (within 5 of 70): 2" in summary

    def test_empty_scorer_output_is_an_error_not_all_weak(self, store):
        self._seed(store)
        with pytest.raises(RuntimeError, match="no results"):
            self._run(store, passed=[], discarded=[])

    def test_requires_cvs(self, store):
        store.write_jsonl(CANDIDATES, [_job(1)])
        with pytest.raises(ValueError, match="convert_cv"):
            score_batch(store, self._cfg)


class TestFetchPosting:
    def test_returns_capped_content_for_known_job(self, store):
        store.write_jsonl(CANDIDATES, [_job(1, job_id="a")])
        tavily = MagicMock()
        tavily.return_value.extract.return_value = {"https://example.com/jobs/1": "x" * 20_000}
        with patch("providers.search.connectors.tavily.TavilyConnector", tavily):
            text = fetch_posting(store, {}, "a")
        assert text.startswith("Head of Data Platform @ Criteo")
        assert len(text) < 9_000

    def test_unknown_job_id(self, store):
        with pytest.raises(KeyError):
            fetch_posting(store, {}, "nope")


# ── SDK tool server ─────────────────────────────────────────────────────────


class TestToolServer:
    def test_tool_names_are_namespaced(self, store):
        names = tool_names(build_tools(store, {}))
        assert "mcp__ajsaa__convert_cv" in names
        assert len(names) == 5

    def test_handler_success_and_error_shapes(self, store, tmp_path):
        tools = {t.name: t for t in build_tools(store, {})}
        md = tmp_path / "cv.md"
        md.write_text("# CV", encoding="utf-8")

        ok = asyncio.run(tools["convert_cv"].handler({"cv_path": str(md)}))
        assert "is_error" not in ok and "stored" in ok["content"][0]["text"]

        err = asyncio.run(tools["convert_cv"].handler({"cv_path": str(tmp_path / "missing.md")}))
        assert err["is_error"] is True
        assert "not found" in err["content"][0]["text"]
