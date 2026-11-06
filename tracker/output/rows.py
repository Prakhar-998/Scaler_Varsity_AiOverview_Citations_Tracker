"""Build sheet rows from decisions and checks, and compute the summary."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from tracker.checks.base import STATUS_FAILED, STATUS_INCOMPLETE, STATUS_NO_AIO, STATUS_OK, SerpResult
from tracker.match import CitationMatch

PROMPT_HEADERS = ["prompt", "cluster", "intent", "score", "status", "reason", "source inputs"]
CHECK_HEADERS = ["date", "time", "prompt", "cluster", "ai overview shown", "varsity cited", "cited url",
                 "position", "brand cited", "organic rank", "competing domains", "check status", "error",
                 "rank match"]  # appended last so existing Checks rows stay aligned
SUMMARY_HEADERS = ["cluster", "checks", "ok", "incomplete", "no_aio", "failed", "ai overview presence %",
                   "varsity citation rate %", "incomplete rate %", "brand citation rate %"]


def prompt_row(d) -> List:
    c = d.candidate
    return [c.prompt, c.cluster, c.intent, d.score, d.status, d.reason, "; ".join(d.sources)]


def _yn(v: bool) -> str:
    return "yes" if v else "no"


def check_row(when: datetime, prompt: str, cluster: str, result: SerpResult, m: Optional[CitationMatch]) -> List:
    m = m or CitationMatch()
    cited_known = result.status == STATUS_OK  # incomplete: AIO shown but sources unknown
    error = result.error or ("AI Overview sources did not load" if result.status == STATUS_INCOMPLETE else "")
    return [
        when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), prompt, cluster,
        _yn(result.shown) if result.status != STATUS_FAILED else "",
        _yn(m.varsity_cited) if cited_known else "",
        m.cited_url, m.position or "", _yn(m.brand_cited) if cited_known else "",
        m.organic_rank or "", ", ".join(m.competing_domains[:10]),
        result.status, error, m.rank_match,
    ]


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}" if d else ""


def summarise(check_rows: List[List]) -> List[List]:
    """Rates per cluster, plus an ALL row.

    AI Overview presence % = AIO detected (ok + incomplete) / (ok + incomplete + no_aio)
    Varsity citation rate % = Varsity cited / ok        (incomplete: sources unknown)
    Incomplete rate %       = incomplete / AIO detected
    Failed checks are counted but kept out of every denominator.
    """
    idx = {h: i for i, h in enumerate(CHECK_HEADERS)}
    stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in check_rows:
        for key in (row[idx["cluster"]], "ALL"):
            s = stats[key]
            s["checks"] += 1
            status = row[idx["check status"]]
            s[status] += 1
            if status == STATUS_OK:
                s["varsity"] += row[idx["varsity cited"]] == "yes"
                s["brand"] += row[idx["brand cited"]] == "yes"
    out = []
    for key in sorted(stats, key=lambda k: (k == "ALL", k)):
        s = stats[key]
        ok, inc, no_aio = s[STATUS_OK], s[STATUS_INCOMPLETE], s[STATUS_NO_AIO]
        detected = ok + inc
        out.append([key, s["checks"], ok, inc, no_aio, s[STATUS_FAILED],
                    _pct(detected, detected + no_aio), _pct(s["varsity"], ok),
                    _pct(inc, detected), _pct(s["brand"], ok)])
    return out
