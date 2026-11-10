"""GSC-grounded candidates: real queries with impressions, no LLM. Always available."""
from __future__ import annotations

import logging
import re
from typing import List

from tracker.config import BrandRules, Cluster, GscQuery

from .generate import Candidate, classify_intent
from .groups import IntentGroups

log = logging.getLogger(__name__)

BOFU_TERMS = ("best", "top", "course", "courses", "fees", "fee", "cost", "price", "vs", "versus", "worth",
              "certification", "certificate", "salary", "training", "program", "programme", "institute",
              "online", "review", "reviews", "compare", "which", "near me")
# Markers that turn an own-brand query into a comparison. "which" and a bare "or" are left out:
# "which scaler program is best" chooses between our own programs.
_COMPARISON_RE = re.compile(r"(?<![a-z0-9])(vs\.?|versus|compared?|comparison|better than|alternatives?|"
                            r"difference between)(?![a-z0-9])")
_BOFU_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in BOFU_TERMS) + r")(?![a-z0-9])")


def bofu_term(query: str) -> str:
    m = _BOFU_RE.search(query.lower())
    return m.group(1) if m else ""


def tag_brands(candidates: List[Candidate], rules: BrandRules) -> None:
    """Tag brand-related candidates. Works for any source.

    own brand + competitor or comparison marker -> kept, brand_tag "brand_comparison"
    own brand only (navigational: login, contact, "scaler reviews", "interviewbit varsity") -> dropped
    competitor only -> kept, brand_tag "competitor"
    """
    for c in candidates:
        if c.pre_drop:
            continue
        own = rules.brand(c.prompt)
        rival = rules.competitor(c.prompt)
        if own and (rival or _COMPARISON_RE.search(c.prompt.lower())):
            c.brand_tag = "brand_comparison"
        elif own:
            c.pre_drop = f"navigational / pure brand query ('{own}')"
        elif rival:
            c.brand_tag = "competitor"


def gsc_candidates(gsc: List[GscQuery], clusters: List[Cluster], rules: BrandRules,
                   dedupe_threshold: float = 90, max_per_group: int = 2) -> List[Candidate]:
    """Queries in an active cluster that carry a BOFU term, ranked by impressions.

    Navigational own-brand and near-duplicate queries are returned with `pre_drop` set so they are logged as
    dropped with a reason. Near-duplicates: at most `max_per_group` queries per intent group (see groups.py),
    and fuzzy matches across groups. Queries outside a cluster or without a BOFU term are not candidates.
    """
    active = {c.key for c in clusters}
    pool = [g for g in gsc if g.cluster in active and bofu_term(g.query)]
    pool.sort(key=lambda g: g.impressions, reverse=True)
    out: List[Candidate] = []
    for g in pool:
        intent, weight = classify_intent(g.query)
        out.append(Candidate(g.query, g.cluster, "", intent, weight, source="gsc", impressions=g.impressions))
    tag_brands(out, rules)

    kept: List[Candidate] = []
    groups = IntentGroups(max_per_group, dedupe_threshold)
    for c in out:  # highest impressions first, so the stronger queries survive
        if c.pre_drop:
            continue
        dup, reason = groups.check(c.prompt)
        if dup is not None:
            c.pre_drop = reason.replace("near-duplicate of", "near-duplicate of higher-impression GSC query")
        else:
            groups.add(c.prompt)
            kept.append(c)
    log.info("GSC source: %d of %d queries in active clusters with a BOFU term; %d kept after brand "
             "filter and dedupe (%d competitor, %d brand_comparison)", len(pool), len(gsc), len(kept),
             sum(c.brand_tag == "competitor" for c in kept), sum(c.brand_tag == "brand_comparison" for c in kept))
    return out
