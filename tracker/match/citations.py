"""Decide whether a SERP result cites Varsity, the wider brand, or only competitors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from tracker.checks.base import SerpResult

from .urls import domain_of, normalise_url

VARSITY_PREFIX = "interviewbit.com/varsity/blog/"
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
    for org in result.organic:
        if is_varsity(org.url):
            m.organic_rank = org.position if m.organic_rank is None else min(m.organic_rank, org.position)
    return m
