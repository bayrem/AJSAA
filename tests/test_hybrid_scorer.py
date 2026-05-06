"""Tests for providers/scoring/hybrid_scorer.py"""
import json
from unittest.mock import MagicMock, patch

from providers.scoring.hybrid_scorer import HybridScorer, _extract_profile, _strip_json


def _job(job_id="j1", title="PM", score=75):
    return {
        "job_id": job_id,
        "title": title,
        "company": "Acme",
        "description": "data platform role",
        "score": score,
        "summary": "good match",
    }


def _cv(name="cv1", content="10 years PM data platform"):
    return {"name": name, "content": content}


def _profile(cv_name="cv1", cv_hash=None):
    from providers.scoring.profile_store import content_hash
    return {
        "cv": cv_name,
        "cv_hash": cv_hash or content_hash("10 years PM data platform"),
        "positive_signals": [{"pattern": "data platform", "weight": 30}],
        "negative_signals": [{"pattern": "junior", "weight": -50}],
        "domain_bonus": {},
        "uncertainty_band": [60, 80],
    }


def _llm_returning(payload):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=json.dumps(payload))
    return llm


class TestStripJson:
    def test_plain_json_unchanged(self):
        assert _strip_json('{"a": 1}') == '{"a": 1}'

    def test_fenced_json_stripped(self):
        assert _strip_json('```json\n{"a": 1}\n```') == '{"a": 1}'


class TestExtractProfile:
    def test_returns_parsed_profile(self, tmp_path):
        cv = _cv()
        profile_payload = _profile()
        llm = _llm_returning(profile_payload)
        result = _extract_profile(llm, cv, [_job()])
        assert result["cv"] == "cv1"
        assert "positive_signals" in result

    def test_llm_failure_returns_empty_profile(self):
        cv = _cv()
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("API error")
        result = _extract_profile(llm, cv, [_job()])
        assert result["cv"] == "cv1"
        assert result["positive_signals"] == []


class TestHybridScorer:
    def _make_scorer(self, llm, profiles_dir, cv=None, scoring_cfg=None):
        cv = cv or _cv()
        cfg = scoring_cfg or {
            "min_score": 70,
            "max_score": 95,
            "uncertainty_band": [60, 80],
            "profiles_dir": str(profiles_dir),
        }
        return HybridScorer(llm, [cv], [{"name": cv["name"], "content": cv["content"]}], cfg)

    def test_bootstraps_when_no_profile(self, tmp_path):
        """First run: calls LLM for scoring AND profile extraction."""
        profile_payload = _profile()

        llm = MagicMock()
        # First call: score_jobs_batch result, second call: extract_profile result
        llm.invoke.return_value = MagicMock(
            content=json.dumps([
                {"job_index": 0, "best_cv": "cv1", "score": 85,
                 "recommendation": "APPLY", "reasoning": "good"}
            ])
        )

        raw_jobs = [{"job_id": "j1", "title": "PM", "company": "Acme",
                     "description": "data platform role"}]

        with patch("providers.scoring.hybrid_scorer._extract_profile", return_value=profile_payload):
            scorer = self._make_scorer(llm, tmp_path)
            scorer.score(raw_jobs)

        # Profile should be saved
        assert (tmp_path / "cv1.json").exists()

    def test_uses_static_when_profile_exists(self, tmp_path):
        """Second run: no LLM calls for scoring when profile is valid."""
        from providers.scoring.profile_store import content_hash, save_profile
        # weight=40 → score=90, clearly above band_hi=80, so no LLM escalation
        profile = {
            "cv": "cv1",
            "cv_hash": content_hash("10 years PM data platform"),
            "positive_signals": [{"pattern": "data platform", "weight": 40}],
            "negative_signals": [],
            "domain_bonus": {},
            "uncertainty_band": [60, 80],
        }
        save_profile(profile, str(tmp_path))

        llm = MagicMock()
        raw_jobs = [{"job_id": "j1", "title": "PM", "company": "Acme",
                     "description": "data platform role"}]

        scorer = self._make_scorer(llm, tmp_path)
        scorer.score(raw_jobs)

        # LLM should not have been called (score is above band_hi=80)
        llm.invoke.assert_not_called()

    def test_escalates_borderline_to_llm(self, tmp_path):
        """Jobs in the uncertainty band are re-scored by LLM."""
        from providers.scoring.profile_store import content_hash, save_profile

        # Profile with weak signals so job scores ~55 (in band [60,80]... wait need score IN band)
        # Let's set uncertainty_band to [50, 90] to catch most jobs
        profile = {
            "cv": "cv1",
            "cv_hash": content_hash("10 years PM data platform"),
            "positive_signals": [{"pattern": "data platform", "weight": 20}],  # 50+20=70
            "negative_signals": [],
            "domain_bonus": {},
            "uncertainty_band": [60, 80],
        }
        save_profile(profile, str(tmp_path))

        llm = MagicMock()
        # LLM re-scores the borderline job
        llm.invoke.return_value = MagicMock(
            content=json.dumps([
                {"job_index": 0, "best_cv": "cv1", "score": 75,
                 "recommendation": "CONSIDER", "reasoning": "borderline"}
            ])
        )

        raw_jobs = [{"job_id": "j1", "title": "PM", "company": "Acme",
                     "description": "data platform role"}]

        scoring_cfg = {
            "min_score": 0,
            "max_score": 95,
            "uncertainty_band": [60, 80],
            "profiles_dir": str(tmp_path),
        }
        scorer = HybridScorer(
            llm, [_cv()], [{"name": "cv1", "content": "10 years PM data platform"}], scoring_cfg
        )
        scorer.score(raw_jobs)

        # LLM was called to re-score the borderline job
        llm.invoke.assert_called_once()

    def test_stale_profile_triggers_bootstrap(self, tmp_path):
        """CV content changed → profile invalidated → LLM bootstrap runs."""
        from providers.scoring.profile_store import save_profile

        stale_profile = {
            "cv": "cv1",
            "cv_hash": "old_hash_that_wont_match",
            "positive_signals": [],
            "negative_signals": [],
            "domain_bonus": {},
            "uncertainty_band": [60, 80],
        }
        save_profile(stale_profile, str(tmp_path))

        llm = MagicMock()
        llm.invoke.return_value = MagicMock(
            content=json.dumps([
                {"job_index": 0, "best_cv": "cv1", "score": 80,
                 "recommendation": "APPLY", "reasoning": "good"}
            ])
        )

        raw_jobs = [{"job_id": "j1", "title": "PM", "company": "Acme",
                     "description": "data platform role"}]
        fresh_profile = _profile()

        with patch("providers.scoring.hybrid_scorer._extract_profile", return_value=fresh_profile):
            scorer = self._make_scorer(llm, tmp_path)
            scorer.score(raw_jobs)

        # LLM was called for bootstrap
        assert llm.invoke.called
