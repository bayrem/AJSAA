"""Tests for providers/scoring/profile_store.py"""


from providers.scoring.profile_store import content_hash, load_profile, save_profile


def _profile(cv_name="cv1", cv_hash="abc123"):
    return {
        "cv": cv_name,
        "cv_hash": cv_hash,
        "positive_signals": [{"pattern": "data", "weight": 20}],
        "negative_signals": [],
        "domain_bonus": {},
        "uncertainty_band": [60, 80],
    }


class TestContentHash:
    def test_deterministic(self):
        assert content_hash("hello") == content_hash("hello")

    def test_different_texts_differ(self):
        assert content_hash("hello") != content_hash("world")

    def test_length_is_16(self):
        assert len(content_hash("anything")) == 16


class TestSaveAndLoadProfile:
    def test_roundtrip(self, tmp_path):
        profile = _profile()
        save_profile(profile, str(tmp_path))
        loaded = load_profile("cv1", "abc123", str(tmp_path))
        assert loaded == profile

    def test_creates_directory(self, tmp_path):
        nested = tmp_path / "a" / "b"
        save_profile(_profile(), str(nested))
        assert (nested / "cv1.json").exists()

    def test_returns_none_when_file_missing(self, tmp_path):
        assert load_profile("nonexistent", "hash", str(tmp_path)) is None

    def test_returns_none_when_cv_hash_differs(self, tmp_path):
        save_profile(_profile(cv_hash="old_hash"), str(tmp_path))
        assert load_profile("cv1", "new_hash", str(tmp_path)) is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        (tmp_path / "cv1.json").write_text("not json", encoding="utf-8")
        assert load_profile("cv1", "abc123", str(tmp_path)) is None

    def test_valid_hash_returns_profile(self, tmp_path):
        profile = _profile(cv_hash="correcthash")
        save_profile(profile, str(tmp_path))
        loaded = load_profile("cv1", "correcthash", str(tmp_path))
        assert loaded is not None
        assert loaded["cv_hash"] == "correcthash"
