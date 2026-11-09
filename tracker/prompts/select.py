"""Code-only filter, scoring and dedupe for drafted candidates.

A candidate survives only if it closely matches a real GSC query with impressions, or one
probe SERP check shows an AI Overview. Every candidate gets a decision and a reason.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rapidfuzz import fuzz, process

from tracker.checks.base import STATUS_FAILED, STATUS_INCOMPLETE, BudgetExceeded, SerpProvider
from tracker.config import Cluster, GscQuery

from .generate import Candidate

W_INTENT, W_DEMAND, W_AIO, W_PROGRAM = 0.35, 0.30, 0.25, 0.10
MIN_WORDS, MAX_WORDS = 3, 12
MIN_WORDS_GSC = 2  # real queries like "fde salary" are short but genuine

ACTIVE, DROPPED = "active", "dropped"


@dataclass
class Decision:
    candidate: Candidate
    status: str = DROPPED
    reason: str = ""
    gsc_query: str = ""
    gsc_impressions: int = 0
    gsc_similarity: float = 0.0
    probed: bool = False
    probe_aio: Optional[bool] = None
    score: float = 0.0
    sources: List[str] = field(default_factory=list)

    @property
    def prompt(self) -> str:
        return self.candidate.prompt


def _note_overlap(kept: Decision, dropped: Decision) -> None:
    """When sources overlap (an LLM draft duplicates a GSC query), say so on the survivor."""
    src = dropped.candidate.source
    note = f"also from {src}"
    if src != kept.candidate.source and note not in kept.sources:
        kept.sources.append(note)


def _best_gsc_match(prompt: str, gsc: List[GscQuery], threshold: float):
    if not gsc:
        return None, 0.0
    hit = process.extractOne(prompt, [g.query for g in gsc], scorer=fuzz.token_sort_ratio)
    if hit and hit[1] >= threshold:
        return gsc[hit[2]], float(hit[1])
    return None, float(hit[1]) if hit else 0.0


def _demand(impressions: int, max_impressions: int) -> float:
    if impressions <= 0 or max_impressions <= 0:
        return 0.0
    return math.log1p(impressions) / math.log1p(max_impressions)


def score(d: Decision, max_impressions: int, cluster_weight: float, max_weight: float) -> float:
    aio = 1.0 if d.probe_aio else 0.0
    program = cluster_weight / max_weight if max_weight else 0.0
    return round(
        W_INTENT * d.candidate.intent_weight
        + W_DEMAND * _demand(d.gsc_impressions, max_impressions)
        + W_AIO * aio
        + W_PROGRAM * program,
        4,
    )


def select_prompts(
    candidates: List[Candidate],
    gsc: List[GscQuery],
    clusters: List[Cluster],
    provider: Optional[SerpProvider],
    settings: Dict,
) -> List[Decision]:
    """Return a Decision for every candidate, in the order: kept (by score), then dropped."""
    gsc_threshold = float(settings.get("gsc_match_threshold", 85))
    dedupe_threshold = float(settings.get("dedupe_threshold", 90))
    keep_top = int(settings.get("keep_top", 50))
    probe = bool(settings.get("probe_unmatched", True)) and provider is not None

    weights = {c.key: c.weight for c in clusters}
    max_weight = max(weights.values(), default=1.0)
    max_imp = max((g.impressions for g in gsc), default=0)

    decisions = [Decision(candidate=c, sources=[f"blog: {c.source_blog}"] if c.source_blog else [])
                 for c in candidates]
    pending: List[Decision] = []

    # 1. sanity + exact duplicates
    seen: Dict[str, Decision] = {}
    for d in decisions:
        n = len(d.prompt.split())
        min_words = MIN_WORDS_GSC if d.candidate.source == "gsc" else MIN_WORDS
        if d.candidate.pre_drop:
            d.reason = d.candidate.pre_drop
        elif not min_words <= n <= MAX_WORDS:
            d.reason = f"length {n} words outside {min_words}-{MAX_WORDS}"
        elif d.prompt in seen:
            d.reason = "exact duplicate"
            _note_overlap(seen[d.prompt], d)
        else:
            seen[d.prompt] = d
            pending.append(d)

    # 2. GSC match (same cluster first, then any)
    for d in pending:
        same = [g for g in gsc if g.cluster == d.candidate.cluster]
        hit, sim = _best_gsc_match(d.prompt, same, gsc_threshold)
        if hit is None:
            hit, sim = _best_gsc_match(d.prompt, gsc, gsc_threshold)
        d.gsc_similarity = sim
        if hit is not None:
            d.gsc_query, d.gsc_impressions = hit.query, hit.impressions
            d.sources.append(f"gsc: {hit.query} ({hit.impressions} impr)")

    # 3. dedupe before probing so we never pay for near-duplicates
    pending.sort(key=lambda d: score(d, max_imp, weights.get(d.candidate.cluster, 0), max_weight), reverse=True)
    unique: List[Decision] = []
    for d in pending:
        dup = next((u for u in unique if fuzz.token_sort_ratio(d.prompt, u.prompt) >= dedupe_threshold), None)
        if dup is not None:
            d.reason = f"near-duplicate of '{dup.prompt}'"
            _note_overlap(dup, d)
        else:
            unique.append(d)

    # 4. probe candidates with no GSC match
    survivors: List[Decision] = []
    budget_hit = False
    for d in unique:
        if d.gsc_query:
            survivors.append(d)
            continue
        if not probe:
            d.reason = "no GSC match; probing disabled"
            continue
        if budget_hit:
            d.reason = "no GSC match; not probed (SERP budget used up)"
            continue
        try:
            res = provider.fetch_ai_overview(d.prompt)
        except BudgetExceeded:
            budget_hit = True
            d.reason = "no GSC match; not probed (SERP budget used up)"
            continue
        d.probed = True
        if res.status == STATUS_FAILED:
            d.reason = f"no GSC match; probe failed ({res.error})"
        elif res.shown:
            d.probe_aio = True
            note = " (sources not loaded)" if res.status == STATUS_INCOMPLETE else ""
            d.sources.append(f"probe: AI Overview shown{note}")
            survivors.append(d)
        else:
            d.probe_aio = False
            d.reason = f"no GSC match (best {d.gsc_similarity:.0f}); probe showed no AI Overview"

    # 5. score and keep the top N
    for d in survivors:
        d.score = score(d, max_imp, weights.get(d.candidate.cluster, 0), max_weight)
    survivors.sort(key=lambda d: d.score, reverse=True)
    for i, d in enumerate(survivors):
        why = "GSC match" if d.gsc_query else "AI Overview on probe"
        if i < keep_top:
            d.status, d.reason = ACTIVE, f"kept: {why}"
        else:
            d.reason = f"{why} but below top {keep_top} by score"

    kept = [d for d in survivors if d.status == ACTIVE]
    return kept + [d for d in decisions if d.status != ACTIVE]
