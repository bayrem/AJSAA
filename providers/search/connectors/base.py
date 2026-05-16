"""Backwards-compatibility shim.

Historically there were two near-identical abstract base classes for search
providers: ``BaseSearchProvider`` (in ``providers/search/base.py``) and
``BaseJobBoardConnector`` (here). They had the same method signature and the
distinction was never enforced.

The codebase has standardised on :class:`providers.search.base.BaseSearchProvider`.
This module re-exports it under the legacy name so any direct imports keep
working during the migration window. Prefer importing
``BaseSearchProvider`` directly in new code.
"""
from providers.search.base import BaseSearchProvider

# Legacy alias kept so external callers don't break. Internal code should
# import BaseSearchProvider directly.
BaseJobBoardConnector = BaseSearchProvider

__all__ = ["BaseJobBoardConnector"]
