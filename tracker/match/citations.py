"""Decide whether a SERP result cites Varsity, the wider brand, or only competitors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from tracker.checks.base import SerpResult

from .urls import domain_of, normalise_url, unwrap_google_redirect

VARSITY_PREFIX = "interviewbit.com/varsity/blog/"
VARSITY_DOMAIN = VARSITY_PREFIX.split("/", 1)[0]
RANK_URL, RANK_DOMAIN = "url", "domain"
BRAND_DOMAINS = ("interviewbit.com", "scaler.com")


def _on_domain(domain: str, root: str) -> bool:
    return domain == root or domain.endswith("." + root)


def is_varsity(url: str) -> bool:
    return (normalise_url(url) + "/").startswith(VARSITY_PREFIX)


def is_brand(url: str) -> bool:
    """Other interviewbit.com / scaler.com pages. Varsity blog pages are not counted here."""
    if is_varsity(url):
        return False
    d = domain_of(url)
    return any(_on_domain(d, root) for root in BRAND_DOMAINS)


@dataclass
class CitationMatch:
    varsity_cited: bool = False
    cited_url: str = ""
    position: Optional[int] = None  # position of first Varsity reference in the AIO
    brand_cited: bool = False
    brand_urls: List[str] = field(default_factory=list)
    organic_rank: Optional[int] = None  # best organic rank of any Varsity page
    # "url": a /varsity/blog/ URL was seen. "domain": only the interviewbit.com domain was visible
    # (opaque Google redirect), so it could be another interviewbit.com page. "" when not found.
    rank_match: str = ""
    competing_domains: List[str] = field(default_factory=list)


def match_citations(result: SerpResult) -> CitationMatch:
    m = CitationMatch()
    for ref in result.references:
        if is_varsity(ref.url):
            if not m.varsity_cited:
                m.varsity_cited, m.cited_url, m.position = True, ref.url, ref.position
        elif is_brand(ref.url):
            m.brand_cited = True
            m.brand_urls.append(ref.url)
        else:
            d = domain_of(ref.url)
            if d and d not in m.competing_domains:
                m.competing_domains.append(d)
    url_rank, domain_rank = _varsity_organic_ranks(result)
    # An exact URL match beats a domain-only match, even a higher-ranked one.
    if url_rank is not None:
        m.organic_rank, m.rank_match = url_rank, RANK_URL
    elif domain_rank is not None:
        m.organic_rank, m.rank_match = domain_rank, RANK_DOMAIN
    return m


def _varsity_organic_ranks(result: SerpResult):
    url_rank = domain_rank = None
    for org in result.organic:
        dest = unwrap_google_redirect(org.url)
        if dest is not None:
            if is_varsity(dest):
                url_rank = org.position if url_rank is None else min(url_rank, org.position)
        elif org.display_url and _on_domain(domain_of(org.display_url), VARSITY_DOMAIN):
            domain_rank = org.position if domain_rank is None else min(domain_rank, org.position)
    return url_rank, domain_rank
