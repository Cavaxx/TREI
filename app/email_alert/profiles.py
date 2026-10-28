"""Portal profiles for alert-email parsing (§6).

An alert email looks different for every portal, but the *shape* is stable: a
list of blocks, each with a title, a price, a link, and a handful of facts. A
profile declares how to recognise the portal and where its listing links and ids
live. Anything a profile does not declare falls back to the generic patterns.

Adding a portal is data, not code: append a :class:`PortalProfile`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PortalProfile:
    name: str
    sender_domains: tuple[str, ...] = ()
    link_hosts: tuple[str, ...] = ()
    #: Regexes whose first group is the listing id inside a URL.
    listing_id_patterns: tuple[str, ...] = ()
    #: Regexes that positively identify a listing-detail URL.
    listing_url_patterns: tuple[str, ...] = ()
    #: Substrings that, when present, mean "this is an alert mail".
    subject_hints: tuple[str, ...] = ()
    notes: str = ""
    extra: dict = field(default_factory=dict)


#: Order matters: the first profile whose domain/host matches wins.
PROFILES: tuple[PortalProfile, ...] = (
    PortalProfile(
        name="immobiliare_it",
        sender_domains=("immobiliare.it",),
        link_hosts=("immobiliare.it",),
        listing_id_patterns=(r"/annunci/(\d+)", r"[?&]id=(\d+)"),
        listing_url_patterns=(r"/annunci/",),
        subject_hints=("immobiliare", "nuovi annunci", "ricerca salvata"),
        notes="Alert from a saved search; one card per listing.",
    ),
    PortalProfile(
        name="idealista_it",
        sender_domains=("idealista.it", "idealista.com"),
        link_hosts=("idealista.it",),
        listing_id_patterns=(r"/immobile/(\d+)",),
        listing_url_patterns=(r"/immobile/",),
        subject_hints=("idealista", "nuovi immobili"),
    ),
    PortalProfile(
        name="casa_it",
        sender_domains=("casa.it",),
        link_hosts=("casa.it",),
        listing_id_patterns=(r"/annunci/(\d+)", r"[?&]id=(\d+)"),
        listing_url_patterns=(r"/annunci/", r"/immobili/"),
        subject_hints=("casa.it",),
    ),
    PortalProfile(
        name="subito_it",
        sender_domains=("subito.it",),
        link_hosts=("subito.it",),
        listing_id_patterns=(r"/annunci/(\w+)",),
        listing_url_patterns=(r"/annunci/",),
        subject_hints=("subito",),
    ),
    PortalProfile(
        name="generic_portal",
        notes="Fallback profile: any sender, links followed where they look like details.",
        listing_id_patterns=(r"/(\d{6,})",),
    ),
)

GENERIC = PROFILES[-1]


def profile_by_name(name: str) -> PortalProfile:
    for profile in PROFILES:
        if profile.name == name:
            return profile
    return GENERIC


def _domain_of(sender: str) -> str:
    match = re.search(r"@([\w.-]+)", sender or "")
    return (match.group(1) if match else "").lower()


def profile_for(*, sender: str = "", body: str = "", subject: str = "") -> PortalProfile:
    """Resolve the portal profile for an alert message."""
    domain = _domain_of(sender)
    haystack = f"{body}\n{subject}".lower()
    for profile in PROFILES:
        if profile is GENERIC:
            continue
        if any(domain.endswith(d) for d in profile.sender_domains):
            return profile
    for profile in PROFILES:
        if profile is GENERIC:
            continue
        if any(host in haystack for host in profile.link_hosts):
            return profile
    return GENERIC
