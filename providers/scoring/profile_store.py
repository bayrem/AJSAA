"""Load, save, and invalidate per-CV scoring profiles from disk."""
import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_profile(cv_name: str, cv_hash: str, profiles_dir: str) -> dict | None:
    """Return the profile if it exists and the CV hasn't changed; None otherwise."""
    path = Path(profiles_dir) / f"{cv_name}.json"
    if not path.exists():
        return None
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read profile '%s': %s", path, e)
        return None
    if profile.get("cv_hash") != cv_hash:
        logger.info("Profile for '%s' is stale (CV changed) — will re-bootstrap", cv_name)
        return None
    return profile


def save_profile(profile: dict, profiles_dir: str) -> None:
    path = Path(profiles_dir) / f"{profile['cv']}.json"
    Path(profiles_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved scoring profile for '%s' → %s", profile["cv"], path)
