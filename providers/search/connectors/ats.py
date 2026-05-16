"""Shared ATS connector — Greenhouse, Lever, Ashby.

These three applicant-tracking systems all expose unauthenticated public
job-board endpoints with very similar JSON shapes. Each one differs only in:

  - The URL template
  - Where the jobs array lives in the response
  - Which field names hold the title / url / location / description
  - Whether the description is HTML or plain text

Capturing those differences in a small ``AtsSpec`` dataclass and writing one
generic ``AtsConnector.fetch()`` implementation eliminates ~100 lines of
duplication across three previously near-identical modules.

Adding a new ATS that follows the same pattern is now a matter of writing
one ``AtsSpec`` constant — no class boilerplate required.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)


# ── Constants shared across every ATS ────────────────────────────────────────

# ATSes return jobs from every office a company has worldwide. We always
# filter to roles whose location string mentions Paris / France / remote.
_DEFAULT_LOCATION_KEYWORDS = [
    "paris", "france", "remote", "télétravail", "hybrid", "île-de-france",
]

# Slugs are taken from user-controlled hints_cache.json and concatenated into
# URLs, so we validate them. Lowercase alphanumeric + hyphen, leading alnum.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")

# Cap on description length saved per job. 1000 chars is enough for scoring
# without bloating the storage JSON.
_DESCRIPTION_MAX = 1000


def _strip_html(s: str) -> str:
    """Remove HTML tags and collapse whitespace. Used for HTML descriptions."""
    return re.sub(r"<[^>]+>", " ", s).strip()


# ── ATS specification dataclass ──────────────────────────────────────────────

@dataclass(frozen=True)
class AtsSpec:
    """Per-ATS configuration handed to :class:`AtsConnector`.

    Attributes:
        source: Canonical name used in the output dict's ``source`` field and
            in log messages.
        url_template: URL containing one ``{slug}`` placeholder.
        extract_items: Callable that, given the parsed JSON response, returns
            the list of raw job items. Captures the difference between
            "the response is the array" (Lever) and "the array lives under
            a specific key" (Greenhouse, Ashby).
        title_key / url_key: Top-level keys in each item that hold the title
            and URL respectively.
        extract_location: Pulls the human-readable location string out of an
            item — sometimes nested (Greenhouse: ``location.name``),
            sometimes flat (Ashby: ``locationName``).
        extract_description: Pulls the description text out of an item.
            Some ATSes return HTML (Greenhouse, Ashby) which we strip; Lever
            already provides plain text.
    """

    source: str
    url_template: str
    extract_items: Callable[[dict], list[dict]]
    title_key: str
    url_key: str
    extract_location: Callable[[dict], str]
    extract_description: Callable[[dict], str]


# ── Concrete specs ───────────────────────────────────────────────────────────

GREENHOUSE_SPEC = AtsSpec(
    source="greenhouse",
    # ``?content=true`` asks Greenhouse to include the full description HTML
    # in the response so we don't need a second request per job.
    url_template="https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    extract_items=lambda data: data.get("jobs", []),
    title_key="title",
    url_key="absolute_url",
    extract_location=lambda item: item.get("location", {}).get("name", ""),
    extract_description=lambda item: _strip_html(item.get("content", ""))[:_DESCRIPTION_MAX],
)


LEVER_SPEC = AtsSpec(
    source="lever",
    # Lever's ``mode=json`` returns the raw array at the root of the response.
    url_template="https://api.lever.co/v0/postings/{slug}?mode=json",
    extract_items=lambda data: data if isinstance(data, list) else [],
    # Lever uses ``text`` rather than ``title`` for the role name.
    title_key="text",
    url_key="hostedUrl",
    extract_location=lambda item: item.get("categories", {}).get("location", ""),
    # Lever returns plain text in ``descriptionPlain`` — no HTML stripping needed.
    extract_description=lambda item: item.get("descriptionPlain", "")[:_DESCRIPTION_MAX],
)


ASHBY_SPEC = AtsSpec(
    source="ashby",
    url_template="https://api.ashbyhq.com/posting-api/job-board/{slug}",
    extract_items=lambda data: data.get("jobPostings", []),
    title_key="title",
    url_key="jobUrl",
    extract_location=lambda item: item.get("locationName", ""),
    extract_description=lambda item: _strip_html(item.get("descriptionHtml", ""))[:_DESCRIPTION_MAX],
)


# Registry used by callers that dispatch by name (e.g. search_companies).
SPEC_REGISTRY: dict[str, AtsSpec] = {
    "greenhouse": GREENHOUSE_SPEC,
    "lever": LEVER_SPEC,
    "ashby": ASHBY_SPEC,
}


# ── Generic connector ────────────────────────────────────────────────────────

class AtsConnector:
    """Slug-based ATS connector parametrised by an :class:`AtsSpec`.

    Note that this is *not* a ``BaseSearchProvider`` — ATS connectors take a
    company slug rather than a query string, so they have a different
    contract (``fetch`` instead of ``search``).
    """

    def __init__(self, spec: AtsSpec, cfg: dict) -> None:
        self.spec = spec
        self.cfg = cfg

    def fetch(self, slug: str, location_keywords: list[str] | None = None) -> list[dict]:
        """Fetch all open jobs for the company ``slug`` from this ATS.

        Args:
            slug: ATS-specific company identifier (e.g. ``"openai"`` for
                Greenhouse's ``openai`` job board). Validated against
                ``_SLUG_RE`` before being interpolated into the URL.
            location_keywords: Optional override list of substrings used to
                filter results by location. Defaults to the Paris/France/remote
                set. Pass ``[]`` to disable filtering.

        Returns:
            List of canonical job dicts (one per matching job).
        """
        # Defence-in-depth — slugs come from user-edited JSON
        if not _SLUG_RE.match(slug):
            logger.error("%s: invalid slug '%s' — skipping", self.spec.source, slug)
            return []

        url = self.spec.url_template.format(slug=slug)
        keywords = location_keywords if location_keywords is not None else _DEFAULT_LOCATION_KEYWORDS

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.error("%s: request failed for '%s': %s", self.spec.source, slug, e)
            return []

        jobs: list[dict] = []
        for item in self.spec.extract_items(data):
            location = self.spec.extract_location(item)
            # Filter to roles in our target region. Empty keywords list
            # means "skip the filter entirely".
            if keywords and not any(kw in location.lower() for kw in keywords):
                continue

            job_url = item.get(self.spec.url_key, "")
            jobs.append({
                # job_id is content-addressed — hashing the URL gives us a
                # stable id without needing the ATS to expose one.
                "job_id": hashlib.sha256(job_url.encode()).hexdigest()[:16],
                "title": item.get(self.spec.title_key, ""),
                # ATSes don't echo back the company name, so we derive it
                # from the slug. Title-casing gives "Openai" → "Openai"
                # (acceptable; users can override via the storage layer).
                "company": slug.title(),
                "location": location,
                "url": job_url,
                "description": self.spec.extract_description(item),
                "source": self.spec.source,
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })

        logger.info("%s: '%s' → %d results", self.spec.source, slug, len(jobs))
        return jobs


# ── Convenience constructors used by callers that dispatch by name ───────────

def build_ats_connector(name: str, cfg: dict) -> AtsConnector | None:
    """Return an ``AtsConnector`` for the named ATS, or ``None`` if unknown."""
    spec = SPEC_REGISTRY.get(name)
    if spec is None:
        return None
    return AtsConnector(spec, cfg)
