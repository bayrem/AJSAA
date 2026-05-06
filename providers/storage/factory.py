def build_storage(cfg: dict):
    """Return a BaseStorageProvider for the configured provider."""
    provider = cfg.get("provider", "local").lower()

    if provider == "local":
        from providers.storage.local import LocalJSONProvider
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
        raise ValueError(f"Unknown storage provider: '{provider}'. Supported: local, google_drive, onedrive, dropbox")
