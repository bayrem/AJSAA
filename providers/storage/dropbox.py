"""Dropbox storage — placeholder.

Falls back to local-only storage until implemented. To complete the
integration:

  1. Add ``dropbox`` (the official SDK) to requirements.txt.
  2. Set ``DROPBOX_ACCESS_TOKEN`` in ``.env`` (see ``.env.template``).
  3. Implement an ``_upload`` method that calls ``dropbox.Dropbox(...).files_upload``
     with ``self.path``'s contents on every ``save``.
"""
import logging

from providers.storage.local import LocalJSONProvider

logger = logging.getLogger(__name__)


class DropboxProvider(LocalJSONProvider):
    """Stub — writes to local storage and logs a placeholder warning."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg.get("local_path", ".data/jobs.json"))
        logger.warning("DropboxProvider is a placeholder — using local storage only")
