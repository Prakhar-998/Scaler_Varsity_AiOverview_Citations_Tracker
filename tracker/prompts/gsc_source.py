"""GSC-grounded candidates: real queries with impressions, no LLM. Always available."""
from __future__ import annotations

import logging
import re
from typing import List

from rapidfuzz import fuzz

from tracker.config import BrandRules, Cluster, GscQuery

from .generate import Candidate, classify_intent

log = logging.getLogger(__name__)

BOFU_TERMS = ("best", "top", "course", "courses", "fees", "fee", "cost", "price", "vs", "versus", "worth",
              "certification", "certificate", "salary", "training", "program", "programme", "institute",
              "online", "review", "reviews", "compare", "which", "near me")
_BOFU_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in BOFU_TERMS) + r")(?![a-z0-9])")


def bofu_term(query: str) -> str:
    m = _BOFU_RE.search(query.lower())
    return m.group(1) if m else ""


def tag_brands(candidates: List[Candidate], rules: BrandRules) -> None:
    """Mark own-brand candidates for dropping and tag competitor-brand ones. Works for any source."""
    for c in candidates:
        if c.pre_drop:
            continue
        own = rules.brand(c.prompt)
        if own:
            c.pre_drop = f"branded query ('{own}')"
        elif rules.competitor(c.prompt):
            c.competitor = True


def gsc_candidates(gsc: List[GscQuery], clusters: List[Cluster], rules: BrandRules,
                   dedupe_threshold: float = 90) -> List[Candidate]:
    """Queries in an active cluster that carry a BOFU term, ranked by impressions.

    Branded and near-duplicate queries are returned with `pre_drop` set so they are logged as
    dropped with a reason. Queries outside a cluster or without a BOFU term are not candidates.
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
    for c in out:  # highest impressions first, so the stronger query survives
        if c.pre_drop:
            continue
        dup = next((k for k in kept if fuzz.token_sort_ratio(c.prompt, k.prompt) >= dedupe_threshold), None)
        if dup is not None:
            c.pre_drop = f"near-duplicate of higher-impression GSC query '{dup.prompt}'"
        else:
            kept.append(c)
    log.info("GSC source: %d of %d queries in active clusters with a BOFU term; %d kept after brand "
             "filter and dedupe (%d competitor)", len(pool), len(gsc), len(kept), sum(c.competitor for c in kept))
    return out
