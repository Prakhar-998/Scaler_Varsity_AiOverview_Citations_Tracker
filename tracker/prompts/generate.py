"""Draft BOFU candidate searches per blog. Gemini only drafts; code decides what survives."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from tracker.config import Blog, Cluster
from tracker.llm import AllLLMsFailed, LLMChain
from tracker.llm.gemini import DEFAULT_GEMINI_MODEL, GeminiModelNotFound, resolve_gemini_model  # noqa: F401

log = logging.getLogger(__name__)

INTENT_PATTERNS = [
    # (intent, regex, buying-intent weight 0..1)
    ("fees", r"\b(fees?|cost|price|pricing)\b", 1.0),
    ("best", r"\b(best|top)\b", 0.9),
    ("comparison", r"\b(vs\.?|versus|or|compare|comparison|which)\b", 0.8),
    ("worth", r"\b(worth|reviews?)\b", 0.8),
    ("audience", r"\bfor (working professionals|freshers|beginners|non[- ]tech)\b", 0.7),
    ("course", r"\b(courses?|program|programme|certification|certificate|bootcamp|training|institute|online)\b"
               r"|\bnear me\b", 0.6),
    ("salary", r"\bsalary\b", 0.5),
]
OTHER_INTENT_WEIGHT = 0.2

PROMPT_TEMPLATE = """You write Google searches that Indian learners type when they are close to choosing a
course or career program. Topic cluster: {cluster}.

Blog title: {title}
Primary keyword: {keyword}

Write {n} distinct searches in these styles where they fit naturally:
"best X course in India", "X vs Y", "is X worth it", "X course fees", "X for working professionals".
Rules: lowercase, 3-10 words, no brand names, no quotes, no question marks, realistic phrasing.
Return JSON only: {{"searches": ["...", "..."]}}"""


@dataclass
class Candidate:
    prompt: str
    cluster: str
    source_blog: str
    intent: str = "other"
    intent_weight: float = OTHER_INTENT_WEIGHT
    source: str = "llm"        # "llm" (drafted per blog) or "gsc" (real GSC query)
    # "competitor": names a competitor ("fde academy course fees")
    # "brand_comparison": our brand compared with another ("scaler vs fde academy")
    brand_tag: str = ""
    impressions: int = 0       # GSC impressions, for gsc-sourced candidates
    pre_drop: str = ""         # reason the source already rejected it; select_prompts logs it as dropped


def classify_intent(prompt: str):
    for intent, pattern, weight in INTENT_PATTERNS:
        if re.search(pattern, prompt):
            return intent, weight
    return "other", OTHER_INTENT_WEIGHT


def clean_prompt(text: str) -> str:
    text = text.strip().strip("\"'`").lower()
    text = re.sub(r"[?!.]+$", "", text)
    return re.sub(r"\s+", " ", text)


def parse_searches(text: str) -> List[str]:
    """Pull the list out of a model reply, tolerating code fences and prose around the JSON."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        try:
            data = json.loads(match.group(0)) if match else None
        except ValueError:
            data = None
    if data is None:
        return []
    items = data.get("searches", []) if isinstance(data, dict) else data
    return [s for s in items if isinstance(s, str)] if isinstance(items, list) else []


class LLMDrafter:
    """Drafts searches for one blog through an LLMChain. Returns (searches, label that answered)."""

    def __init__(self, chain: LLMChain):
        self.chain = chain

    def draft(self, blog: Blog, cluster: Cluster, n: int) -> Tuple[List[str], str]:
        prompt = PROMPT_TEMPLATE.format(cluster=cluster.name, title=blog.title, keyword=blog.primary_keyword, n=n)
        text, label = self.chain.complete(prompt)
        return parse_searches(text), label


def draft_candidates(blogs: List[Blog], clusters: List[Cluster], drafter, per_blog: int,
                     on_drafted: Optional[Callable[[List[Candidate]], None]] = None) -> List[Candidate]:
    """Draft for every blog in an active cluster. If every LLM option fails, log a warning and
    return what was drafted so far; the caller carries on with GSC candidates.
    `on_drafted` gets each blog's new candidates as soon as they exist (select saves them to disk)."""
    by_key: Dict[str, Cluster] = {c.key: c for c in clusters}
    todo = [(b, by_key[b.cluster]) for b in blogs if b.cluster in by_key]
    out: List[Candidate] = []
    for i, (blog, cluster) in enumerate(todo, start=1):
        try:
            searches, label = drafter.draft(blog, cluster, per_blog)
        except AllLLMsFailed as exc:
            log.warning("LLM drafting stopped at blog %d/%d (%s); continuing with %d LLM candidates "
                        "plus GSC candidates", i, len(todo), exc, len(out))
            break
        before = len(out)
        for raw in searches:
            prompt = clean_prompt(raw)
            if not prompt:
                continue
            intent, weight = classify_intent(prompt)
            out.append(Candidate(prompt, cluster.key, blog.title, intent, weight, source="llm"))
        if on_drafted is not None:
            on_drafted(out[before:])
        log.info("drafting %d/%d via %s: %s -> %d candidates", i, len(todo), label, blog.title, len(out) - before)
    return out
