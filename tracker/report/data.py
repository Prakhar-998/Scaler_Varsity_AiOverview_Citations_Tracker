"""Build the dashboard's data from select and check outputs.

Inputs are plain rows (dicts keyed by the CSV headers), so the same builder works for the runs/
CSVs today and the Sheets tabs later. `load_runs` reads runs/; `build_report` is pure.

Definitions match the Summary tab (tracker/output/rows.py): failed checks are left out of every
rate, incomplete AI Overviews count as shown but not in the citation rate.

Every number except spend uses only checks of the current active prompt set. Checks are tagged with
the set they ran on (`prompt set`); older checks without the tag count as the active set only when
their run checked exactly the active prompts. Everything else (check --prompts test runs, earlier
prompt sets) is listed under `other_runs`.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from rapidfuzz import fuzz

from tracker.config import Blog, Cluster
from tracker.prompts.generate import INTENT_PATTERNS, OTHER_INTENT_WEIGHT, classify_intent
from tracker.prompts.prompt_file import prompt_set_id

INTENT_WEIGHTS = {intent: weight for intent, _, weight in INTENT_PATTERNS}
BLOG_MATCH_THRESHOLD = 60  # token_set_ratio, prompt vs blog title + primary keyword
_IMPRESSIONS_RE = re.compile(r"gsc: .*?\((\d+) impr\)")
_BLOG_RE = re.compile(r"(?:^|; )blog: (.+?)(?:; |$)")


@dataclass
class RunInputs:
    prompts: List[Dict[str, str]] = field(default_factory=list)  # latest select's Prompts rows
    prompts_from: str = ""                                          # where they came from
    checks: List[Dict[str, str]] = field(default_factory=list)   # every Checks row, all runs
    costs: List[Dict[str, str]] = field(default_factory=list)    # runs/cost_log.csv rows
    active: List[str] = field(default_factory=list)               # runs/active_prompts.json prompts


def _stamp(path: Path) -> str:
    """'dry-run-20261110-154018' -> '20261110-154018', so dry and real runs sort together."""
    return path.name.split("-", 1)[1] if path.name.startswith("run-") else path.name.split("-", 2)[-1]


def _read(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_runs(runs: Path) -> RunInputs:
    """Latest prompts.csv, every checks.csv (deduped), and the cost log."""
    out = RunInputs()
    dirs = sorted((d for d in runs.glob("*run-*") if d.is_dir()), key=_stamp)
    with_prompts = [d for d in dirs if (d / "prompts.csv").exists()]
    if with_prompts:
        latest = with_prompts[-1]
        out.prompts, out.prompts_from = _read(latest / "prompts.csv"), latest.name
    seen = set()
    for d in dirs:
        if (d / "checks.csv").exists():
            for row in _read(d / "checks.csv"):
                key = (row.get("date"), row.get("time"), row.get("prompt"), row.get("device", ""))
                if key not in seen:
                    seen.add(key)
                    out.checks.append(row)
    if (runs / "cost_log.csv").exists():
        out.costs = _read(runs / "cost_log.csv")
    if (runs / "active_prompts.json").exists():
        out.active = [p["prompt"] for p in json.loads((runs / "active_prompts.json").read_text())]
    return out


def _pct(n: int, d: int) -> Optional[float]:
    return round(100 * n / d, 1) if d else None


def _rates(rows: List[Dict[str, str]]) -> Dict:
    c = Counter(r.get("check status", "") for r in rows)
    ok, inc, no_aio = c["ok"], c["incomplete"], c["no_aio"]
    cited = sum(1 for r in rows if r.get("check status") == "ok" and r.get("varsity cited") == "yes")
    brand = sum(1 for r in rows if r.get("check status") == "ok" and r.get("brand cited") == "yes")
    return {"checks": len(rows), "ok": ok, "incomplete": inc, "no_aio": no_aio, "failed": c["failed"],
            "cited": cited, "aio_presence": _pct(ok + inc, ok + inc + no_aio),
            "citation_rate": _pct(cited, ok), "incomplete_rate": _pct(inc, ok + inc),
            "brand_rate": _pct(brand, ok)}


def _domains(row: Dict[str, str]) -> List[str]:
    return [d.strip() for d in (row.get("competing domains") or "").split(",") if d.strip()]


def _int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _checked_at(row: Dict[str, str]) -> str:
    return f"{row.get('date', '')} {row.get('time', '')}".strip()


def _latest_per_prompt(checks: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """The newest non-failed check per (prompt, device); a failed check doesn't hide the last good one."""
    latest: Dict[tuple, Dict[str, str]] = {}
    for row in sorted(checks, key=_checked_at):
        key = (row.get("prompt"), row.get("device", ""))
        if row.get("check status") != "failed" or key not in latest:
            latest[key] = row
    return list(latest.values())


def _split_by_prompt_set(checks: List[Dict[str, str]], current: str):
    """(checks of the current set, summary of every other run). A run is one check timestamp."""
    runs: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in checks:
        runs[_checked_at(r)].append(r)
    mine, others = [], []
    for at, rows in sorted(runs.items()):
        tag = next((r["prompt set"] for r in rows if r.get("prompt set")), "")
        # checks from before tagging: the active set only if the run checked exactly it
        if not current or tag == current or (not tag and prompt_set_id(r["prompt"] for r in rows) == current):
            mine += rows
            continue
        if tag.startswith("file:"):
            why = f"check --prompts {tag[5:]} (test run)"
        elif tag:
            why = "earlier prompt set"
        else:
            why = "untagged run that didn't check the active set"
        others.append({"checked_at": at, "checks": len(rows), "prompts": len({r["prompt"] for r in rows}),
                       "prompt_set": tag, "why": why})
    return mine, others


def _map_blog(prompt: str, cluster: str, named: str, blogs: List[Blog]) -> Dict[str, str]:
    """The Varsity blog a prompt belongs to: the drafting blog if known, else the closest title or
    primary keyword in the same cluster."""
    if named:
        hit = next((b for b in blogs if b.title == named), None)
        return {"title": named, "url": hit.url if hit else "", "how": "drafted from"}
    best, best_score = None, 0.0
    for b in blogs:
        if b.cluster != cluster:
            continue
        score = max(fuzz.token_set_ratio(prompt, b.title.lower()),
                    fuzz.token_set_ratio(prompt, b.primary_keyword.lower()) if b.primary_keyword else 0)
        if score > best_score:
            best, best_score = b, score
    if best is not None and best_score >= BLOG_MATCH_THRESHOLD:
        return {"title": best.title, "url": best.url, "how": "closest title/keyword match"}
    return {"title": "", "url": "", "how": "no matching blog"}


def build_report(inputs: RunInputs, clusters: List[Cluster], blogs: List[Blog],
                 generated_at: Optional[datetime] = None, sample: bool = False) -> Dict:
    names = {c.key: c.name for c in clusters}
    meta: Dict[str, Dict[str, str]] = {r["prompt"]: r for r in inputs.prompts if r.get("prompt")}
    active_rows = [r for r in inputs.prompts if r.get("status") == "active"]
    # active_prompts.json is what `check` runs; the latest prompts.csv is the fallback
    active = inputs.active or [r["prompt"] for r in active_rows]
    current = prompt_set_id(active) if active else ""
    checks, other_runs = _split_by_prompt_set(inputs.checks, current)
    latest = _latest_per_prompt(checks)

    def info(prompt: str) -> Dict:
        m = meta.get(prompt, {})
        intent = m.get("intent") or classify_intent(prompt)[0]
        imp = _IMPRESSIONS_RE.search(m.get("source inputs", ""))
        blog = _BLOG_RE.search(m.get("source inputs", ""))
        return {"source": m.get("source") or "", "brand_tag": m.get("brand tag") or "", "intent": intent,
                "intent_weight": INTENT_WEIGHTS.get(intent, OTHER_INTENT_WEIGHT),
                "impressions": int(imp.group(1)) if imp else 0, "blog": blog.group(1) if blog else ""}

    # 1. headline
    # DataForSEO spend: the billed amount where the run logged it, else the run's estimate
    billed = estimated = 0.0
    n_billed = n_estimated = 0
    for r in inputs.costs:
        if r.get("provider") != "dataforseo":
            continue
        if r.get("billed usd"):
            billed += float(r["billed usd"]); n_billed += 1
        elif r.get("estimated usd"):
            estimated += float(r["estimated usd"]); n_estimated += 1
    overall = _rates(checks)
    kpis = dict(overall, prompts_tracked=len(active) or len({r["prompt"] for r in latest}),
                prompt_set=current, checks_excluded=sum(o["checks"] for o in other_runs),
                last_check_at=max((_checked_at(r) for r in checks), default=""),
                cost_usd=round(billed + estimated, 4) if n_billed + n_estimated else None,
                cost_billed_usd=round(billed, 4), cost_estimated_usd=round(estimated, 4),
                cost_runs_billed=n_billed, cost_runs_estimated=n_estimated)

    # 2. by cluster
    by_cluster = defaultdict(list)
    for r in checks:
        by_cluster[r.get("cluster", "")].append(r)
    cluster_rows = [dict(_rates(rows), cluster=k, name=names.get(k, k))
                    for k, rows in sorted(by_cluster.items(), key=lambda kv: list(names).index(kv[0])
                                          if kv[0] in names else 99)]

    # 3/4. gaps and wins, from the latest check per prompt and device
    gaps, wins = [], []
    for r in latest:
        p, i = r["prompt"], info(r["prompt"])
        base = {"prompt": p, "cluster": r.get("cluster", ""), "device": r.get("device", ""),
                "checked_at": _checked_at(r), "intent": i["intent"], "impressions": i["impressions"]}
        if r.get("check status") == "ok" and r.get("varsity cited") == "yes":
            wins.append(dict(base, cited_url=r.get("cited url", ""), position=_int(r.get("position"))))
        elif r.get("check status") == "ok":
            blog = _map_blog(p, r.get("cluster", ""), i["blog"], blogs)
            gaps.append(dict(base, competitors=_domains(r), organic_rank=_int(r.get("organic rank")),
                             rank_match=r.get("rank match", ""), brand_cited=r.get("brand cited") == "yes",
                             blog_title=blog["title"], blog_url=blog["url"], blog_how=blog["how"],
                             _w=i["intent_weight"]))
    gaps.sort(key=lambda g: (-g["_w"], -g["impressions"], g["prompt"]))
    for g in gaps:
        g.pop("_w")
    wins.sort(key=lambda w: (w["position"] or 99, w["prompt"]))

    # 5. competing domains, share of AI Overviews with sources (latest per prompt and device)
    with_sources = [r for r in latest if r.get("check status") == "ok"]
    counts = Counter(d for r in with_sources for d in set(_domains(r)))
    domains = [{"domain": d, "count": n, "share": _pct(n, len(with_sources))} for d, n in counts.most_common(15)]

    # 6. every prompt: latest check per device, plus active prompts not checked yet
    table = []
    for r in latest:
        i = info(r["prompt"])
        table.append({"prompt": r["prompt"], "cluster": r.get("cluster", ""), "source": i["source"],
                      "brand_tag": i["brand_tag"], "aio": r.get("ai overview shown", ""),
                      "varsity": r.get("varsity cited", ""), "position": _int(r.get("position")),
                      "competitors": _domains(r), "organic_rank": _int(r.get("organic rank")),
                      "rank_match": r.get("rank match", ""), "device": r.get("device", ""),
                      "status": r.get("check status", ""), "checked_at": _checked_at(r)})
    checked = {r["prompt"] for r in latest}
    for p in active:
        if p not in checked:
            r = meta.get(p, {})
            table.append({"prompt": p, "cluster": r.get("cluster", ""), "source": r.get("source", ""),
                          "brand_tag": r.get("brand tag", ""), "aio": "", "varsity": "", "position": None,
                          "competitors": [], "organic_rank": None, "rank_match": "", "device": "",
                          "status": "not checked", "checked_at": ""})

    # 7. trend: citation rate per check run (one timestamp per run, all devices)
    by_run = defaultdict(list)
    for r in checks:
        by_run[_checked_at(r)].append(r)
    trend = [dict(_rates(rows), run=at) for at, rows in sorted(by_run.items()) if at]

    return {"title": "Varsity AI Overview Citation Tracker", "sample": sample,
            "generated_at": (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
            "prompts_from": inputs.prompts_from, "kpis": kpis, "clusters": cluster_rows, "gaps": gaps,
            "wins": wins, "domains": domains, "aio_with_sources": len(with_sources), "prompts": table,
            "trend": trend, "other_runs": other_runs}


__all__ = ["RunInputs", "load_runs", "build_report"]
