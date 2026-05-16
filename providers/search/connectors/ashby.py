"""Ashby ATS connector — kept as a thin shim around :mod:`ats`.

The actual implementation now lives in
:mod:`providers.search.connectors.ats` so all three ATSes share one tested
codepath. This module is preserved so any external caller importing
``AshbyConnector`` keeps working.
"""
from providers.search.connectors.ats import ASHBY_SPEC, AtsConnector


class AshbyConnector(AtsConnector):
    """Ashby-specific binding of :class:`AtsConnector`."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(ASHBY_SPEC, cfg)
