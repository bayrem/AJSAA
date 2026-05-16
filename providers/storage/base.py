"""Base contract for storage providers.

A storage provider owns the persistence layer for scored jobs. Every
provider:

  - ``save(jobs)``  appends new jobs to its store, returning how many were
    actually new (deduped against existing rows by ``job_id``).
  - ``load_all()`` returns every job currently stored.

Cloud-storage providers (Google Drive, OneDrive, Dropbox) typically extend
:class:`providers.storage.local.LocalJSONProvider` so the local file remains
the source of truth and cloud writes are an additional concern.
"""
from abc import ABC, abstractmethod


class BaseStorageProvider(ABC):
    """Abstract contract for any job-storage backend."""

    @abstractmethod
    def save(self, jobs: list[dict]) -> int:
        """Persist ``jobs``, returning how many were newly added.

        Jobs already present (matched by ``job_id``) must be silently
        ignored — the agent runs daily and should be idempotent.
        """

    @abstractmethod
    def load_all(self) -> list[dict]:
        """Return every job currently stored, in storage order."""
