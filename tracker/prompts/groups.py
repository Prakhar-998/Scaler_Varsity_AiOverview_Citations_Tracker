"""Intent groups: near-duplicate searches that differ only in synonyms, plurals or word order.

  gen ai / genai / generative ai -> genai      best / top -> best
  course / courses -> course                  fees / fee / cost / price -> fees

"fde academy fees", "fde academy cost", "fde.academy price" and "fees fde academy" share one group.
At most `max_per_group` prompts per group are kept (highest impressions or score first); a candidate in a
new group is still dropped when it is a fuzzy near-duplicate of another group.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz

SYNONYMS = [
    (re.compile(r"\b(generative ai|gen ai|genai)\b"), "genai"),
    (re.compile(r"\btop\b"), "best"),
    (re.compile(r"\bcourses\b"), "course"),
    (re.compile(r"\b(fees|fee|cost|price)\b"), "fees"),
]
_PUNCT = re.compile(r"[^a-z0-9]+")


def intent_key(prompt: str) -> str:
    """Canonical, order-free form of a search: punctuation dropped, synonyms folded, tokens sorted."""
    text = _PUNCT.sub(" ", prompt.lower()).strip()
    for pattern, canonical in SYNONYMS:
        text = pattern.sub(canonical, text)
    return " ".join(sorted(text.split()))


class IntentGroups:
    """Feed candidates best first; `check` says whether the next one may be kept, `add` records it."""

    def __init__(self, max_per_group: int = 2, threshold: float = 90):
        self.max_per_group = max_per_group
        self.threshold = threshold
        self.groups: Dict[str, List[str]] = {}

    def check(self, prompt: str) -> Tuple[Optional[str], str]:
        """(None, "") if the prompt may be kept, else (the kept prompt it duplicates, reason)."""
        key = intent_key(prompt)
        members = self.groups.get(key)
        if members is not None:
            if len(members) < self.max_per_group:
                return None, ""
            kept = " and ".join(f"'{m}'" for m in members)
            return members[0], f"intent group full ({self.max_per_group} kept: {kept})"
        for other, kept_members in self.groups.items():
            if fuzz.token_sort_ratio(key, other) >= self.threshold:
                return kept_members[0], f"near-duplicate of '{kept_members[0]}'"
        return None, ""

    def add(self, prompt: str) -> None:
        self.groups.setdefault(intent_key(prompt), []).append(prompt)


__all__ = ["IntentGroups", "SYNONYMS", "intent_key"]
