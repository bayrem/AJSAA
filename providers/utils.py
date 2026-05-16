"""Shared helpers for provider modules.

This module centralises three patterns that were previously copy-pasted across
the providers/ tree:

  - ``env_required``: fail-fast accessor for required environment variables.
    Replaces the awkward ``os.environ.get(X) or _require(X)`` idiom that was
    duplicated in every notifier module.

  - ``JsonCache``: a tiny load/save wrapper around a JSON file on disk that
    swallows I/O errors and logs them. Replaces four near-identical copies in
    ``circuit_breaker``, ``adaptive_web``, ``store_results`` and
    ``search_companies``.

  - ``strip_json_fence``: removes ```` ```json ... ``` ```` fences from LLM
    output so the inner JSON can be parsed. Replaces three slightly different
    implementations in ``llm_scorer``, ``hybrid_scorer`` and ``web_search`` —
    one of which had a real ``str.lstrip`` substring bug.

Keeping them together (rather than scattered across submodules) makes the
intent obvious and avoids creating new package boundaries for what are really
small utilities.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, NoReturn

logger = logging.getLogger(__name__)


# ── Environment variables ────────────────────────────────────────────────────

def env_required(var: str) -> str:
    """Return the value of an environment variable, or raise if missing.

    Used by notifier and connector constructors that genuinely cannot operate
    without a credential. The error message points the user at ``.env.template``
    so they know where to look.

    Args:
        var: Name of the environment variable to read.

    Returns:
        The value of the environment variable (guaranteed non-empty).

    Raises:
        ValueError: If the variable is unset or empty.
    """
    value = os.environ.get(var, "")
    if not value:
        raise ValueError(
            f"{var} is not set — add it to your .env file (see .env.template)"
        )
    return value


def env_required_strict(var: str) -> NoReturn:
    """Always raise — used only for backwards-compatible ``or _require()`` callers.

    Prefer ``env_required(var)`` directly. This shim exists so legacy callers
    that did ``os.environ.get(var) or _require(var)`` still work during the
    migration window. Do not introduce new uses.
    """
    raise ValueError(
        f"{var} is not set — add it to your .env file (see .env.template)"
    )


# ── JSON file cache ──────────────────────────────────────────────────────────

class JsonCache:
    """Load/save helper for a JSON file used as on-disk state.

    Three behaviours that every previous copy of this pattern shared:

      1. Missing file → return ``default`` (empty dict by default).
      2. Corrupt JSON → log a warning and return ``default`` (don't crash).
      3. Save failures (read-only FS, permission errors) → log and skip.

    The class is intentionally tiny — it isn't trying to be a database. It just
    captures the repeated try/except boilerplate in one place so callers can
    write ``cache.load()`` / ``cache.save(data)`` and stop thinking about it.
    """

    def __init__(self, path: str | Path, default_factory: type = dict) -> None:
        """Create a cache backed by ``path``.

        Args:
            path: Path to the JSON file (need not exist yet).
            default_factory: Callable that returns the empty/default value
                used when the file is missing or unreadable.
        """
        self.path = Path(path)
        self._default_factory = default_factory

    def load(self) -> Any:
        """Return the parsed JSON content, or the default value on failure."""
        if not self.path.exists():
            return self._default_factory()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("JsonCache: could not read %s: %s", self.path, e)
            return self._default_factory()

    def save(self, data: Any) -> None:
        """Write ``data`` to disk as JSON, creating parent dirs as needed.

        Any I/O error is logged at WARNING level but otherwise swallowed so
        cache failures never crash the agent.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("JsonCache: failed to persist %s: %s", self.path, e)


# ── LLM output normalisation ─────────────────────────────────────────────────

def strip_json_fence(raw: str) -> str:
    """Strip Markdown code fences from an LLM response, returning the inner text.

    LLMs frequently wrap JSON output in ```` ```json ... ``` ```` blocks even
    when explicitly asked not to. This helper handles three common shapes:

      - ``"```json\\n...\\n```"`` — labelled fence.
      - ``"```\\n...\\n```"``    — bare fence.
      - Plain text                — returned unchanged (after ``strip()``).

    Important: this strips the substring ``"json"`` only when it immediately
    follows the opening fence. The previous ``hybrid_scorer._strip_json``
    implementation used ``str.lstrip("json")`` which actually stripped any
    leading occurrence of the characters {j, o, s, n} — a subtle bug that
    over-stripped inputs starting with letters like ``j`` or ``s``.
    """
    raw = raw.strip()
    if "```json" in raw:
        # Take what's between the first ```json and the next ```
        return raw.split("```json", 1)[1].split("```", 1)[0].strip()
    if raw.startswith("```"):
        # Drop opening fence, optional "json" label, and any trailing fence
        inner = raw[3:]
        if inner.startswith("json"):
            inner = inner[4:]
        if "```" in inner:
            inner = inner.split("```", 1)[0]
        return inner.strip()
    return raw
