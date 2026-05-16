"""OneDrive storage — placeholder.

Falls back to local-only storage until implemented. To complete the
integration:

  1. Add ``msal`` to requirements.txt for Azure AD device-code auth.
  2. Set the three OneDrive env vars in ``.env`` (see ``.env.template``).
  3. Implement an ``_upload`` method that pushes ``self.path`` via
     Microsoft Graph's ``driveItem.put-content`` endpoint.
"""
import logging

from providers.storage.local import LocalJSONProvider

logger = logging.getLogger(__name__)


class OneDriveProvider(LocalJSONProvider):
    """Stub — writes to local storage and logs a placeholder warning."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg.get("local_path", ".data/jobs.json"))
        # Warning at construction time so misconfiguration surfaces before
        # the first run-end attempt to sync.
        logger.warning("OneDriveProvider is a placeholder — using local storage only")
