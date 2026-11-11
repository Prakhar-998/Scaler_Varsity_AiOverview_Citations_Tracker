"""Build the dashboard's data from select and check outputs.

Inputs are plain rows (dicts keyed by the CSV headers), so the same builder works for the runs/
CSVs today and the Sheets tabs later. `load_runs` reads runs/; `build_report` is pure.

Definitions match the Summary tab (tracker/output/rows.py): failed checks are left out of every
rate, incomplete AI Overviews count as shown but not in the citation rate.

History is per prompt: every number except spend and the trend uses the checks of prompts that are
in the current active set (matched by `_key`, so case, spacing and a trailing full stop don't matter),
from any real run, so adding or removing prompts never discards earlier
checks of the prompts that stay. Real runs are tagged with the set they ran on (`set-...`); test runs
(`check --prompts`) are tagged `file:...` and never count. Older untagged runs count as real when
they checked exactly a set some `select` produced (any prompts.csv under runs/). The trend uses all
the checks each real run made. Left-out checks are summarised under `other_runs`.
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
from tracker.match import brand_site, normalise_url, page_kind
from tracker.prompts.generate import INTENT_PATTERNS, OTHER_INTENT_WEIGHT, classify_intent
from tracker.prompts.prompt_file import prompt_set_id

INTENT_WEIGHTS = {intent: weight for intent, _, weight in INTENT_PATTERNS}
BLOG_MATCH_THRESHOLD = 60  # token_set_ratio, prompt vs blog title + primary keyword
_IMPRESSIONS_RE = re.compile(r"gsc: .*?\((\d+) impr\)")
_BLOG_RE = re.compile(r"(?:^|; )blog: (.+?)(?:; |$)")
_TRAILING_PUNCT = ".,;:!?\u2026"


@dataclass
class RunInputs:
    prompts: List[Dict[str, str]] = field(default_factory=list)  # latest select's Prompts rows
    prompts_from: str = ""                                          # where they came from
    checks: List[Dict[str, str]] = field(default_factory=list)   # every Checks row, all runs
    costs: List[Dict[str, str]] = field(default_factory=list)    # runs/cost_log.csv rows
    active: List[str] = field(default_factory=list)               # runs/active_prompts.json prompts
    known_sets: List[str] = field(default_factory=list)           # set ids of every select's active prompts


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
    for d in with_prompts:
        kept = [r["prompt"] for r in _read(d / "prompts.csv") if r.get("status") == "active"]
        if kept:
            out.known_sets.append(prompt_set_id(kept))
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
    anyp = sum(1 for r in rows if r.get("check status") == "ok" and "yes" in (r.get("varsity cited"), r.get("brand cited")))
    # any-page citations by site: the blog, else the brand page's site (blank before brand url was recorded)
    sites = Counter(brand_site(r.get("brand url", "")) for r in rows
                    if r.get("check status") == "ok" and r.get("varsity cited") != "yes" and r.get("brand cited") == "yes")
    return {"checks": len(rows), "ok": ok, "incomplete": inc, "no_aio": no_aio, "failed": c["failed"],
            "cited": cited, "aio_presence": _pct(ok + inc, ok + inc + no_aio),
            "citation_rate": _pct(cited, ok), "incomplete_rate": _pct(inc, ok + inc),
            "brand_rate": _pct(brand, ok), "any_cited": anyp, "any_rate": _pct(anyp, ok),
            "any_split": {"blog": cited, "interviewbit": sites["InterviewBit"], "scaler": sites["Scaler"],
                          "not_recorded": sites[""]}}


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


def _key(prompt: str) -> str:
    """How past checks match active prompts: lowercase, trimmed, spaces collapsed, no trailing punctuation,
    so "Best FDE course." checked in the pilot run still counts for the active "best fde course"."""
    return " ".join(prompt.lower().split()).rstrip(_TRAILING_PUNCT).rstrip()


def _split_runs(checks: List[Dict[str, str]], active: List[str], known_sets: set):
    """Split checks by run (one check timestamp) into
    (checks of still-active prompts from real runs, every check of real runs, summary of what was left out)."""
    runs: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in checks:
        runs[_checked_at(r)].append(r)
    keep = {_key(p): p for p in active}
    tracked, real, others = [], [], []
    for at, rows in sorted(runs.items()):
        tag = next((r["prompt set"] for r in rows if r.get("prompt set")), "")
        n_prompts = len({r["prompt"] for r in rows})
        if tag.startswith("file:"):
            others.append({"checked_at": at, "checks": len(rows), "prompts": n_prompts, "prompt_set": tag,
                           "why": f"check --prompts {tag[5:]} (test run)"})
            continue
        if not tag and keep and prompt_set_id(r["prompt"] for r in rows) not in known_sets:
            others.append({"checked_at": at, "checks": len(rows), "prompts": n_prompts, "prompt_set": "",
                           "why": "untagged run of a prompt list no select produced (test run)"})
            continue
        real += rows
        # checks of an active prompt carry its active spelling from here on
        mine = [dict(r, prompt=keep[_key(r["prompt"])]) if keep else r
                for r in rows if not keep or _key(r["prompt"]) in keep]
        tracked += mine
        if len(mine) < len(rows):
            gone = {_key(r["prompt"]) for r in rows} - {_key(r["prompt"]) for r in mine}
            others.append({"checked_at": at, "checks": len(rows) - len(mine), "prompts": len(gone), "prompt_set": tag,
                           "why": "checks of prompts no longer active"})
    return tracked, real, others


def _citation(row: Dict[str, str]) -> Dict:
    """The page we're cited with: the Varsity blog if cited, else the first other InterviewBit/Scaler page.
    Rows from before `brand url` was recorded know a brand page was cited but not which."""
    if row.get("varsity cited") == "yes":
        url, pos = row.get("cited url", ""), row.get("position")
    elif row.get("brand cited") == "yes":
        url, pos = row.get("brand url", ""), row.get("brand position")
    else:
        return {"any": row.get("varsity cited", ""), "url": "", "position": None, "kind": "", "site": ""}
    return {"any": "yes", "url": url, "position": _int(pos), "kind": page_kind(url) or "page not recorded",
            "site": brand_site(url)}


def _history(checks: List[Dict[str, str]]) -> Dict[str, Dict]:
    """Per prompt, over the runs that got an answer for it (ok or no AI Overview; failed and incomplete
    checks say nothing about citations): runs counted, runs where one of our pages was cited (any device),
    the pages, best position, last cited, and whether the latest run lost the citation."""
    runs: Dict[str, Dict[str, bool]] = defaultdict(dict)
    pages: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    best: Dict[str, Optional[int]] = {}
    for r in sorted(checks, key=_checked_at):
        if r.get("check status") not in ("ok", "no_aio"):
            continue
        p, at, c = r["prompt"], _checked_at(r), _citation(r)
        hit = r.get("check status") == "ok" and c["any"] == "yes"
        runs[p][at] = runs[p].get(at, False) or hit
        if not hit:
            continue
        key = normalise_url(c["url"]) if c["url"] else ""
        e = pages[p].setdefault(key, {"url": c["url"], "kind": c["kind"], "site": c["site"], "runs": set()})
        e["runs"].add(at)
        if c["position"] is not None and (best.get(p) is None or c["position"] < best[p]):
            best[p] = c["position"]
    out = {}
    for p, by_run in runs.items():
        cited = sorted(at for at, hit in by_run.items() if hit)
        latest = max(by_run)
        blog_runs = {at for e in pages[p].values() if e["kind"] == "blog" for at in e["runs"]}
        # a brand citation from before brand url was recorded adds nothing once a URL is known
        known = [e for e in pages[p].values() if e["url"]] or list(pages[p].values())
        out[p] = {"runs": len(by_run), "cited_runs": len(cited), "blog_runs": len(blog_runs),
                  "last_cited": cited[-1] if cited else "", "lost": bool(cited) and not by_run[latest],
                  "best_position": best.get(p),
                  # most recently cited page first
                  "pages": [{k: v for k, v in e.items() if k != "runs"} for e in
                            sorted(known, key=lambda e: max(e["runs"]), reverse=True)]}
    return out


def _scoreboard(history: Dict[str, Dict], cluster_of: Dict[str, str]) -> List[Dict]:
    """Every active prompt one of our pages was cited for in any run, most consistent first, then best position."""
    rows = [dict(h, prompt=p, cluster=cluster_of.get(p, "")) for p, h in history.items() if h["cited_runs"]]
    rows.sort(key=lambda w: (-w["cited_runs"] / w["runs"], -w["cited_runs"], w["best_position"] or 99, w["prompt"]))
    return rows


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


def _by_blog(checks: List[Dict[str, str]], prompts: Dict[str, str], named: Dict[str, str],
             blogs: List[Blog]) -> Dict[str, List[Dict]]:
    """Varsity pages cited in any check, with the prompts citing each and its best position; and the blogs
    that active prompts map to (see _map_blog) but that were never cited. `prompts` is prompt -> cluster."""
    titles = {normalise_url(b.url): b.title for b in blogs}
    cited: Dict[str, Dict] = {}
    for r in checks:
        if r.get("check status") != "ok" or r.get("varsity cited") != "yes" or not r.get("cited url"):
            continue
        key = normalise_url(r["cited url"])
        e = cited.setdefault(key, {"url": r["cited url"], "title": titles.get(key, ""), "checks": 0, "best": {}})
        e["checks"] += 1
        pos, best = _int(r.get("position")), e["best"]
        if r["prompt"] not in best or (pos is not None and (best[r["prompt"]] is None or pos < best[r["prompt"]])):
            best[r["prompt"]] = pos
    rows = []
    for e in cited.values():
        hits = sorted(e.pop("best").items(), key=lambda kv: (kv[1] or 99, kv[0]))
        e["prompts"] = [{"prompt": p, "position": pos} for p, pos in hits]
        e["best_position"] = min((pos for _, pos in hits if pos is not None), default=None)
        rows.append(e)
    rows.sort(key=lambda e: (-len(e["prompts"]), e["best_position"] or 99, e["url"]))

    never: Dict[str, Dict] = {}
    for p, cluster in sorted(prompts.items()):
        blog = _map_blog(p, cluster, named.get(p, ""), blogs)
        if not blog["title"] or (blog["url"] and normalise_url(blog["url"]) in cited):
            continue
        e = never.setdefault(blog["url"] or blog["title"], dict(blog, prompts=[]))
        e["prompts"].append(p)
    uncited = sorted(never.values(), key=lambda e: (-len(e["prompts"]), e["title"]))
    return {"cited": rows, "never_cited": uncited}


def _runs_cited(h: Optional[Dict]) -> Dict:
    h = h or {}
    return {"cited_runs": h.get("cited_runs", 0), "runs": h.get("runs", 0), "lost": h.get("lost", False)}


def build_report(inputs: RunInputs, clusters: List[Cluster], blogs: List[Blog],
                 generated_at: Optional[datetime] = None, sample: bool = False) -> Dict:
    names = {c.key: c.name for c in clusters}
    meta: Dict[str, Dict[str, str]] = {r["prompt"]: r for r in inputs.prompts if r.get("prompt")}
    active_rows = [r for r in inputs.prompts if r.get("status") == "active"]
    # active_prompts.json is what `check` runs; the latest prompts.csv is the fallback
    active = inputs.active or [r["prompt"] for r in active_rows]
    current = prompt_set_id(active) if active else ""
    known = set(inputs.known_sets) | {current} | ({prompt_set_id(r["prompt"] for r in active_rows)} if active_rows else set())
    checks, real_checks, other_runs = _split_runs(inputs.checks, active, known)
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

    # 3. gaps, from the latest check per prompt and device
    gaps = []
    for r in latest:
        p, i = r["prompt"], info(r["prompt"])
        base = {"prompt": p, "cluster": r.get("cluster", ""), "device": r.get("device", ""),
                "checked_at": _checked_at(r), "intent": i["intent"], "impressions": i["impressions"]}
        if r.get("check status") == "ok" and r.get("varsity cited") != "yes":
            blog = _map_blog(p, r.get("cluster", ""), i["blog"], blogs)
            c = _citation(r)
            gaps.append(dict(base, competitors=_domains(r), organic_rank=_int(r.get("organic rank")),
                             rank_match=r.get("rank match", ""), brand_cited=r.get("brand cited") == "yes",
                             brand_url=c["url"], brand_kind=c["kind"], brand_site=c["site"], brand_position=c["position"],
                             blog_title=blog["title"], blog_url=blog["url"], blog_how=blog["how"],
                             _w=i["intent_weight"]))
    gaps.sort(key=lambda g: (-g["_w"], -g["impressions"], g["prompt"]))
    for g in gaps:
        g.pop("_w")

    # 4. wins scoreboard: every run, per prompt
    history = _history(checks)
    wins = _scoreboard(history, {r["prompt"]: r.get("cluster", "") for r in checks})

    # 5. competing domains, share of AI Overviews with sources (latest per prompt and device)
    with_sources = [r for r in latest if r.get("check status") == "ok"]
    counts = Counter(d for r in with_sources for d in set(_domains(r)))
    domains = [{"domain": d, "count": n, "share": _pct(n, len(with_sources))} for d, n in counts.most_common(15)]

    # 6. every prompt: latest check per device, plus active prompts not checked yet
    table = []
    for r in latest:
        i, c = info(r["prompt"]), _citation(r)
        table.append({"prompt": r["prompt"], "cluster": r.get("cluster", ""), "source": i["source"],
                      "brand_tag": i["brand_tag"], "aio": r.get("ai overview shown", ""),
                      "varsity": r.get("varsity cited", ""), "position": _int(r.get("position")),
                      "any": c["any"], "cited_url": c["url"], "cited_kind": c["kind"], "cited_site": c["site"],
                      "any_position": c["position"],
                      "competitors": _domains(r), "organic_rank": _int(r.get("organic rank")),
                      "rank_match": r.get("rank match", ""), "device": r.get("device", ""),
                      "status": r.get("check status", ""), "checked_at": _checked_at(r),
                      **_runs_cited(history.get(r["prompt"]))})
    checked = {r["prompt"] for r in latest}
    for p in active:
        if p not in checked:
            r = meta.get(p, {})
            table.append({"prompt": p, "cluster": r.get("cluster", ""), "source": r.get("source", ""),
                          "brand_tag": r.get("brand tag", ""), "aio": "", "varsity": "", "position": None,
                          "any": "", "cited_url": "", "cited_kind": "", "cited_site": "", "any_position": None,
                          "competitors": [], "organic_rank": None, "rank_match": "", "device": "",
                          "status": "not checked", "checked_at": "", **_runs_cited(None)})

    # 7. trend: citation rate per check run (one timestamp per run, all devices), over the prompts that run checked
    by_run = defaultdict(list)
    for r in real_checks:
        by_run[_checked_at(r)].append(r)
    trend = [dict(_rates(rows), run=at, prompts=len({r["prompt"] for r in rows}))
             for at, rows in sorted(by_run.items()) if at]

    # 8. by blog
    tracked = {r["prompt"]: r.get("cluster", "") for r in latest}
    for p in active:
        tracked.setdefault(p, meta.get(p, {}).get("cluster", ""))
    by_blog = _by_blog(checks, tracked, {p: info(p)["blog"] for p in tracked}, blogs)

    return {"title": "Varsity AI Overview Citation Tracker", "sample": sample, "by_blog": by_blog,
            "generated_at": (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
            "prompts_from": inputs.prompts_from, "kpis": kpis, "clusters": cluster_rows, "gaps": gaps,
            "wins": wins, "wins_blog": sum(1 for w in wins if w["blog_runs"]), "domains": domains, "aio_with_sources": len(with_sources), "prompts": table,
            "trend": trend, "other_runs": other_runs}


__all__ = ["RunInputs", "load_runs", "build_report"]
