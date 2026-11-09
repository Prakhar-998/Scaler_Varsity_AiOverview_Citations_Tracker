"""Load YAML config and the CSV inputs (blogs, GSC queries)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@dataclass
class Cluster:
    key: str
    name: str
    weight: float = 1.0
    active: bool = True
    match_terms: List[str] = field(default_factory=list)

    def matches(self, text: str) -> bool:
        t = text.lower()
        return any(term.lower() in t for term in self.match_terms)


@dataclass
class Blog:
    title: str
    primary_keyword: str
    url: str = ""
    cluster: Optional[str] = None


@dataclass
class GscQuery:
    query: str
    impressions: int
    cluster: Optional[str] = None


def load_yaml(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    return load_yaml(path or CONFIG_DIR / "settings.yaml")


def load_clusters(path: Optional[Path] = None, active_only: bool = True) -> List[Cluster]:
    data = load_yaml(path or CONFIG_DIR / "programs.yaml")
    clusters = [Cluster(**c) for c in data.get("clusters", [])]
    return [c for c in clusters if c.active] if active_only else clusters


@dataclass
class BrandRules:
    brand_terms: List[str] = field(default_factory=list)
    competitors: List[str] = field(default_factory=list)

    @staticmethod
    def _find(text: str, terms: List[str]) -> str:
        t = text.lower()
        for term in terms:
            if re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", t):
                return term
        return ""

    def brand(self, text: str) -> str:
        """Our own brand term found in text, or ''."""
        return self._find(text, self.brand_terms)

    def competitor(self, text: str) -> str:
        return self._find(text, self.competitors)


def load_brand_rules(path: Optional[Path] = None) -> BrandRules:
    data = load_yaml(path or CONFIG_DIR / "programs.yaml")
    return BrandRules(list(data.get("brand_terms") or []), list(data.get("competitors") or []))


def assign_cluster(text: str, clusters: List[Cluster]) -> Optional[str]:
    for c in clusters:
        if c.matches(text):
            return c.key
    return None


def _pick(row: Dict[str, str], *names: str) -> str:
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for n in names:
        if n in lowered and lowered[n] is not None:
            return lowered[n].strip()
    return ""


def load_blogs(path: Path, clusters: List[Cluster]) -> List[Blog]:
    blogs = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            title = _pick(row, "title", "post title")
            kw = _pick(row, "primary_keyword", "primary keyword", "focus keyword", "keyword")
            if not title:
                continue
            blogs.append(Blog(title, kw, _pick(row, "url", "link", "permalink"),
                              assign_cluster(f"{title} {kw}", clusters)))
    return blogs


def _to_int(value: str) -> int:
    try:
        return int(float(value.replace(",", "")))
    except (AttributeError, ValueError):
        return 0


def load_gsc(path: Path, clusters: List[Cluster]) -> List[GscQuery]:
    """Accepts our CSV or a raw GSC 'Queries' export ('Top queries', 'Impressions')."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            q = " ".join(_pick(row, "query", "top queries", "queries").split())  # GSC can include newlines
            imp = _to_int(_pick(row, "impressions"))
            if q and imp > 0:
                rows.append(GscQuery(q.lower(), imp, assign_cluster(q, clusters)))
    return rows
