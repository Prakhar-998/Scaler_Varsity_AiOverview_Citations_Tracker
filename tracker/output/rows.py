"""Build sheet rows from decisions and checks, and compute the summary."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from tracker.checks.base import STATUS_FAILED, STATUS_INCOMPLETE, STATUS_NO_AIO, STATUS_OK, SerpResult
from tracker.match import MATCH_LABEL, MATCH_URL, CitationMatch, OurPage, brand_site

PROMPT_HEADERS = ["prompt", "cluster", "source", "brand tag", "intent", "score", "status", "reason",
                  "source inputs"]
CHECK_HEADERS = ["date", "time", "prompt", "cluster", "device", "ai overview shown", "varsity cited", "cited url",
                 "position", "brand cited", "organic rank", "rank match", "competing domains", "check status",
                 "error", "prompt set", "brand url", "brand position", "our pages", "varsity match"]
# "our pages": "#2 https://... | #4 [title] https://<matched blog> | #6 [label] Scaler page (URL hidden)"
OUR_PAGES_SEP = " | "
SUMMARY_HEADERS = ["cluster", "device", "checks", "ok", "incomplete", "no_aio", "failed", "ai overview presence %",
                   "varsity citation rate %", "incomplete rate %", "brand citation rate %",
                   "any page citation rate %"]


def prompt_row(d) -> List:
    c = d.candidate
    return [c.prompt, c.cluster, c.source, c.brand_tag, c.intent, d.score, d.status, d.reason,
            "; ".join(d.sources)]


def _yn(v: bool) -> str:
    return "yes" if v else "no"


def check_row(when: datetime, prompt: str, cluster: str, result: SerpResult, m: Optional[CitationMatch],
              device: str = "", prompt_set: str = "") -> List:
    m = m or CitationMatch()
    cited_known = result.status == STATUS_OK  # incomplete: AIO shown but sources unknown
    error = result.error or ("AI Overview sources did not load" if result.status == STATUS_INCOMPLETE else "")
    return [
        when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), prompt, cluster, device,
        _yn(result.shown) if result.status != STATUS_FAILED else "",
        _yn(m.varsity_cited) if cited_known else "",
        m.cited_url, m.position or "", _yn(m.brand_cited) if cited_known else "",
        m.organic_rank or "", m.rank_match, ", ".join(m.competing_domains[:10]),
        result.status, error, prompt_set,
        (m.brand_urls[0] if m.brand_urls else "") if cited_known else "", (m.brand_position or "") if cited_known else "",
        format_our_pages(m.our_pages) if cited_known else "", m.varsity_match if cited_known else "",
    ]


def format_our_pages(pages: List[OurPage]) -> str:
    return OUR_PAGES_SEP.join(f"#{p.position or '?'} " + ("" if p.match == MATCH_URL else f"[{p.match}] ") + p.name
                              for p in pages)


def parse_our_pages(text: str) -> List[OurPage]:
    out = []
    for part in (text or "").split(OUR_PAGES_SEP):
        pos, _, rest = part.strip().partition(" ")
        match = MATCH_URL
        if rest.startswith("[") and "] " in rest:
            match, rest = rest[1:].split("] ", 1)
        if not rest:
            continue
        position = int(pos[1:]) if pos[1:].isdigit() else None
        if match == MATCH_LABEL:
            out.append(OurPage("", position, rest.split(" ", 1)[0], match))
        else:
            out.append(OurPage(rest, position, brand_site(rest), match))
    return out


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}" if d else ""


def summarise(check_rows: List[List]) -> List[List]:
    """Rates per cluster and device, plus an ALL row per device (and ALL / ALL when there is more
    than one device). Mobile and desktop AI Overviews differ, so they are never pooled per cluster.

    AI Overview presence % = AIO detected (ok + incomplete) / (ok + incomplete + no_aio)
    Varsity citation rate % = Varsity cited / ok        (incomplete: sources unknown)
    Incomplete rate %       = incomplete / AIO detected
    Any page citation rate % = Varsity blog or another InterviewBit/Scaler page cited / ok
    Failed checks are counted but kept out of every denominator.
    """
    idx = {h: i for i, h in enumerate(CHECK_HEADERS)}
    stats: Dict[tuple, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    devices = {row[idx["device"]] for row in check_rows}
    for row in check_rows:
        cluster, device = row[idx["cluster"]], row[idx["device"]]
        keys = [(cluster, device), ("ALL", device)] + ([("ALL", "ALL")] if len(devices) > 1 else [])
        for key in keys:
            s = stats[key]
            s["checks"] += 1
            status = row[idx["check status"]]
            s[status] += 1
            if status == STATUS_OK:
                s["varsity"] += row[idx["varsity cited"]] == "yes"
                s["brand"] += row[idx["brand cited"]] == "yes"
                s["any"] += "yes" in (row[idx["varsity cited"]], row[idx["brand cited"]])
    out = []
    for key in sorted(stats, key=lambda k: (k[0] == "ALL", k[0], k[1] == "ALL", k[1])):
        s = stats[key]
        ok, inc, no_aio = s[STATUS_OK], s[STATUS_INCOMPLETE], s[STATUS_NO_AIO]
        detected = ok + inc
        out.append([*key, s["checks"], ok, inc, no_aio, s[STATUS_FAILED],
                    _pct(detected, detected + no_aio), _pct(s["varsity"], ok),
                    _pct(inc, detected), _pct(s["brand"], ok), _pct(s["any"], ok)])
    return out
