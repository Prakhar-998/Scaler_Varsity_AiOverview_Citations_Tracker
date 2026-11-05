"""SERP providers. Add a provider by writing one adapter module and registering it here."""
from __future__ import annotations

import os
from typing import Optional

from .base import BudgetExceeded, CallBudget, SerpProvider, SerpResult


def get_provider(name: Optional[str] = None, budget: Optional[CallBudget] = None) -> SerpProvider:
    name = (name or os.environ.get("SERP_PROVIDER") or "serphouse").lower()
    if name == "serphouse":
        from .serphouse import SerphouseProvider
        return SerphouseProvider(os.environ.get("SERPHOUSE_API_KEY", ""), budget=budget)
    if name == "fake":
        from .fake import FakeProvider
        return FakeProvider(budget=budget)
    raise ValueError(f"unknown SERP_PROVIDER: {name}")


__all__ = ["get_provider", "BudgetExceeded", "CallBudget", "SerpProvider", "SerpResult"]
