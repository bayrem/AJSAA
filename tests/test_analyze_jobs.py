"""Tests for agent/nodes/analyze_jobs.py — focused on JD truncation and batch scoring."""
from unittest.mock import MagicMock

from providers.scoring.llm_scorer import _strip_fences, score_jobs_batch


def _make_llm(json_response: str) -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=json_response)
    return llm


def _make_job(title="PM", company="Acme", description="x" * 600) -> dict:
    return {"title": title, "company": company, "location": "Paris",
            "description": description, "job_id": "abc123"}


# ── JD truncation ─────────────────────────────────────────────────────────────

class TestJdTruncation:
    def test_description_truncated_to_600_in_prompt(self):
        """The LLM prompt must never include more than 600 chars of job description."""
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 80, "recommendation": "APPLY", "reasoning": "good"}]')
        job = _make_job(description="A" * 1200)  # 1200 chars, should be cut to 600

        score_jobs_batch(llm, [job], [{"name": "cv1", "content": "PM 10yr"}], {"min_score": 70})

        prompt_sent = llm.invoke.call_args[0][0][0].content
        # 600 A's should appear, but not 601
        assert "A" * 600 in prompt_sent
        assert "A" * 601 not in prompt_sent

    def test_short_description_not_padded(self):
        llm = _make_llm('[{"job_index": 0, "best_cv": "cv1", "score": 80, "recommendation": "APPLY", "reasoning": "ok"}]')
        job = _make_job(description="Short desc")

        score_jobs_batch(llm, [job], [{"name": "cv1", "content": "PM"}], {"min_score": 70})

        prompt_sent = llm.invoke.call_args[0][0][0].content
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
        """All jobs (regardless of count) should produce exactly 1 LLM call."""
        llm = _make_llm("[]")
        jobs = [_make_job(title=f"Job {i}") for i in range(12)]
        score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert llm.invoke.call_count == 1

    def test_malformed_llm_response_does_not_crash(self):
        llm = _make_llm("not valid json {{{{")
        jobs = [_make_job()]
        result = score_jobs_batch(llm, jobs, [{"name": "cv1", "content": "PM"}], {"min_score": 70})
        assert result == []


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
