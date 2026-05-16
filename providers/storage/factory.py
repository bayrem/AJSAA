"""Storage-provider factory.

Selects between local-only and cloud-backed providers based on
``cfg["provider"]`` from the ``storage`` block of config.yaml. The cloud
providers all extend :class:`LocalJSONProvider`, so even when one is
selected the local file remains the source of truth.
"""


def build_storage(cfg: dict):
    """Return a configured ``BaseStorageProvider`` instance.

    Args:
        cfg: The ``storage`` slice of config.yaml.

    Raises:
        ValueError: If ``cfg["provider"]`` is not recognised.
    """
    provider = cfg.get("provider", "local").lower()

    if provider == "local":
        from providers.storage.local import LocalJSONProvider
        # Local provider takes the path directly rather than the full cfg
        return LocalJSONProvider(cfg.get("local_path", ".data/jobs.json"))
    elif provider == "google_drive":
        from providers.storage.google_drive import GoogleDriveProvider
        return GoogleDriveProvider(cfg)
    elif provider == "onedrive":
        from providers.storage.onedrive import OneDriveProvider
        return OneDriveProvider(cfg)
    elif provider == "dropbox":
        from providers.storage.dropbox import DropboxProvider
        return DropboxProvider(cfg)
    else:
        raise ValueError(
            f"Unknown storage provider: '{provider}'. "
            "Supported: local, google_drive, onedrive, dropbox"
        )
