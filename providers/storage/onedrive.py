"""OneDrive storage — placeholder."""
import logging

from providers.storage.local import LocalJSONProvider

logger = logging.getLogger(__name__)


class OneDriveProvider(LocalJSONProvider):
    """
    Placeholder. Implement using Microsoft Graph API:
    https://learn.microsoft.com/en-us/graph/api/driveitem-put-content

    Required env vars: ONEDRIVE_CLIENT_ID, ONEDRIVE_CLIENT_SECRET, ONEDRIVE_TENANT_ID
    Add `msal` to requirements.txt for auth.
    """

    def __init__(self, cfg: dict):
        super().__init__(cfg.get("local_path", ".data/jobs.json"))
        logger.warning("OneDriveProvider is a placeholder — using local storage only")
