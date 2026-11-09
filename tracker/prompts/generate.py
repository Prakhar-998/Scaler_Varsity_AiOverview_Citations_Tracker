"""Draft BOFU candidate searches per blog. Gemini only drafts; code decides what survives."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from tracker.config import Blog, Cluster

INTENT_PATTERNS = [
    # (intent, regex, buying-intent weight 0..1)
    ("fees", r"\b(fees?|cost|price|pricing)\b", 1.0),
    ("best", r"\b(best|top)\b", 0.9),
    ("comparison", r"\b(vs\.?|versus|or)\b", 0.8),
    ("worth", r"\bworth\b", 0.8),
    ("audience", r"\bfor (working professionals|freshers|beginners|non[- ]tech)\b", 0.7),
    ("course", r"\b(course|program|certification|bootcamp)\b", 0.6),
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
    """Pull the list out of a model reply, tolerating code fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data.get("searches", []) if isinstance(data, dict) else data
    return [s for s in items if isinstance(s, str)] if isinstance(items, list) else []


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class GeminiModelNotFound(RuntimeError):
    """The configured Gemini model returned 404 (retired, misspelled, or not enabled for this key)."""

    def __init__(self, model: str, detail: str = ""):
        self.model = model
        msg = f"Gemini model '{model}' returned 404 (not found or no longer available)."
        detail = detail.strip()
        if detail:
            msg += f" Gemini said: {detail}" + ("" if detail.endswith((".", "!", "?")) else ".")
        msg += " Set GEMINI_MODEL in .env, or gemini_model in config/settings.yaml, to an available model."
        super().__init__(msg)


def resolve_gemini_model(settings: Dict) -> str:
    """GEMINI_MODEL env var, then gemini_model in settings.yaml, then the default."""
    return os.environ.get("GEMINI_MODEL", "").strip() or settings.get("gemini_model") or DEFAULT_GEMINI_MODEL


class GeminiDrafter:
    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        from google import genai  # imported lazily so offline tests don't need it

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def draft(self, blog: Blog, cluster: Cluster, n: int) -> List[str]:
        prompt = PROMPT_TEMPLATE.format(cluster=cluster.name, title=blog.title, keyword=blog.primary_keyword, n=n)
        from google.genai import errors

        try:
            resp = self.client.models.generate_content(
                model=self.model, contents=prompt, config={"response_mime_type": "application/json"}
            )
        except errors.ClientError as exc:
            if exc.code == 404:
                raise GeminiModelNotFound(self.model, exc.message or "") from exc
            raise
        return parse_searches(resp.text or "")


def draft_candidates(blogs: List[Blog], clusters: List[Cluster], drafter, per_blog: int) -> List[Candidate]:
    by_key: Dict[str, Cluster] = {c.key: c for c in clusters}
    out: List[Candidate] = []
    for blog in blogs:
        cluster: Optional[Cluster] = by_key.get(blog.cluster or "")
        if cluster is None:
            continue  # blog not in an active cluster
        for raw in drafter.draft(blog, cluster, per_blog):
            prompt = clean_prompt(raw)
            if not prompt:
                continue
            intent, weight = classify_intent(prompt)
            out.append(Candidate(prompt, cluster.key, blog.title, intent, weight))
    return out
