"""Spoke tools for the hub-and-spoke agent (v3).

Each module exposes plain Python functions that take a :class:`RunStore`
(plus config), do one job, persist results to the store, and return a short
human-readable summary. ``agent.tools.server`` wraps them as Agent SDK tools.
"""
