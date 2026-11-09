"""DataForSEO adapter.

Endpoint: POST https://api.dataforseo.com/v3/serp/google/organic/live/advanced, HTTP Basic auth
(DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD), body is a list with one task.

`load_async_ai_overview: true` makes DataForSEO wait for AI Overviews that load late, which
SERPHouse returns without sources (see docs/decisions.md).

Response shape (confirmed from real captures in tests/fixtures/dataforseo/, 2026-11-09):
  status_code (20000 = ok), tasks[0].status_code, tasks[0].result[0].items[]
  items[type=ai_overview]   references[] (type, source, domain, title, url, text): the sources list;
                            items[].references[]: inline citations per section, which on desktop
                            include sources missing from the list
  items[type=organic]       rank_group (organic position), title, breadcrumb, website_name
                            `url` is an opaque google.com/goto redirect on mobile AND desktop, with
                            domain "google.com"; the site is only in `breadcrumb`
On mobile, many AI Overview references are also google.com/goto links; `source` and `title` still
name the page (see docs/decisions.md, mobile follow-up).
"""
from __future__ import annotations

import html
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .base import (
    STATUS_INCOMPLETE,
    STATUS_NO_AIO,
    STATUS_OK,
    BudgetExceeded,
    CallBudget,
    OrganicResult,
    Reference,
    SerpProvider,
    SerpResult,
    site_url,
)

log = logging.getLogger(__name__)

ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
OK_CODE = 20000
DEFAULT_PARAMS = {
    "location_code": 2356,  # India
    "language_code": "en",
    "device": "mobile",
    "load_async_ai_overview": True,  # wait for AI Overviews that load late
}


class DataForSeoProvider(SerpProvider):
    name = "dataforseo"

    def __init__(
        self,
        login: str,
        password: str,
        budget: Optional[CallBudget] = None,
        session: Optional[requests.Session] = None,
        retries: int = 2,
        timeout: float = 120.0,
        backoff: float = 2.0,
        incomplete_retries: int = 1,
        incomplete_delay: float = 5.0,
    ):
        if not login or not password:
            raise ValueError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD must be set")
        self.auth = (login, password)
        self.budget = budget
        self.session = session or requests.Session()
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self.incomplete_retries = incomplete_retries
        self.incomplete_delay = incomplete_delay

    def fetch_ai_overview(self, query: str, retry_incomplete: bool = True) -> SerpResult:
        """Fetch once; if the AI Overview still came back without sources, retry once
        unless `retry_incomplete` is False."""
        result = self._fetch(query)
        for _ in range(self.incomplete_retries if retry_incomplete else 0):
            if result.status != STATUS_INCOMPLETE:
                break
            log.info("AI Overview without sources for %r; retrying", query)
            time.sleep(self.incomplete_delay)
            try:
                result = self._fetch(query)
            except BudgetExceeded:
                log.warning("SERP budget used up before incomplete retry for %r", query)
                break
        return result

    def _fetch(self, query: str) -> SerpResult:
        """One logical request, with retries for network errors, 429 and 5xx."""
        payload = [dict(DEFAULT_PARAMS, keyword=query)]
        headers = {"Accept": "application/json"}
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            if self.budget is not None:
                self.budget.spend()  # every attempt may cost a credit
            try:
                resp = self.session.post(ENDPOINT, json=payload, headers=headers, auth=self.auth,
                                         timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"request error: {exc}"
            else:
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        return SerpResult.failed(query, "response was not JSON")
                    return parse_response(query, body)
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code < 500 and resp.status_code != 429:
                    break  # client error (e.g. 401 bad login): retrying won't help
            if attempt < self.retries:
                time.sleep(self.backoff * (attempt + 1))
        return SerpResult.failed(query, last_error)


def parse_response(query: str, body: Dict[str, Any]) -> SerpResult:
    """Turn a DataForSEO JSON body into a SerpResult. Pure function, tested offline."""
    if not isinstance(body, dict):
        return SerpResult.failed(query, "response body is not an object")
    if body.get("status_code") != OK_CODE:
        return SerpResult.failed(query, f"provider status: {body.get('status_code')} {body.get('status_message', '')}"
                                 .strip(), body)
    tasks = body.get("tasks")
    task = tasks[0] if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict) else None
    if task is None:
        return SerpResult.failed(query, "no tasks in response", body)
    if task.get("status_code") != OK_CODE:
        return SerpResult.failed(query, f"task status: {task.get('status_code')} {task.get('status_message', '')}"
                                 .strip(), body)
    results = task.get("result")
    result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None
    if result is None:
        return SerpResult.failed(query, "no result in task", body)

    items = [i for i in (result.get("items") or []) if isinstance(i, dict)]
    organic = _parse_organic([i for i in items if i.get("type") == "organic"])
    aio = next((i for i in items if i.get("type") == "ai_overview"), None)
    if aio is None:
        return SerpResult(query=query, status=STATUS_NO_AIO, organic=organic, raw=body)

    refs = _parse_references(aio)
    return SerpResult(
        query=query,
        status=STATUS_OK if refs else STATUS_INCOMPLETE,
        shown=True,
        references=refs,
        organic=organic,
        raw=body,
    )


def _text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def _parse_references(aio: Dict[str, Any]) -> List[Reference]:
    """The AI Overview's own reference list, in order; references nested in its elements are
    added after, so nothing is missed when the top-level list is empty."""
    raw = list(aio.get("references") or [])
    for element in aio.get("items") or []:
        if isinstance(element, dict):
            raw += element.get("references") or []
    refs: List[Reference] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url or url in seen:
            continue
        seen.add(url)
        refs.append(Reference(url=url, title=_text(item.get("title")),
                              source=_text(item.get("source") or item.get("domain")), position=len(refs) + 1))
    return refs


def _parse_organic(items: List[Dict[str, Any]]) -> List[OrganicResult]:
    out: List[OrganicResult] = []
    for i, item in enumerate(items, start=1):
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        pos = item.get("rank_group")
        out.append(OrganicResult(
            url=url,
            title=_text(item.get("title")),
            position=pos if isinstance(pos, int) else i,
            # `url` and `domain` are a google.com redirect; the breadcrumb shows the real site
            display_url=site_url(item.get("breadcrumb")),
        ))
    return out


__all__ = ["DataForSeoProvider", "parse_response"]
