"""Tests for ``scripts.live_server`` (issue #62).

What's covered
--------------
- Server boots on a free port and serves ``/`` and ``/state.json``.
- ``update_state`` is atomic under concurrent writers — no torn JSON.
- Page template renders BOTH the live variant (with JS poll) and the static
  one (no JS), proving the shared renderer can dual-mode.
- A port that's already bound raises a clean ``RuntimeError`` rather than
  fronting an OSError.

Why random ports
----------------
We use ``find_free_port()`` for every test instead of hard-coding 8765 so the
suite never collides with a developer's actual live monitor and so two test
processes (CI parallelism) can run side by side. ``socket.bind(("127.0.0.1",
0))`` lets the kernel pick.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from urllib.request import urlopen

import pytest

from scripts import report
from scripts.live_server import LiveMonitor, find_free_port

# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def started_monitor():
    """Spin up a LiveMonitor on a free port, tear it down at end of test."""
    port = find_free_port()
    mon = LiveMonitor(port=port)
    url = mon.start()
    try:
        yield mon, url
    finally:
        mon.stop()
        # Give the daemon serve thread a tick to release the socket so the
        # next test (using a fresh port anyway) doesn't see lingering state.
        time.sleep(0.05)


def _fetch(url: str, timeout: float = 2.0) -> tuple[int, bytes]:
    """Tiny urllib helper — returns ``(status, body)``."""
    with urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read()


# ── server lifecycle ────────────────────────────────────────────────────────


class TestServerLifecycle:
    def test_start_returns_loopback_url(self, started_monitor):
        _, url = started_monitor
        assert url.startswith("http://127.0.0.1:")

    def test_only_loopback_host_allowed(self):
        # Defensive: the constructor refuses non-loopback hosts so a future
        # caller can't accidentally expose the page on the LAN.
        with pytest.raises(ValueError):
            LiveMonitor(host="0.0.0.0", port=find_free_port())

    def test_port_in_use_raises_clean_error(self):
        # Bind a socket, hold it, then try to boot a monitor on the same port.
        port = find_free_port()
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
        try:
            mon = LiveMonitor(port=port)
            with pytest.raises(RuntimeError, match="port .* in use"):
                mon.start()
        finally:
            holder.close()

    def test_stop_is_idempotent(self):
        mon = LiveMonitor(port=find_free_port())
        mon.start()
        mon.stop()
        # Second call must be a no-op, not an exception.
        mon.stop()


# ── HTTP endpoints ──────────────────────────────────────────────────────────


class TestEndpoints:
    def test_state_json_endpoint(self, started_monitor):
        mon, url = started_monitor
        mon.update_state({
            "run_id": "abc123",
            "status": "running",
            "current_node": "analyze_jobs",
            "node_status": {"load_context": "complete"},
        })
        status, body = _fetch(url + "state.json")
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["run_id"] == "abc123"
        assert payload["current_node"] == "analyze_jobs"
        assert payload["status"] == "running"

    def test_root_serves_live_html(self, started_monitor):
        mon, url = started_monitor
        mon.update_state({
            "run_id": "abc123",
            "status": "running",
            "node_status": {},
            "node_timings": {},
            "errors": [],
            "scored_jobs": [],
        })
        status, body = _fetch(url)
        assert status == 200
        html = body.decode("utf-8")
        # Live variant must include the JS poll block.
        assert "<script>" in html
        assert "/state.json" in html
        assert "AJSAA — Run abc123" in html

    def test_unknown_path_returns_404(self, started_monitor):
        _, url = started_monitor
        # urlopen raises HTTPError on 4xx — catch and inspect.
        import urllib.error
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _fetch(url + "no-such-thing")
        assert exc_info.value.code == 404


# ── state writer ────────────────────────────────────────────────────────────


class TestStateWriter:
    def test_update_state_replaces_atomically(self):
        # Pure in-memory test — no server needed.
        mon = LiveMonitor(port=find_free_port())
        mon.update_state({"a": 1, "status": "running"})
        snap = mon._read_state()
        assert snap == {"a": 1, "status": "running"}
        mon.update_state({"b": 2, "status": "complete"})
        snap = mon._read_state()
        assert snap == {"b": 2, "status": "complete"}

    def test_update_state_deep_copies_input(self):
        # Mutating the dict after passing it must not affect the served state.
        mon = LiveMonitor(port=find_free_port())
        payload = {"nodes": ["a", "b"], "status": "running"}
        mon.update_state(payload)
        payload["nodes"].append("c")
        assert mon._read_state()["nodes"] == ["a", "b"]

    def test_concurrent_writers_dont_corrupt(self):
        # 8 writer threads * 250 writes each, with reads interleaved. The
        # invariant: every read sees a dict that's exactly one of the
        # writers' payloads — never a half-merged frankenstate with keys
        # from different writers.
        mon = LiveMonitor(port=find_free_port())
        # Seed with a known payload so readers don't fire before any writer
        # has run and see the unrelated default state. The seed is itself a
        # valid writer payload, so the invariant still holds.
        mon.update_state({"writer": -1, "step": -1, "status": "running"})

        errors: list[str] = []
        stop = threading.Event()
        expected_keys = {"writer", "step", "status"}

        def writer(tag: int) -> None:
            for i in range(250):
                mon.update_state({"writer": tag, "step": i, "status": "running"})

        def reader() -> None:
            while not stop.is_set():
                snap = mon._read_state()
                # A torn read would show extra/missing keys vs the writer
                # payload shape. We don't care about value identity — only
                # that the dict is internally consistent.
                if set(snap.keys()) != expected_keys:
                    errors.append(f"torn read: {snap!r}")
                    return

        readers = [threading.Thread(target=reader) for _ in range(2)]
        for r in readers:
            r.start()
        writers = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        stop.set()
        for r in readers:
            r.join()

        assert errors == [], errors


# ── shared template (live vs static) ────────────────────────────────────────


class TestTemplateModes:
    """The same renderer must produce both variants. Issue #62 acceptance."""

    def _base_state(self) -> dict:
        return {
            "run_id": "deadbeef",
            "timestamp": "2026-05-17 10:00 UTC",
            "stored_count": 0,
            "scored_jobs": [],
            "errors": [],
            "token_usage": {},
        }

    def test_live_mode_includes_poll_js(self):
        html = report.render_dashboard_html(
            self._base_state(), duration_s=0.0, node_timings={}, live=True, status="running"
        )
        assert "<script>" in html
        assert "/state.json" in html
        assert "badge-running" in html

    def test_static_mode_omits_poll_js(self):
        html = report.render_dashboard_html(
            self._base_state(), duration_s=12.3, node_timings={}, live=False, status="complete"
        )
        assert "<script>" not in html
        assert "/state.json" not in html
        assert "badge-complete" in html

    def test_failed_status_renders_red_badge(self):
        state = self._base_state()
        state["errors"] = ["something exploded"]
        html = report.render_dashboard_html(
            state, duration_s=1.0, node_timings={}, live=False, status="failed"
        )
        assert "badge-failed" in html
        # Errors block must be visible (display:block, not none).
        assert "display:block" in html

    def test_unknown_status_falls_back_to_running_badge(self):
        # Guards against a caller passing an unexpected value; badge CSS only
        # has classes for the three documented states.
        html = report.render_dashboard_html(
            self._base_state(), duration_s=0.0, node_timings={}, live=True, status="weird"
        )
        assert "badge-running" in html


# ── graph integration smoke test ────────────────────────────────────────────


class TestGraphHook:
    """``agent.graph._safe`` must push snapshots when a writer is registered."""

    def test_writer_called_per_node_completion(self):
        # We don't actually run the full pipeline (slow, requires LLMs); we
        # just exercise the wrapper directly with a stub node function.
        from agent import graph as graph_mod

        snapshots: list[dict] = []

        def writer(snap: dict) -> None:
            snapshots.append(snap)

        graph_mod.set_live_state_writer(writer)
        try:
            wrapped = graph_mod._safe(lambda s: {"stored_count": 7}, "store_results")
            wrapped({"run_id": "x", "timestamp": "t", "errors": []})
        finally:
            graph_mod.set_live_state_writer(None)

        # Wrapper pushes a "running" snapshot before the call, then one after.
        assert len(snapshots) >= 2
        # The final snapshot must reflect the node's status as complete.
        assert snapshots[-1]["node_status"]["store_results"] == "complete"
        assert snapshots[-1]["current_node"] == "store_results"

    def test_writer_failure_does_not_break_node(self):
        # Live writer errors must NEVER propagate into the pipeline.
        from agent import graph as graph_mod

        def bad_writer(snap: dict) -> None:
            raise RuntimeError("kaboom")

        graph_mod.set_live_state_writer(bad_writer)
        try:
            wrapped = graph_mod._safe(lambda s: {"ok": True}, "load_context")
            result = wrapped({"run_id": "x", "timestamp": "t", "errors": []})
        finally:
            graph_mod.set_live_state_writer(None)

        assert result == {"ok": True}
