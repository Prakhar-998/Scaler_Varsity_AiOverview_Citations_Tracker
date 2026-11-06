"""URL normalisation so the same page compares equal however Google links it."""
from __future__ import annotations

from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

REDIRECT_PATHS = ("/url", "/goto")
REDIRECT_PARAMS = ("url", "q")


def _is_google_host(host: str) -> bool:
    host = host.lower().split(":")[0]
    return host == "google.com" or host.endswith(".google.com")


def is_google_redirect(url: str) -> bool:
    """Google click-through links such as google.com/goto?url=... or google.com/url?q=..."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    return _is_google_host(parts.netloc) and parts.path in REDIRECT_PATHS


def unwrap_google_redirect(url: str) -> Optional[str]:
    """Return the destination of a Google redirect link, or None if it can't be read.

    Only plain (URL-encoded) destinations are decoded. The mobile `goto?url=` links SERPHouse
    returns carry an opaque, apparently encrypted token instead (base64 protobuf, no readable URL),
    so they return None and callers must fall back to something else.
    Non-redirect URLs are returned unchanged.
    """
    if not is_google_redirect(url):
        return url
    query = parse_qs(urlsplit(url if "://" in url else "https://" + url).query)
    for key in REDIRECT_PARAMS:
        for value in query.get(key, []):
            value = unquote(value).strip()
            if value.lower().startswith(("http://", "https://")) and not is_google_redirect(value):
                return value
    return None


def _strip_google_amp_cache(url: str) -> str:
    # https://www.google.com/amp/s/www.site.com/page/amp/ -> https://www.site.com/page/amp/
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.endswith("google.com") and parts.path.startswith("/amp/"):
        rest = parts.path[len("/amp/"):]
        if rest.startswith("s/"):
            rest = rest[2:]
        return "https://" + unquote(rest)
    return url


def normalise_url(url: str) -> str:
    """Return `host/path` with no scheme, www, query, fragment, AMP marker or trailing slash."""
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    url = unwrap_google_redirect(url) or url  # opaque redirects stay as google.com/goto
    url = _strip_google_amp_cache(url)
    parts = urlsplit(url)
    host = parts.netloc.lower().split("@")[-1].split(":")[0]
    for prefix in ("www.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = unquote(parts.path).rstrip("/")
    if path.endswith("/amp"):
        path = path[: -len("/amp")]
    elif path.endswith(".amp"):
        path = path[: -len(".amp")]
    return f"{host}{path.lower()}"


def domain_of(url: str) -> str:
    return normalise_url(url).split("/", 1)[0]
