"""In-process HTTP server that serves the live AJSAA monitor.

Spawns an ``http.server.ThreadingHTTPServer`` in a daemon thread on
127.0.0.1, exposes two endpoints, and is shut down cleanly by :meth:`stop`.

Endpoints
---------
``GET /``           → live HTML page (polls ``/state.json`` every second).
``GET /state.json`` → latest pipeline-state snapshot as JSON.

Design notes
------------
- Loopback only. Binding is hard-coded to ``127.0.0.1`` regardless of what the
  caller passes; we never bind to ``0.0.0.0``. The constructor still accepts a
  ``host`` argument for symmetry with stdlib, but the value is ignored if it
  isn't ``127.0.0.1`` — failing loud rather than silently exposing.
- No auth. The page is meant for the user running ``run.py`` on their own
  machine.
- Thread-safe state. Writers (the graph) and the HTTP handler hit the same
  ``_state`` dict under a single ``RLock``. The handler deep-copies before
  serialising so a writer mid-update can't corrupt the JSON.
- Daemon thread. The server dies with the process, so ``Ctrl-C`` on
  ``run.py`` doesn't leave an orphan listener. :meth:`stop` is the graceful
  path used when the run completes normally.
"""
from __future__ import annotations

import copy
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from monitoring.web_monitoring.report import render_dashboard_html

logger = logging.getLogger(__name__)


_EMPTY_STATE: dict = {
    "run_id": "—",
    "timestamp": "",
    "status": "running",
    "current_node": None,
    "node_status": {},
    "node_timings": {},
    "kpis": {},
    "token_usage": {},
    "errors": [],
    "scored_jobs": [],
}


class LiveMonitor:
    """A tiny HTTP server that serves a live view of the running pipeline.

    Usage::

        monitor = LiveMonitor(port=8765)
        url = monitor.start()
        monitor.update_state({"status": "running", ...})
        ...
        monitor.update_state({"status": "complete", ...})
        monitor.stop()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        on_state: Callable[[dict], None] | None = None,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError(
                f"LiveMonitor binds 127.0.0.1 only by design (got {host!r})"
            )
        self._host = host
        self._port = port
        self._on_state = on_state

        self._state: dict = copy.deepcopy(_EMPTY_STATE)
        self._lock = threading.RLock()

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> str:
        """Bind the port, spawn the serve thread, return the public URL.

        Raises ``RuntimeError`` if the port is already in use.
        """
        if self._server is not None:
            raise RuntimeError("LiveMonitor already started")

        handler_factory = _make_handler_factory(self._read_state)
        try:
            self._server = ThreadingHTTPServer((self._host, self._port), handler_factory)
        except OSError as exc:
            raise RuntimeError(
                f"port {self._port} in use — pass --port to override"
            ) from exc

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"LiveMonitor:{self._port}",
            daemon=True,
        )
        self._thread.start()

        actual_port = self._server.server_address[1]
        return f"http://{self._host}:{actual_port}/"

    def stop(self) -> None:
        """Shut down the server cleanly. Idempotent."""
        if self._server is None:
            return

        def _close(srv: ThreadingHTTPServer) -> None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:  # pragma: no cover — best-effort teardown
                logger.exception("LiveMonitor shutdown failed")

        threading.Thread(target=_close, args=(self._server,), daemon=True).start()
        self._server = None
        self._thread = None

    # ── state I/O ───────────────────────────────────────────────────────────

    def update_state(self, state: dict) -> None:
        snapshot = copy.deepcopy(state)
        with self._lock:
            self._state = snapshot
        if self._on_state is not None:
            try:
                self._on_state(snapshot)
            except Exception:  # pragma: no cover
                logger.exception("LiveMonitor on_state hook raised")

    def _read_state(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)


# ── HTTP handler ────────────────────────────────────────────────────────────

def _make_handler_factory(read_state: Callable[[], dict]):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/state.json":
                self._serve_state_json()
                return
            if self.path == "/" or self.path.startswith("/?"):
                self._serve_live_page()
                return
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not Found")

        def _serve_state_json(self) -> None:
            payload = json.dumps(read_state(), default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _serve_live_page(self) -> None:
            state = read_state()
            status = state.get("status", "running")
            node_timings = state.get("node_timings", {}) or {}
            html = render_dashboard_html(
                state, 0.0, node_timings, live=True, status=status
            )
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


# ── helpers ─────────────────────────────────────────────────────────────────

def find_free_port() -> int:
    """Return a free TCP port on 127.0.0.1 — used by tests to avoid clashes."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
