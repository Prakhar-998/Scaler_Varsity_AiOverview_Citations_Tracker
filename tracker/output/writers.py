"""Writers: Google Sheets via service account, or local CSVs for --dry-run."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from .rows import BLOG_HEADERS, CHECK_HEADERS, COST_HEADERS, PROMPT_HEADERS, SUMMARY_HEADERS

TAB_PROMPTS, TAB_CHECKS, TAB_SUMMARY, TAB_COSTS, TAB_BLOGS = "Prompts", "Checks", "Summary", "Costs", "Blogs"


def sheets_configured() -> bool:
    return bool(os.environ.get("SHEET_ID") and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))


class CsvWriter:
    """Dry-run writer: never touches Sheets."""

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._checks: List[List] = []

    def _write(self, name: str, headers: List[str], rows: List[List]) -> None:
        with open(self.out_dir / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)

    def write_prompts(self, rows: List[List]) -> None:
        self._write("prompts", PROMPT_HEADERS, rows)

    def append_checks(self, rows: List[List]) -> None:
        self._checks.extend(rows)
        self._write("checks", CHECK_HEADERS, self._checks)

    def read_checks(self) -> List[List]:
        return list(self._checks)

    def write_summary(self, rows: List[List]) -> None:
        self._write("summary", SUMMARY_HEADERS, rows)


class SheetsWriter:
    """The Sheet is the store for real runs: Prompts (the latest select, `status` marks the active set),
    Checks (appended, every run), Summary, Costs (one row per run) and Blogs (for title matching and
    mapping gaps on a machine without data/private/). Values are written RAW, so dates, times and
    prompts come back exactly as written."""

    def __init__(self, sheet_id: str = "", service_account: str = "", book=None):
        if book is None:
            import gspread  # lazy: offline tests and dry runs don't need it

            if not sheet_id or not service_account:
                raise ValueError("SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON must be set (or use --dry-run)")
            if service_account.lstrip().startswith("{"):
                client = gspread.service_account_from_dict(json.loads(service_account))
            else:
                client = gspread.service_account(filename=os.path.expanduser(service_account))
            book = client.open_by_key(sheet_id)
        self.book = book

    @classmethod
    def from_env(cls) -> "SheetsWriter":
        return cls(os.environ.get("SHEET_ID", ""), os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""))

    def _find(self, title: str):
        import gspread

        try:
            return self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            return None

    def _tab(self, title: str, headers: List[str]):
        ws = self._find(title)
        if ws is None:
            ws = self.book.add_worksheet(title=title, rows=1000, cols=len(headers))
        if ws.row_values(1) != headers:
            ws.update(range_name="A1", values=[headers], value_input_option="RAW")
        return ws

    def _replace(self, title: str, headers: List[str], rows: List[List]) -> None:
        ws = self._tab(title, headers)
        ws.clear()
        ws.update(range_name="A1", values=[headers] + rows, value_input_option="RAW")

    def _append(self, title: str, headers: List[str], rows: List[List]) -> None:
        if rows:
            self._tab(title, headers).append_rows(rows, value_input_option="RAW")

    def _records(self, title: str) -> List[Dict[str, str]]:
        """Rows as dicts keyed by the tab's own header row; [] when the tab doesn't exist yet."""
        ws = self._find(title)
        values = ws.get_all_values() if ws is not None else []
        if not values:
            return []
        headers = values[0]
        return [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in values[1:] if any(r)]

    def write_prompts(self, rows: List[List]) -> None:
        self._replace(TAB_PROMPTS, PROMPT_HEADERS, rows)

    def read_prompts(self) -> List[Dict[str, str]]:
        return self._records(TAB_PROMPTS)

    def active_prompts(self) -> List[Dict[str, str]]:
        """The active set `check` runs: Prompts rows with status active."""
        return [{"prompt": r["prompt"], "cluster": r.get("cluster", ""), "source": r.get("source", "")}
                for r in self.read_prompts() if r.get("status") == "active" and r.get("prompt")]

    def append_checks(self, rows: List[List]) -> None:
        self._append(TAB_CHECKS, CHECK_HEADERS, rows)

    def read_check_records(self) -> List[Dict[str, str]]:
        return self._records(TAB_CHECKS)

    def read_checks(self) -> List[List]:
        return [[r.get(h, "") for h in CHECK_HEADERS] for r in self.read_check_records()]

    def write_summary(self, rows: List[List]) -> None:
        self._replace(TAB_SUMMARY, SUMMARY_HEADERS, rows)

    def append_costs(self, rows: List[List]) -> None:
        self._append(TAB_COSTS, COST_HEADERS, rows)

    def read_costs(self) -> List[Dict[str, str]]:
        return self._records(TAB_COSTS)

    def write_blogs(self, rows: List[List]) -> None:
        self._replace(TAB_BLOGS, BLOG_HEADERS, rows)

    def read_blogs(self) -> Optional[List[Dict[str, str]]]:
        """Blogs tab rows, or None when there is no Blogs tab."""
        return self._records(TAB_BLOGS) if self._find(TAB_BLOGS) is not None else None
