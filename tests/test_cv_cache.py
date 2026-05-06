"""Tests for providers/scoring/cv_cache.py"""
import hashlib
from unittest.mock import MagicMock

import pytest

from providers.scoring.cv_cache import _compress, get_or_compress


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    """Redirect cache writes to a temp directory for every test."""
    monkeypatch.setattr("providers.scoring.cv_cache._CACHE_DIR", tmp_path / "cv_cache")
    return tmp_path / "cv_cache"


def _make_llm(response: str = "YOE: 10 years\nRole: PM") -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=response)
    return llm


def _make_cv(name: str = "cv1", content: str = "Senior PM with 10 years experience") -> dict:
    return {"name": name, "content": content}


# ── cache miss / hit ──────────────────────────────────────────────────────────

class TestGetOrCompress:
    def test_cache_miss_calls_llm(self, tmp_cache):
        llm = _make_llm("YOE: 10")
        cv = _make_cv()

        result = get_or_compress(llm, cv)

        llm.invoke.assert_called_once()
        assert result == "YOE: 10"

    def test_cache_miss_writes_file(self, tmp_cache):
        llm = _make_llm("YOE: 10")
        cv = _make_cv()
        content_hash = hashlib.sha256(cv["content"].encode()).hexdigest()[:16]

        get_or_compress(llm, cv)

        cache_file = tmp_cache / f"{cv['name']}_{content_hash}.txt"
        assert cache_file.exists()
        assert cache_file.read_text() == "YOE: 10"

    def test_cache_hit_skips_llm(self, tmp_cache):
        llm = _make_llm("YOE: 10")
        cv = _make_cv()

        get_or_compress(llm, cv)   # miss — writes cache
        llm.invoke.reset_mock()
        result = get_or_compress(llm, cv)  # hit — reads from disk

        llm.invoke.assert_not_called()
        assert result == "YOE: 10"

    def test_changed_cv_triggers_new_compression(self, tmp_cache):
        llm = _make_llm("YOE: 10")
        cv_v1 = _make_cv(content="Senior PM, 10 years")
        cv_v2 = _make_cv(content="Senior PM, 11 years")  # different content

        get_or_compress(llm, cv_v1)
        assert llm.invoke.call_count == 1

        get_or_compress(llm, cv_v2)
        assert llm.invoke.call_count == 2  # second call for changed CV

    def test_different_cv_names_stored_separately(self, tmp_cache):
        llm = _make_llm("result")
        cv_a = _make_cv(name="cv_a", content="same content")
        cv_b = _make_cv(name="cv_b", content="same content")

        get_or_compress(llm, cv_a)
        get_or_compress(llm, cv_b)

        # Same content but different names → different cache files
        assert llm.invoke.call_count == 2

    def test_returns_stripped_content(self, tmp_cache):
        llm = _make_llm("  YOE: 5 years  \n")
        cv = _make_cv()

        result = get_or_compress(llm, cv)

        assert result == "YOE: 5 years"


# ── _compress ─────────────────────────────────────────────────────────────────

class TestCompress:
    def test_passes_cv_content_in_prompt(self):
        llm = _make_llm("YOE: 3")
        _compress(llm, "my cv content here")

        call_args = llm.invoke.call_args[0][0]  # first positional arg (list of messages)
        assert "my cv content here" in call_args[0].content

    def test_returns_str(self):
        llm = _make_llm("YOE: 3")
        result = _compress(llm, "cv")
        assert isinstance(result, str)
