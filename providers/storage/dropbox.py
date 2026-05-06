"""Dropbox storage — placeholder."""
import logging

from providers.storage.local import LocalJSONProvider

logger = logging.getLogger(__name__)


class DropboxProvider(LocalJSONProvider):
    """
    Placeholder. Implement using the Dropbox SDK:
    https://github.com/dropbox/dropbox-sdk-python

    Required env vars: DROPBOX_ACCESS_TOKEN
    Add `dropbox` to requirements.txt.
    """

    def __init__(self, cfg: dict):
        super().__init__(cfg.get("local_path", ".data/jobs.json"))
        logger.warning("DropboxProvider is a placeholder — using local storage only")
