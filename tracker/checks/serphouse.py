"""SERPHouse adapter (prototype provider).

Endpoint: GET https://api.serphouse.com/serp/live with a Bearer token.

The public docs do not give a schema for the AI Overview block, so `parse_response`
looks for it under a few likely key names and logs which one it found. Once a real
response is captured into tests/fixtures/, narrow `AIO_KEYS` / `REF_KEYS` to match it.
"""
from __future__ import annotations

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
}

# Unconfirmed against a real response yet. Checked in order.
AIO_KEYS = ("ai_overview", "ai_overviews", "google_ai_overview", "aiOverview")
REF_KEYS = ("references", "sources", "citations", "links")
URL_KEYS = ("url", "link", "source_url", "href")


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
        params = dict(DEFAULT_PARAMS, q=query)
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            if self.budget is not None:
                self.budget.spend()  # every attempt may cost a credit
            try:
                resp = self.session.get(ENDPOINT, params=params, headers=headers, timeout=self.timeout)
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
    if str(body.get("status", "success")).lower() not in ("success", "ok"):
        return SerpResult.failed(query, f"provider status: {body.get('status')} {body.get('msg', '')}".strip(), body)

    results = _results_block(body)
    if results is None:
        return SerpResult.failed(query, "no results block in response", body)

    organic = _parse_organic(results.get("organic") or [])
    aio_key, aio = _find_aio(results)
    if aio is None:
        return SerpResult(query=query, status=STATUS_NO_AIO, organic=organic, raw=body)

    log.debug("AI Overview found under key %r", aio_key)
    refs = _parse_references(aio)
    return SerpResult(
        query=query,
        status=STATUS_OK,
        shown=True,
        references=refs,
        organic=organic,
        incomplete=not refs,
        raw=body,
    )


def _results_block(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Seen shapes: {"results": {"organic": ...}} and {"results": {"results": {"organic": ...}}}
    block = body.get("results")
    if isinstance(block, dict) and isinstance(block.get("results"), dict):
        block = block["results"]
    return block if isinstance(block, dict) else None


def _find_aio(results: Dict[str, Any]):
    for key in AIO_KEYS:
        value = results.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict) and value:
            return key, value
    return None, None


def _first_url(item: Dict[str, Any]) -> str:
    for key in URL_KEYS:
        if isinstance(item.get(key), str) and item[key]:
            return item[key]
    return ""


def _parse_references(aio: Dict[str, Any]) -> List[Reference]:
    items: List[Any] = []
    for key in REF_KEYS:
        if isinstance(aio.get(key), list):
            items = aio[key]
            break
    refs: List[Reference] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _first_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        refs.append(Reference(url=url, title=str(item.get("title", "")), position=len(refs) + 1))
    return refs


def _parse_organic(items: List[Any]) -> List[OrganicResult]:
    out: List[OrganicResult] = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        url = _first_url(item)
        if not url:
            continue
        pos = item.get("position")
        out.append(OrganicResult(
            url=url,
            title=str(item.get("title") or item.get("site_title") or ""),
            position=int(pos) if isinstance(pos, (int, str)) and str(pos).isdigit() else i,
        ))
    return out


__all__ = ["SerphouseProvider", "parse_response", "STATUS_OK", "STATUS_NO_AIO", "STATUS_FAILED"]
