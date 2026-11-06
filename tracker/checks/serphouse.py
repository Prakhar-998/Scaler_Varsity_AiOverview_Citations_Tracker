"""SERPHouse adapter (prototype provider).

Endpoint: POST https://api.serphouse.com/serp/live, JSON body {"data": {...}}, Bearer token.

SERPHouse adds Google Verbatim (tbs=li:1) by default, which suppresses AI Overviews, so every
request sends "tbs": "" to clear it. The GET format from their AI Overview docs (gl/hl params)
returned "check_html_manually" errors in testing, so it is not used.

Response shape (confirmed from real captures in tests/fixtures/serphouse/):
  results.results.organic[]          position, title/site_title, link
  results.results.ai_overview        title, contents[], references[] (source, link, title)
"""
from __future__ import annotations

import html
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .base import (
    STATUS_FAILED,
    STATUS_NO_AIO,
    STATUS_OK,
    CallBudget,
    OrganicResult,
    Reference,
    SerpProvider,
    SerpResult,
)

log = logging.getLogger(__name__)

ENDPOINT = "https://api.serphouse.com/serp/live"
DEFAULT_PARAMS = {
    "domain": "google.com",
    "loc": "India",
    "lang": "en",
    "device": "mobile",
    "serp_type": "web",
    "tbs": "",  # must be sent: clears SERPHouse's default Verbatim mode (tbs=li:1)
}

class SerphouseProvider(SerpProvider):
    name = "serphouse"

    def __init__(
        self,
        api_key: str,
        budget: Optional[CallBudget] = None,
        session: Optional[requests.Session] = None,
        retries: int = 2,
        timeout: float = 60.0,
        backoff: float = 2.0,
    ):
        if not api_key:
            raise ValueError("SERPHOUSE_API_KEY is not set")
        self.api_key = api_key
        self.budget = budget
        self.session = session or requests.Session()
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff

    def fetch_ai_overview(self, query: str) -> SerpResult:
        body = {"data": dict(DEFAULT_PARAMS, q=query)}
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            if self.budget is not None:
                self.budget.spend()  # every attempt may cost a credit
            try:
                resp = self.session.post(ENDPOINT, json=body, headers=headers, timeout=self.timeout)
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
                    break  # client error: retrying won't help
            if attempt < self.retries:
                time.sleep(self.backoff * (attempt + 1))
        return SerpResult.failed(query, last_error)


def parse_response(query: str, body: Dict[str, Any]) -> SerpResult:
    """Turn a SERPHouse JSON body into a SerpResult. Pure function, tested offline."""
    if not isinstance(body, dict):
        return SerpResult.failed(query, "response body is not an object")
    if str(body.get("status", "")).lower() != "success":
        detail = body.get("error") or body.get("msg") or ""
        return SerpResult.failed(query, f"provider status: {body.get('status')} {detail}".strip(), body)

    outer = body.get("results")
    results = outer.get("results") if isinstance(outer, dict) else None
    if not isinstance(results, dict):
        return SerpResult.failed(query, "no results.results block in response", body)

    organic = _parse_organic(results.get("organic") or [])
    aio = results.get("ai_overview")
    if not isinstance(aio, dict):
        return SerpResult(query=query, status=STATUS_NO_AIO, organic=organic, raw=body)

    refs = _parse_references(aio.get("references") or [])
    return SerpResult(
        query=query,
        status=STATUS_OK,
        shown=True,
        references=refs,
        organic=organic,
        incomplete=not refs,
        raw=body,
    )


def _text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def _parse_references(items: List[Any]) -> List[Reference]:
    """Reference order in the AI Overview is the citation position (1-based)."""
    refs: List[Reference] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("link")
        if not isinstance(url, str) or not url or url in seen:
            continue
        seen.add(url)
        refs.append(Reference(url=url, title=_text(item.get("title")), source=_text(item.get("source")),
                              position=len(refs) + 1))
    return refs


def _parse_organic(items: List[Any]) -> List[OrganicResult]:
    out: List[OrganicResult] = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        url = item.get("link")
        if not isinstance(url, str) or not url:
            continue
        pos = item.get("position")
        out.append(OrganicResult(
            url=url,
            title=_text(item.get("title") or item.get("site_title")),
            position=pos if isinstance(pos, int) else i,
        ))
    return out


__all__ = ["SerphouseProvider", "parse_response"]
