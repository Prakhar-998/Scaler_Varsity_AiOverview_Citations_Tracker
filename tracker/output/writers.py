"""Writers: Google Sheets via service account, or local CSVs for --dry-run."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import List

from .rows import CHECK_HEADERS, PROMPT_HEADERS, SUMMARY_HEADERS


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
    def __init__(self, sheet_id: str, service_account: str):
        import gspread  # lazy: offline tests and dry runs don't need it

        if not sheet_id or not service_account:
            raise ValueError("SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON must be set (or use --dry-run)")
        if service_account.lstrip().startswith("{"):
            client = gspread.service_account_from_dict(json.loads(service_account))
        else:
            client = gspread.service_account(filename=os.path.expanduser(service_account))
        self.book = client.open_by_key(sheet_id)

    def _tab(self, title: str, headers: List[str]):
        import gspread

        try:
            ws = self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(title=title, rows=1000, cols=len(headers))
        if ws.row_values(1) != headers:
            ws.update(range_name="A1", values=[headers])
        return ws

    def _replace(self, title: str, headers: List[str], rows: List[List]) -> None:
        ws = self._tab(title, headers)
        ws.clear()
        ws.update(range_name="A1", values=[headers] + rows)

    def write_prompts(self, rows: List[List]) -> None:
        self._replace("Prompts", PROMPT_HEADERS, rows)

    def append_checks(self, rows: List[List]) -> None:
        if rows:
            self._tab("Checks", CHECK_HEADERS).append_rows(rows, value_input_option="USER_ENTERED")

    def read_checks(self) -> List[List]:
        values = self._tab("Checks", CHECK_HEADERS).get_all_values()[1:]
        width = len(CHECK_HEADERS)
        return [(r + [""] * width)[:width] for r in values]

    def write_summary(self, rows: List[List]) -> None:
        self._replace("Summary", SUMMARY_HEADERS, rows)
