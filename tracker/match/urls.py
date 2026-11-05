"""URL normalisation so the same page compares equal however Google links it."""
from __future__ import annotations

from urllib.parse import unquote, urlsplit


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
