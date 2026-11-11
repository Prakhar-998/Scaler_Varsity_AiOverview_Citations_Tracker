"""Decide whether a SERP result cites Varsity, the wider brand, or only competitors."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from rapidfuzz import fuzz

from tracker.checks.base import Reference, SerpResult

from .urls import domain_of, is_google_redirect, normalise_url, unwrap_google_redirect

VARSITY_PREFIX = "interviewbit.com/varsity/blog/"
VARSITY_DOMAIN = VARSITY_PREFIX.split("/", 1)[0]
RANK_URL, RANK_DOMAIN = "url", "domain"
BRAND_DOMAINS = ("interviewbit.com", "scaler.com")
# Path words that mark an InterviewBit/Scaler program (course) page rather than other content
# (topics, articles, events). A heuristic: the cited URL is always shown next to the label.
PROGRAM_PATH_WORDS = ("course", "program", "academy")
PAGE_BLOG, PAGE_PROGRAM, PAGE_OTHER = "blog", "program page", "other page"
# How one of our pages was recognised: its URL; a hidden google.com/goto link whose title matches a
# Varsity blog title; or a hidden link with only the site label ("InterviewBit", "Scaler") to go on.
MATCH_URL, MATCH_TITLE, MATCH_LABEL = "url", "title", "label"
SITE_LABELS = {"interviewbit": "InterviewBit", "interviewbit.com": "InterviewBit",
               "scaler": "Scaler", "scaler.com": "Scaler"}
# token_set_ratio of a hidden InterviewBit reference title vs a Varsity blog title. Google rewrites
# titles: the real fees blog scored 89.5 against its own title; other InterviewBit pages score under 60.
TITLE_MATCH_THRESHOLD = 85
_TITLE_NOISE = re.compile(r"\s*(?:[-|–]\s*(?:interviewbit|scaler)(?:\.com)?)?\s*(?:\.\.\.|…)?\s*$", re.I)


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


def page_kind(url: str) -> str:
    """blog (/varsity/blog/), program page, or other page, for an InterviewBit/Scaler URL; "" otherwise."""
    if not url:
        return ""
    if is_varsity(url):
        return PAGE_BLOG
    if not is_brand(url):
        return ""
    path = normalise_url(url).split("/", 1)[1] if "/" in normalise_url(url) else ""
    return PAGE_PROGRAM if any(w in path for w in PROGRAM_PATH_WORDS) else PAGE_OTHER


def brand_site(url: str) -> str:
    """InterviewBit (the Varsity blog included) or Scaler, for one of our URLs; "" otherwise."""
    d = domain_of(url) if url else ""
    if _on_domain(d, "interviewbit.com"):
        return "InterviewBit"
    return "Scaler" if _on_domain(d, "scaler.com") else ""


@dataclass
class OurPage:
    """One Varsity/InterviewBit/Scaler page an AI Overview cited."""
    url: str                  # the page URL; the matched blog URL for a title match; "" for a label match
    position: Optional[int]
    site: str                 # InterviewBit (the Varsity blog included) or Scaler
    match: str = MATCH_URL

    @property
    def name(self) -> str:
        return self.url or f"{self.site} page (URL hidden)"


def match_blog_title(title: str, blogs: Sequence) -> Optional[object]:
    """The Varsity blog (anything with .title and .url) whose title best matches a reference title, if
    it scores TITLE_MATCH_THRESHOLD or more. Site suffixes and a trailing ellipsis are ignored."""
    clean = _TITLE_NOISE.sub("", title or "").strip().lower()
    if len(clean.split()) < 3:
        return None
    best, best_score = None, 0.0
    for b in blogs:
        if not is_varsity(getattr(b, "url", "")):
            continue
        score = fuzz.token_set_ratio(clean, b.title.lower())
        if score > best_score:
            best, best_score = b, score
    return best if best_score >= TITLE_MATCH_THRESHOLD else None


def _our_page(ref: Reference, blogs: Sequence) -> Optional[OurPage]:
    if is_varsity(ref.url) or is_brand(ref.url):
        return OurPage(ref.url, ref.position, brand_site(ref.url))
    if not (is_google_redirect(ref.url) and unwrap_google_redirect(ref.url) is None):
        return None
    site = SITE_LABELS.get((ref.source or "").strip().lower())
    if site is None:
        return None
    # only InterviewBit-labelled links can be a Varsity blog; Scaler titles resembling a blog title
    # (e.g. Scaler's "AI Engineer Roadmap 2026") would be false blog citations
    blog = match_blog_title(ref.title, blogs) if site == "InterviewBit" else None
    if blog is not None:
        return OurPage(blog.url, ref.position, site, MATCH_TITLE)
    return OurPage("", ref.position, site, MATCH_LABEL)


@dataclass
class CitationMatch:
    varsity_cited: bool = False
    cited_url: str = ""
    position: Optional[int] = None  # position of first Varsity reference in the AIO
    varsity_match: str = ""  # MATCH_URL or MATCH_TITLE for that reference
    brand_cited: bool = False
    brand_urls: List[str] = field(default_factory=list)
    brand_position: Optional[int] = None  # position of the first other InterviewBit/Scaler reference
    # every Varsity/InterviewBit/Scaler page cited, in reference order, once each
    our_pages: List[OurPage] = field(default_factory=list)
    organic_rank: Optional[int] = None  # best organic rank of any Varsity page
    # "url": a /varsity/blog/ URL was seen. "domain": only the interviewbit.com domain was visible
    # (opaque Google redirect), so it could be another interviewbit.com page. "" when not found.
    rank_match: str = ""
    competing_domains: List[str] = field(default_factory=list)


def match_citations(result: SerpResult, blogs: Sequence = ()) -> CitationMatch:
    """`blogs` (Varsity blogs with .title and .url) lets a hidden InterviewBit link count as a blog by title."""
    m = CitationMatch()
    seen = set()
    for ref in result.references:
        page = _our_page(ref, blogs)
        if page is not None:
            key = normalise_url(page.url) if page.url else ref.url  # hidden links: one per goto link
            if key not in seen:
                seen.add(key)
                m.our_pages.append(page)
            if page.url and is_varsity(page.url):
                if not m.varsity_cited:
                    m.varsity_cited, m.cited_url, m.position, m.varsity_match = True, page.url, ref.position, page.match
            else:
                if not m.brand_cited:
                    m.brand_cited, m.brand_position = True, ref.position
                if page.url:
                    m.brand_urls.append(page.url)
            continue
        if is_google_redirect(ref.url) and unwrap_google_redirect(ref.url) is None:
            # opaque google.com/goto link from another site: the real domain is unknown, so it is
            # not counted as "google.com"
            continue
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
