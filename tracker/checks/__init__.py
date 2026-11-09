"""SERP providers. Add a provider by writing one adapter module and registering it here."""
from __future__ import annotations

import os
from typing import Optional

from .base import DEVICES, BudgetExceeded, CallBudget, SerpProvider, SerpResult


def get_provider(name: Optional[str] = None, budget: Optional[CallBudget] = None,
                 device: str = "mobile") -> SerpProvider:
    name = (name or os.environ.get("SERP_PROVIDER") or "serphouse").lower()
    if device not in DEVICES:
        raise ValueError(f"unknown device {device!r}; use one of {', '.join(DEVICES)}")
    if name == "serphouse":
        from .serphouse import SerphouseProvider
        return SerphouseProvider(os.environ.get("SERPHOUSE_API_KEY", ""), budget=budget, device=device)
    if name == "dataforseo":
        from .dataforseo import DataForSeoProvider
        return DataForSeoProvider(os.environ.get("DATAFORSEO_LOGIN", ""), os.environ.get("DATAFORSEO_PASSWORD", ""),
                                  budget=budget, device=device)
    if name == "fake":
        from .fake import FakeProvider
        return FakeProvider(budget=budget, device=device)
    raise ValueError(f"unknown SERP_PROVIDER: {name}")


__all__ = ["get_provider", "DEVICES", "BudgetExceeded", "CallBudget", "SerpProvider", "SerpResult"]
