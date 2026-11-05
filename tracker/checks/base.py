"""Provider-neutral SERP result types and the adapter interface.

Every SERP provider implements `SerpProvider.fetch_ai_overview(query)` and returns a
`SerpResult`. Nothing outside `tracker/checks/` knows which provider is in use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

STATUS_OK = "ok"          # AI Overview shown and parsed
STATUS_NO_AIO = "no_aio"  # SERP fetched fine, no AI Overview on it
STATUS_FAILED = "failed"  # request or parse failed; says nothing about AIO presence


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


@dataclass
class OrganicResult:
    url: str
    title: str = ""
    position: int = 0


@dataclass
class SerpResult:
    query: str
    status: str
    shown: bool = False
    references: List[Reference] = field(default_factory=list)
    organic: List[OrganicResult] = field(default_factory=list)
    error: Optional[str] = None
    # AIO block was present but carried no references (e.g. still loading).
    incomplete: bool = False
    raw: Optional[Dict[str, Any]] = None

    @classmethod
    def failed(cls, query: str, error: str, raw: Optional[Dict[str, Any]] = None) -> "SerpResult":
        return cls(query=query, status=STATUS_FAILED, error=error, raw=raw)


class SerpProvider:
    """Adapter interface. Settings are fixed for the prototype: google.com, India, English, mobile."""

    name = "base"

    def fetch_ai_overview(self, query: str) -> SerpResult:
        raise NotImplementedError
