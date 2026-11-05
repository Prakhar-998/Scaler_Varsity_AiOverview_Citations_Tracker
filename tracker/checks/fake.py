"""In-memory provider for tests and offline dry runs. Costs nothing."""
from __future__ import annotations

from typing import Dict, Optional

from .base import STATUS_NO_AIO, CallBudget, SerpProvider, SerpResult


class FakeProvider(SerpProvider):
    name = "fake"

    def __init__(self, responses: Optional[Dict[str, SerpResult]] = None, budget: Optional[CallBudget] = None):
        self.responses = responses or {}
        self.budget = budget
        self.calls = []

    def fetch_ai_overview(self, query: str) -> SerpResult:
        if self.budget is not None:
            self.budget.spend()
        self.calls.append(query)
        return self.responses.get(query) or SerpResult(query=query, status=STATUS_NO_AIO)
