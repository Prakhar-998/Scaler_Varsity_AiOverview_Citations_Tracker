"""Candidate files: drafted LLM candidates saved before probing, and prompt files loaded back in.

`candidates.csv` (written by select) and hand-made prompt files share one format:
  prompt, cluster                     required
  source, intent, source blog         optional; source defaults to "manual", intent to classify_intent
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterable, List

from tracker.config import Cluster

from .generate import INTENT_PATTERNS, OTHER_INTENT_WEIGHT, Candidate, classify_intent, clean_prompt

log = logging.getLogger(__name__)

CANDIDATE_HEADERS = ["prompt", "cluster", "source", "intent", "source blog"]
INTENT_WEIGHTS = {intent: weight for intent, _, weight in INTENT_PATTERNS}


class CandidateLog:
    """Appends candidates to a CSV as they are drafted, so an interrupted run keeps them."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", newline="") as f:
            csv.writer(f).writerow(CANDIDATE_HEADERS)
        self.count = 0

    def add(self, candidates: Iterable[Candidate]) -> None:
        rows = [[c.prompt, c.cluster, c.source, c.intent, c.source_blog] for c in candidates]
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerows(rows)
        self.count += len(rows)


def load_prompt_file(path: Path, clusters: List[Cluster]) -> List[Candidate]:
    """Read a prompt file. Rows with no prompt, or a cluster that isn't active, are skipped with a warning."""
    keys = {c.key for c in clusters}
    out: List[Candidate] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = {(h or "").strip().lower() for h in reader.fieldnames or []}
        if not {"prompt", "cluster"} <= fields:
            raise ValueError(f"{path}: needs 'prompt' and 'cluster' columns (found: {', '.join(sorted(fields))})")
        for n, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            prompt, cluster = clean_prompt(row.get("prompt", "")), row.get("cluster", "").lower()
            if not prompt:
                continue
            if cluster not in keys:
                log.warning("%s line %d: cluster %r is not an active cluster; skipped: %s", path, n, cluster, prompt)
                continue
            intent = row.get("intent", "").lower()
            if intent in INTENT_WEIGHTS or intent == "other":
                weight = INTENT_WEIGHTS.get(intent, OTHER_INTENT_WEIGHT)
            else:
                intent, weight = classify_intent(prompt)
            out.append(Candidate(prompt, cluster, row.get("source blog", ""), intent, weight,
                                 source=row.get("source", "").lower() or "manual"))
    log.info("%d prompts loaded from %s", len(out), path)
    return out


__all__ = ["CANDIDATE_HEADERS", "CandidateLog", "load_prompt_file"]
