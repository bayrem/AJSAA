"""Tests for providers/search/dedup — semantic_deduplicate()."""
import pytest

from providers.search.dedup import DEDUP_THRESHOLD, semantic_deduplicate


# ── Helpers ───────────────────────────────────────────────────────────────────

def _job(title: str, company: str, location: str, url: str = "https://example.com") -> dict:
    return {"title": title, "company": company, "location": location, "url": url}


# ── Same role, different URL ──────────────────────────────────────────────────

class TestSameRoleDifferentURL:
    def test_exact_duplicate_kept_once(self):
        jobs = [
            _job("Senior Product Manager", "Dataiku", "Paris", "https://indeed.com/1"),
            _job("Senior Product Manager", "Dataiku", "Paris", "https://wttj.co/1"),
            _job("Senior Product Manager", "Dataiku", "Paris", "https://careers.dataiku.com"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 1
        assert result[0]["url"] == "https://indeed.com/1"

    def test_near_duplicate_title_variation(self):
        """Minor title variants like "Sr." vs "Senior" should still match."""
        jobs = [
            _job("Senior Product Manager", "Dataiku", "Paris, France"),
            _job("Senior Product Manager ", "Dataiku", "Paris, France"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 1

    def test_preserves_first_occurrence(self):
        first = _job("AI Product Manager", "Mistral", "Paris", "https://first.com")
        second = _job("AI Product Manager", "Mistral", "Paris", "https://second.com")
        result = semantic_deduplicate([first, second])
        assert result[0] is first


# ── Different roles — should NOT be deduped ──────────────────────────────────

class TestDifferentRoles:
    def test_different_titles_kept(self):
        jobs = [
            _job("Product Manager", "Dataiku", "Paris"),
            _job("Engineering Manager", "Dataiku", "Paris"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 2

    def test_different_companies_kept(self):
        jobs = [
            _job("Head of Product", "Dataiku", "Paris"),
            _job("Head of Product", "Criteo", "Paris"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 2

    def test_empty_input(self):
        assert semantic_deduplicate([]) == []

    def test_single_item_unchanged(self):
        job = _job("Product Manager", "Acme", "Paris")
        result = semantic_deduplicate([job])
        assert result == [job]


# ── Location mismatch ─────────────────────────────────────────────────────────

class TestLocationMismatch:
    def test_paris_vs_london_kept(self):
        jobs = [
            _job("Senior Product Manager", "Dataiku", "Paris"),
            _job("Senior Product Manager", "Dataiku", "London"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 2

    def test_remote_vs_paris_kept(self):
        jobs = [
            _job("Product Manager", "Mistral", "Remote"),
            _job("Product Manager", "Mistral", "Paris"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 2

    def test_same_location_different_formatting_deduped(self):
        """'Paris, France' and 'Paris France' are near-identical — should dedup."""
        jobs = [
            _job("VP Product", "Qonto", "Paris, France"),
            _job("VP Product", "Qonto", "Paris France"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 1


# ── Threshold boundary ────────────────────────────────────────────────────────

class TestThresholdBoundary:
    def test_dedup_threshold_constant_value(self):
        assert DEDUP_THRESHOLD == 0.85

    def test_highly_similar_strings_above_threshold(self):
        """Strings that ratio() > 0.85 → treated as duplicates."""
        jobs = [
            _job("Senior Product Manager EMEA", "Dataiku", "Paris"),
            _job("Senior Product Manager, EMEA", "Dataiku", "Paris"),
        ]
        result = semantic_deduplicate(jobs)
        # These differ only by a comma — ratio will be well above 0.85
        assert len(result) == 1

    def test_clearly_different_strings_below_threshold(self):
        """Completely unrelated titles stay separate."""
        jobs = [
            _job("Chief Financial Officer", "Ledger", "Paris"),
            _job("Software Engineer", "Ledger", "Paris"),
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 2


# ── Missing / empty fields ───────────────────────────────────────────────────

class TestMissingFields:
    def test_missing_location_treated_as_empty_string(self):
        """Two jobs with missing location and otherwise identical fields dedup."""
        jobs = [
            {"title": "PM", "company": "Acme", "url": "https://a.com"},
            {"title": "PM", "company": "Acme", "url": "https://b.com"},
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 1

    def test_none_fields_handled_gracefully(self):
        jobs = [
            {"title": None, "company": None, "location": None, "url": "https://a.com"},
            {"title": None, "company": None, "location": None, "url": "https://b.com"},
        ]
        result = semantic_deduplicate(jobs)
        assert len(result) == 1


# ── Order preservation ────────────────────────────────────────────────────────

class TestOrderPreservation:
    def test_output_order_matches_input_order(self):
        jobs = [
            _job("PM", "Alpha", "Paris"),
            _job("CTO", "Beta", "Lyon"),
            _job("PM", "Alpha", "Paris"),  # duplicate of first
            _job("Designer", "Gamma", "Bordeaux"),
        ]
        result = semantic_deduplicate(jobs)
        assert [j["company"] for j in result] == ["Alpha", "Beta", "Gamma"]
