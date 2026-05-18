"""Tests for agent/nodes/generate_queries — deterministic cross-product generation."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.nodes.generate_queries import (
    _build_queries,
    _cached_hash,
    _sha256_of_file,
    _write_queries_file,
    run,
)


# ── _build_queries ────────────────────────────────────────────────────────────

class TestBuildQueries:
    def test_cross_product(self):
        cvs = {"cv1": ["Senior PM", "Head of Product"]}
        queries = _build_queries(cvs, ["Paris", "Remote"])
        assert queries == [
            "Senior PM Paris",
            "Senior PM Remote",
            "Head of Product Paris",
            "Head of Product Remote",
        ]

    def test_two_cvs_combined(self):
        cvs = {
            "cv1": ["Senior PM", "Head of Product"],
            "cv2": ["AI PM", "Product Lead"],
        }
        queries = _build_queries(cvs, ["Paris"])
        assert "Senior PM Paris" in queries
        assert "AI PM Paris" in queries
        assert len(queries) == 4

    def test_positions_capped_at_two(self):
        cvs = {"cv1": ["Title A", "Title B", "Title C"]}
        queries = _build_queries(cvs, ["Paris"])
        assert len(queries) == 2
        assert "Title C Paris" not in queries

    def test_single_position_single_location(self):
        assert _build_queries({"cv1": ["PM"]}, ["London"]) == ["PM London"]

    def test_empty_locations_returns_empty(self):
        assert _build_queries({"cv1": ["PM"]}, []) == []

    def test_cv_keys_sorted_for_stable_output(self):
        cvs = {"cv2": ["AI PM"], "cv1": ["Senior PM"]}
        queries = _build_queries(cvs, ["Paris"])
        assert queries == ["Senior PM Paris", "AI PM Paris"]


# ── _cached_hash ──────────────────────────────────────────────────────────────

class TestCachedHash:
    def test_returns_none_when_file_missing(self, tmp_path):
        assert _cached_hash(tmp_path / "missing.md") is None

    def test_returns_hash_from_first_line(self, tmp_path):
        f = tmp_path / "queries.md"
        f.write_text("# hash: abc123\nsome query\n")
        assert _cached_hash(f) == "abc123"

    def test_returns_none_when_no_hash_header(self, tmp_path):
        f = tmp_path / "queries.md"
        f.write_text("some query\nother query\n")
        assert _cached_hash(f) is None

    def test_handles_empty_file(self, tmp_path):
        f = tmp_path / "queries.md"
        f.write_bytes(b"")
        assert _cached_hash(f) is None


# ── _write_queries_file ───────────────────────────────────────────────────────

class TestWriteQueriesFile:
    def test_writes_hash_on_first_line(self, tmp_path):
        f = tmp_path / "queries.md"
        _write_queries_file(f, ["PM Paris", "PM Remote"], "deadbeef")
        lines = f.read_text().splitlines()
        assert lines[0] == "# hash: deadbeef"

    def test_queries_follow_hash_and_blank_line(self, tmp_path):
        f = tmp_path / "queries.md"
        _write_queries_file(f, ["PM Paris", "PM Remote"], "deadbeef")
        lines = f.read_text().splitlines()
        assert lines[1] == ""
        assert lines[2] == "PM Paris"
        assert lines[3] == "PM Remote"

    def test_roundtrip_with_cached_hash(self, tmp_path):
        f = tmp_path / "queries.md"
        _write_queries_file(f, ["PM Paris"], "cafebabe")
        assert _cached_hash(f) == "cafebabe"


# ── run (graph node) ──────────────────────────────────────────────────────────

def _make_state(cvs_cfg: dict, locations: list[str], raw_queries: list[str] | None = None) -> dict:
    return {
        "config": {"cvs": cvs_cfg, "locations": locations},
        "raw_queries": raw_queries or [],
        "errors": [],
        "run_log": [],
    }


class TestRunNode:
    def test_generates_queries_when_no_cached_file(self, tmp_path):
        config_file = tmp_path / "search_config.yaml"
        config_file.write_text("cvs:\n  cv1:\n    - Senior PM\n")
        queries_file = tmp_path / "job_queries.md"

        state = _make_state({"cv1": ["Senior PM"]}, ["Paris"])

        with (
            patch("agent.nodes.generate_queries._SEARCH_CONFIG_PATH", config_file),
            patch("agent.nodes.generate_queries._QUERIES_FILE", queries_file),
        ):
            result = run(state)

        assert result["queries"] == ["Senior PM Paris"]
        assert queries_file.exists()
        assert _cached_hash(queries_file) == hashlib.sha256(config_file.read_bytes()).hexdigest()

    def test_cache_hit_skips_regeneration(self, tmp_path):
        config_file = tmp_path / "search_config.yaml"
        config_file.write_text("cvs:\n  cv1:\n    - Senior PM\n")
        queries_file = tmp_path / "job_queries.md"

        config_hash = hashlib.sha256(config_file.read_bytes()).hexdigest()
        _write_queries_file(queries_file, ["Senior PM Paris"], config_hash)

        state = _make_state({"cv1": ["Senior PM"]}, ["Paris"], raw_queries=["Senior PM Paris"])

        with (
            patch("agent.nodes.generate_queries._SEARCH_CONFIG_PATH", config_file),
            patch("agent.nodes.generate_queries._QUERIES_FILE", queries_file),
        ):
            result = run(state)

        assert result["queries"] == ["Senior PM Paris"]
        assert "cache hit" in result["run_log"][-1]

    def test_cache_miss_regenerates_file(self, tmp_path):
        config_file = tmp_path / "search_config.yaml"
        config_file.write_text("cvs:\n  cv1:\n    - Senior PM\n")
        queries_file = tmp_path / "job_queries.md"

        _write_queries_file(queries_file, ["Old Query Paris"], "stale_hash")

        state = _make_state({"cv1": ["Senior PM", "Head of Product"]}, ["Paris"])

        with (
            patch("agent.nodes.generate_queries._SEARCH_CONFIG_PATH", config_file),
            patch("agent.nodes.generate_queries._QUERIES_FILE", queries_file),
        ):
            result = run(state)

        assert "Senior PM Paris" in result["queries"]
        assert "Head of Product Paris" in result["queries"]
        new_hash = hashlib.sha256(config_file.read_bytes()).hexdigest()
        assert _cached_hash(queries_file) == new_hash

    def test_error_when_no_cvs_in_config(self):
        state = _make_state({}, ["Paris"])
        result = run(state)
        assert result["queries"] == []
        assert any("cvs" in e for e in result["errors"])

    def test_error_when_no_locations_in_config(self):
        state = _make_state({"cv1": ["PM"]}, [])
        result = run(state)
        assert result["queries"] == []
        assert any("locations" in e for e in result["errors"])
