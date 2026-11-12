"""Provider-neutral SERP result types and the adapter interface.

Every SERP provider implements `SerpProvider.fetch_ai_overview(query)` and returns a
`SerpResult`. Nothing outside `tracker/checks/` knows which provider is in use.
"""
from __future__ import annotations

import hashlib
import html
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

STATUS_OK = "ok"          # AI Overview shown and parsed
STATUS_NO_AIO = "no_aio"  # SERP fetched fine, no AI Overview on it
STATUS_INCOMPLETE = "incomplete"  # AIO shown but its sources did not load; citations unknown
STATUS_FAILED = "failed"  # request or parse failed; says nothing about AIO presence


def query_label(query: str) -> str:
    """How a query is named in logs. With TRACKER_REDACT_LOGS=1 (set by the GitHub Actions workflow: a public
    repo's Actions logs are public) a short hash stands in for the prompt text."""
    if os.environ.get("TRACKER_REDACT_LOGS", "").strip().lower() in ("1", "true", "yes"):
        return "query " + hashlib.sha256(query.encode()).hexdigest()[:8]
    return repr(query)


class BudgetExceeded(RuntimeError):
    """Raised when a run would go over its max SERP calls setting."""


@dataclass
class CallBudget:
    """Cost guard: hard cap on SERP calls in a single run."""

    max_calls: int
    used: int = 0

    def spend(self) -> None:
        if self.used >= self.max_calls:
            raise BudgetExceeded(f"SERP call budget of {self.max_calls} used up")
        self.used += 1

    @property
    def remaining(self) -> int:
        return self.max_calls - self.used


@dataclass
class Reference:
    url: str
    title: str = ""
    position: int = 0  # 1-based order in the AI Overview reference list
    source: str = ""  # publisher label as shown in the AI Overview, e.g. "LinkedIn · Pablo Gonzalez"


@dataclass
class OrganicResult:
    url: str
    title: str = ""
    position: int = 0
    # Site URL as shown on the result (domain, sometimes a breadcrumb). Used for a domain-level
    # rank when `url` is an opaque Google redirect. Empty when the result shows no site URL.
    display_url: str = ""


def site_url(value: Any) -> str:
    """'https://fde.academy › blog › ...' -> 'https://fde.academy'. Social results show text such as
    '740+ reactions · 3 months ago' instead of a URL; those return ''."""
    text = html.unescape(str(value or "")).strip().split("›")[0].strip()
    return text if text.lower().startswith(("http://", "https://")) and " " not in text else ""


@dataclass
class SerpResult:
    query: str
    status: str
    shown: bool = False
    references: List[Reference] = field(default_factory=list)
    organic: List[OrganicResult] = field(default_factory=list)
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    @classmethod
    def failed(cls, query: str, error: str, raw: Optional[Dict[str, Any]] = None) -> "SerpResult":
        return cls(query=query, status=STATUS_FAILED, error=error, raw=raw)


DEVICES = ("desktop", "mobile")


class SerpProvider:
    """Adapter interface. Fixed for the prototype: google.com, India, English. Each provider instance
    fetches for one device (`device`, from the `devices` setting or --device)."""

    name = "base"
    device = "mobile"
    # USD actually billed so far, for providers that report it per response (DataForSEO); else None
    spent_usd: Optional[float] = None

    def fetch_ai_overview(self, query: str, retry_incomplete: bool = True) -> SerpResult:
        """`retry_incomplete=False` skips the late-load retry, e.g. for select probes, where an
        incomplete AI Overview already proves one is shown."""
        raise NotImplementedError
