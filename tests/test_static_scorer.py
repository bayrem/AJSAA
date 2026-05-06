"""Tests for providers/scoring/static_scorer.py"""
from providers.scoring.static_scorer import StaticScorer, score_jobs_static

_PROFILE = {
    "positive_signals": [
        {"pattern": "data platform", "weight": 25},
        {"pattern": "mlops|airflow", "weight": 20},
    ],
    "negative_signals": [
        {"pattern": "junior|internship|alternance", "weight": -50},
        {"pattern": "consulting", "weight": -10},
    ],
    "domain_bonus": {
        "ai|ml|llm": 15,
    },
}


def _job(title="PM", description=""):
    return {"job_id": "abc", "title": title, "company": "Acme", "description": description}


class TestStaticScorer:
    def test_baseline_score_is_50(self):
        scorer = StaticScorer({"positive_signals": [], "negative_signals": [], "domain_bonus": {}})
        assert scorer.score(_job()) == 50

    def test_positive_signal_increases_score(self):
        scorer = StaticScorer(_PROFILE)
        job = _job(description="data platform engineering role")
        assert scorer.score(job) > 50

    def test_negative_signal_decreases_score(self):
        scorer = StaticScorer(_PROFILE)
        job = _job(title="Junior Data Engineer", description="internship position")
        assert scorer.score(job) < 50

    def test_score_clamped_to_zero(self):
        profile = {
            "positive_signals": [],
            "negative_signals": [{"pattern": "anything", "weight": -200}],
            "domain_bonus": {},
        }
        scorer = StaticScorer(profile)
        assert scorer.score(_job(description="anything")) == 0

    def test_score_clamped_to_95(self):
        profile = {
            "positive_signals": [{"pattern": "x", "weight": 200}],
            "negative_signals": [],
            "domain_bonus": {},
        }
        scorer = StaticScorer(profile)
        assert scorer.score(_job(description="x")) == 95

    def test_domain_bonus_applied(self):
        scorer = StaticScorer(_PROFILE)
        without = scorer.score(_job(description="data platform"))
        with_bonus = scorer.score(_job(description="data platform llm ai"))
        assert with_bonus > without

    def test_case_insensitive_match(self):
        scorer = StaticScorer(_PROFILE)
        lower = scorer.score(_job(description="data platform"))
        upper = scorer.score(_job(description="DATA PLATFORM"))
        assert lower == upper

    def test_empty_profile_scores_50(self):
        scorer = StaticScorer({})
        assert scorer.score(_job(description="anything")) == 50


class TestScoreJobsStatic:
    def _profiles(self):
        return {"cv1": _PROFILE}

    def test_passes_jobs_above_threshold(self):
        jobs = [_job(description="data platform mlops ai")]
        results = score_jobs_static(jobs, self._profiles(), {"min_score": 70, "max_score": 95})
        assert len(results) == 1

    def test_filters_jobs_below_threshold(self):
        jobs = [_job(title="Junior Intern", description="junior internship alternance")]
        results = score_jobs_static(jobs, self._profiles(), {"min_score": 70, "max_score": 95})
        assert len(results) == 0

    def test_picks_best_cv(self):
        profiles = {
            "cv_weak": {
                "positive_signals": [{"pattern": "data", "weight": 5}],
                "negative_signals": [],
                "domain_bonus": {},
            },
            "cv_strong": {
                "positive_signals": [{"pattern": "data", "weight": 30}],
                "negative_signals": [],
                "domain_bonus": {},
            },
        }
        results = score_jobs_static(
            [_job(description="data platform")],
            profiles,
            {"min_score": 0, "max_score": 95},
        )
        assert results[0]["best_cv"] == "cv_strong"

    def test_result_has_required_fields(self):
        jobs = [_job(description="data platform mlops")]
        results = score_jobs_static(jobs, self._profiles(), {"min_score": 0, "max_score": 95})
        r = results[0]
        assert "score" in r
        assert "best_cv" in r
        assert "recommendation" in r

    def test_recommendation_apply_above_80(self):
        profile = {
            "positive_signals": [{"pattern": "x", "weight": 35}],  # 50+35 = 85
            "negative_signals": [],
            "domain_bonus": {},
        }
        results = score_jobs_static(
            [_job(description="x")], {"cv1": profile}, {"min_score": 0, "max_score": 95}
        )
        assert results[0]["recommendation"] == "APPLY"

    def test_recommendation_consider_below_80(self):
        profile = {
            "positive_signals": [{"pattern": "x", "weight": 22}],  # 50+22 = 72
            "negative_signals": [],
            "domain_bonus": {},
        }
        results = score_jobs_static(
            [_job(description="x")], {"cv1": profile}, {"min_score": 0, "max_score": 95}
        )
        assert results[0]["recommendation"] == "CONSIDER"
