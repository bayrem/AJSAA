"""Load, save, and invalidate per-CV scoring profiles on disk.

Profiles are the cached output of the hybrid-scorer's bootstrap step — once
generated, they let static scoring run with no LLM calls. A profile is keyed
by the CV's name *and* its content hash; editing the CV invalidates the
profile so the hybrid scorer rebuilds it on the next run.

File layout: ``{profiles_dir}/{cv_name}.json``
"""
import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    """Return a stable 16-char hash of the given text.

    Used as the CV-edit detection key. SHA-256 truncated to 16 hex chars is
    plenty of collision resistance for this use case (we're checking
    "did this single CV change?" not building a content-addressed store).
    """
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_profile(cv_name: str, cv_hash: str, profiles_dir: str) -> dict | None:
    """Return the profile if it exists and matches the current CV hash; ``None`` otherwise.

    Three failure cases all return ``None`` (logged appropriately):
      - File doesn't exist (never bootstrapped)
      - File is unreadable / not valid JSON (corrupt)
      - Hash mismatch (CV has been edited since the profile was saved)
    """
    path = Path(profiles_dir) / f"{cv_name}.json"
    if not path.exists():
        return None
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read profile '%s': %s", path, e)
        return None
    if profile.get("cv_hash") != cv_hash:
        # CV content has changed — the cached profile no longer matches.
        # Caller will treat this as "needs bootstrap" and rebuild.
        logger.info("Profile for '%s' is stale (CV changed) — will re-bootstrap", cv_name)
        return None
    return profile


def save_profile(profile: dict, profiles_dir: str) -> None:
    """Persist a profile to ``{profiles_dir}/{profile['cv']}.json``."""
    path = Path(profiles_dir) / f"{profile['cv']}.json"
    Path(profiles_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved scoring profile for '%s' → %s", profile["cv"], path)
