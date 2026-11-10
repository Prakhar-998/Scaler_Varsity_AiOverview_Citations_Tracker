"""Render the dashboard: one self-contained HTML file with the report data embedded as JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = "/*__REPORT_DATA__*/"


def render(data: Dict, out: Path) -> Path:
    html = TEMPLATE.read_text(encoding="utf-8")
    # escape "</" and "<!--" so no value (a prompt, a URL) can close the <script> block
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html.replace(PLACEHOLDER, payload, 1), encoding="utf-8")
    return out


__all__ = ["render"]
