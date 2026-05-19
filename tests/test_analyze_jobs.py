"""Tests for agent/nodes/analyze_jobs.py — focused on JD truncation, batch scoring, and
the prose fast-fail introduced in the P1 fix (#75)."""
from unittest.mock import MagicMock

from providers.scoring.llm_scorer import _is_prose, _strip_fences, score_jobs_batch


def _make_llm(json_response: str) -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=json_response)
    return llm


def _make_job(title="PM", company="Acme", description="x" * 600) -> dict:
    return {"title": title, "company": company, "location": "Paris",
            "description": description, "job_id": "abc123"}


def _human_prompt(llm: MagicMock) -> str:
    """Return the content of the HumanMessage from the first invoke call.

    score_jobs_batch now sends [SystemMessage, HumanMessage]; the scoring
    content is at index 1.
    """
    return llm.invoke.call_args[0][0][1].content


# ── JD truncation ─────────────────────────────────────────────────────────────

class TestJdTruncation:
    def test_description_truncated_to_1000_in_prompt(self):
        """The LLM prompt must never include more than 1000 chars of job description."""
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 80, "recommendation": "APPLY", "reasoning": "good"}]')
        job = _make_job(description="A" * 2000)  # 2000 chars, should be cut to 1000

        score_jobs_batch(llm, [job], [{"name": "cv1", "content": "PM 10yr"}], {"min_score": 70})

        prompt_sent = _human_prompt(llm)
        assert "A" * 1000 in prompt_sent
        assert "A" * 1001 not in prompt_sent

    def test_short_description_not_padded(self):
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 80, "recommendation": "APPLY", "reasoning": "ok"}]')
        job = _make_job(description="Short desc")

        score_jobs_batch(llm, [job], [{"name": "cv1", "content": "PM"}], {"min_score": 70})

        prompt_sent = _human_prompt(llm)
        assert "Short desc" in prompt_sent


# ── batch scoring logic ───────────────────────────────────────────────────────

class TestScoreJobsBatch:
    def test_passing_jobs_returned(self):
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 85, "recommendation": "APPLY", "reasoning": "strong"}]')
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert len(result) == 1
        assert result[0]["score"] == 85

    def test_below_threshold_filtered(self):
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 60, "recommendation": "SKIP", "reasoning": "weak"}]')
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert result == []

    def test_score_capped_at_max(self):
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 99, "recommendation": "APPLY", "reasoning": "great"}]')
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70, "max_score": 95})
        assert result[0]["score"] == 95

    def test_float_score_accepted(self):
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 82.5, "recommendation": "APPLY", "reasoning": "good"}]')
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert result[0]["score"] == 82

    def test_negative_index_ignored(self):
        llm = _make_llm('[{"job_index": -1, "best_cv": "cv1", "score": 90, "recommendation": "APPLY", "reasoning": "x"}]')
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert result == []

    def test_out_of_bounds_index_ignored(self):
        llm = _make_llm('[{"job_index": 5, "best_cv": "cv1", "score": 90, "recommendation": "APPLY", "reasoning": "x"}]')
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert result == []

    def test_single_call_for_all_jobs(self):
        """All jobs (regardless of count) should produce exactly 1 LLM call on success."""
        llm = _make_llm("[]")
        jobs = [_make_job(title=f"Job {i}") for i in range(12)]
        score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert llm.invoke.call_count == 1

    def test_malformed_llm_response_does_not_crash(self):
        llm = _make_llm("not valid json {{{{")
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert result == []

    def test_system_message_sent_before_human_message(self):
        """score_jobs_batch must include a SystemMessage as the first message."""
        from langchain_core.messages import SystemMessage
        llm = _make_llm("[]")
        score_jobs_batch(llm, [_make_job()], [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        messages = llm.invoke.call_args[0][0]
        assert isinstance(messages[0], SystemMessage)
        assert "JSON" in messages[0].content


# ── prose fast-fail ───────────────────────────────────────────────────────────

class TestProseDetection:
    def test_prose_detected_by_letter_start(self):
        assert _is_prose("Here is a scoring breakdown...") is True

    def test_json_array_not_prose(self):
        assert _is_prose('[{"job_index": 0}]') is False

    def test_json_object_not_prose(self):
        assert _is_prose('{"result": []}') is False

    def test_empty_string_not_prose(self):
        assert _is_prose("") is False

    def test_whitespace_before_bracket_not_prose(self):
        assert _is_prose("  \n[{}]") is False

    def test_prose_triggers_retry(self):
        """When the LLM returns prose, it should trigger one retry call."""
        # First call returns prose; retry call returns empty JSON
        llm = MagicMock()
        llm.invoke.side_effect = [
            MagicMock(content="Here are my scoring thoughts..."),
            MagicMock(content="[]"),
        ]
        result = score_jobs_batch(llm, [_make_job()], [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert result == []
        assert llm.invoke.call_count == 2


# ── _strip_fences ─────────────────────────────────────────────────────────────

class TestStripFences:
    def test_plain_json_unchanged(self):
        assert _strip_fences('[{"a": 1}]') == '[{"a": 1}]'

    def test_json_fence_stripped(self):
        raw = "```json\n[{\"a\": 1}]\n```"
        assert _strip_fences(raw) == '[{"a": 1}]'

    def test_plain_fence_stripped(self):
        raw = "```\n[{\"a\": 1}]\n```"
        assert _strip_fences(raw) == '[{"a": 1}]'
