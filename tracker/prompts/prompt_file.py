"""Candidate files: drafted LLM candidates are saved to candidates.csv before any probe SERP calls.

Columns: prompt, cluster, source, intent, source blog
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .generate import Candidate

CANDIDATE_HEADERS = ["prompt", "cluster", "source", "intent", "source blog"]


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


__all__ = ["CANDIDATE_HEADERS", "CandidateLog"]
