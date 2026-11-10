"""Fake report inputs for `report --sample` (portfolio screenshots).

Everything here is invented: prompts, impressions, results, and competitor domains, which use the
reserved .example TLD so they can't be mistaken for real sites. The data goes through the same
build_report as real runs, so the sample shows exactly what the real dashboard renders.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import List, Tuple

from tracker.config import Blog
from tracker.output import CHECK_HEADERS, PROMPT_HEADERS

from .data import RunInputs

VARSITY = "https://www.interviewbit.com/varsity/blog/"
COMPETITORS = ["coursehub.example", "skillpath.example", "learnwise.example", "videotube.example",
               "techforum.example", "careerbase.example", "upskill.example", "devacademy.example",
               "salarywatch.example", "jobsboard.example"]

# (prompt, cluster, source, brand tag, intent, impressions, chance Varsity is cited)
PROMPTS: List[Tuple[str, str, str, str, str, int, float]] = [
    ("forward deployed engineer course fees", "fde", "gsc", "", "fees", 640, 0.7),
    ("best forward deployed engineer course in india", "fde", "gsc", "", "best", 520, 0.2),
    ("fde certification cost india", "fde", "llm", "", "fees", 0, 0.1),
    ("devacademy fde program review", "fde", "gsc", "competitor", "worth", 210, 0.0),
    ("forward deployed engineer salary in india", "fde", "gsc", "", "salary", 880, 0.5),
    ("is forward deployed engineering worth it", "fde", "llm", "", "worth", 0, 0.3),
    ("forward deployed engineer vs software engineer", "fde", "gsc", "", "comparison", 300, 0.6),
    ("fde course for working professionals", "fde", "manual", "", "audience", 0, 0.0),
    ("generative ai course fees in india", "genai", "gsc", "", "fees", 410, 0.2),
    ("best generative ai course in india", "genai", "gsc", "", "best", 950, 0.1),
    ("top genai certification online", "genai", "llm", "", "best", 0, 0.0),
    ("generative ai course for beginners", "genai", "gsc", "", "audience", 260, 0.4),
    ("agentic ai course fees", "genai", "gsc", "", "fees", 120, 0.0),
    ("coursehub genai course vs skillpath", "genai", "gsc", "competitor", "comparison", 90, 0.0),
    ("is a generative ai course worth it", "genai", "llm", "", "worth", 0, 0.5),
    ("prompt engineering course online india", "genai", "gsc", "", "course", 180, 0.2),
]

BLOGS = [
    Blog("Forward Deployed Engineer Course Fees in India", "fde course fees", VARSITY + "fde-course-fees/", "fde"),
    Blog("Best FDE Training Programs in India", "best fde course", VARSITY + "best-fde-courses/", "fde"),
    Blog("Forward Deployed Engineer Salary in India", "fde salary india", VARSITY + "fde-salary/", "fde"),
    Blog("FDE vs Software Engineer: Key Differences", "fde vs software engineer", VARSITY + "fde-vs-swe/", "fde"),
    Blog("Generative AI Course Fees Compared", "generative ai course fees", VARSITY + "genai-course-fees/", "genai"),
    Blog("10 Best Generative AI Courses in India", "best generative ai course", VARSITY + "best-genai-courses/",
         "genai"),
    Blog("Generative AI for Beginners: Where to Start", "generative ai beginners", VARSITY + "genai-beginners/",
         "genai"),
]


def sample_inputs(days: int = 6, seed: int = 7) -> Tuple[RunInputs, List[Blog]]:
    rng = random.Random(seed)
    start = date(2026, 11, 4)
    prompts, checks = [], []
    for p, cluster, source, tag, intent, imp, cite in PROMPTS:
        row = dict.fromkeys(PROMPT_HEADERS, "")
        inputs = ([f"gsc: {p} ({imp} impr)"] if imp else []) + (["probe: AI Overview shown"] if not imp else [])
        row.update({"prompt": p, "cluster": cluster, "source": source, "brand tag": tag, "intent": intent,
                    "score": "", "status": "active", "reason": "kept", "source inputs": "; ".join(inputs)})
        prompts.append(row)
    for day in range(days):
        d = start + timedelta(days=day)
        lift = 0.06 * day  # citation chances improve a little each run, so the trend has a shape
        for slot, time in enumerate(("06:10", "18:40")):
            for p, cluster, *_rest, cite in PROMPTS:
                r = rng.random()
                status = "ok" if r < 0.86 else "no_aio" if r < 0.94 else "incomplete" if r < 0.98 else "failed"
                cited = status == "ok" and rng.random() < min(cite + lift, 0.95)
                pos = rng.randint(1, 6) if cited else ""
                slug = next((b.url for b in BLOGS if b.cluster == cluster and
                             any(w in p for w in b.primary_keyword.split()[-2:])), VARSITY + "genai-beginners/")
                comps = rng.sample(COMPETITORS, rng.randint(2, 5)) if status == "ok" else []
                rank = rng.choice(["", "", 3, 5, 8, 12]) if status != "failed" else ""
                checks.append({**dict.fromkeys(CHECK_HEADERS, ""), **{
                    "date": d.isoformat(), "time": time, "prompt": p, "cluster": cluster, "device": "desktop",
                    "ai overview shown": "yes" if status in ("ok", "incomplete") else "" if status == "failed" else "no",
                    "varsity cited": ("yes" if cited else "no") if status == "ok" else "",
                    "cited url": slug if cited else "", "position": pos,
                    "brand cited": ("yes" if rng.random() < 0.08 else "no") if status == "ok" else "",
                    "organic rank": rank, "rank match": "url" if rank else "",
                    "competing domains": ", ".join(comps), "check status": status,
                    "error": "HTTP 503" if status == "failed" else ""}})
    n_checks = days * 2 * len(PROMPTS)
    costs = [{"provider": "dataforseo", "checks": str(len(PROMPTS)), "estimated usd": f"{len(PROMPTS) * 0.004:.4f}",
              "billed usd": f"{len(PROMPTS) * 0.0034:.4f}"} for _ in range(n_checks // len(PROMPTS))]
    return RunInputs(prompts, "sample data", checks, costs), BLOGS


__all__ = ["sample_inputs"]
