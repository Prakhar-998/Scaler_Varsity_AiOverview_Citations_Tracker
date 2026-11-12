"""The Sheet as the store for real runs, with an in-memory stand-in for the gspread spreadsheet."""

import gspread
import pytest

from tracker import cli
from tracker.output import CHECK_HEADERS, PROMPT_HEADERS, SheetsWriter
from tracker.prompts import prompt_set_id


class FakeTab:
    def __init__(self, title):
        self.title, self.values, self.input_options = title, [], set()

    def row_values(self, n):
        return list(self.values[n - 1]) if len(self.values) >= n else []

    def update(self, range_name, values, value_input_option=None):
        assert range_name == "A1"
        self.input_options.add(value_input_option)
        for i, row in enumerate(values):
            if i < len(self.values):
                self.values[i] = [str(v) for v in row]
            else:
                self.values.append([str(v) for v in row])

    def clear(self):
        self.values = []

    def append_rows(self, rows, value_input_option=None):
        self.input_options.add(value_input_option)
        self.values += [[str(v) for v in r] for r in rows]

    def get_all_values(self):
        return [list(r) for r in self.values]


class FakeBook:
    def __init__(self):
        self.tabs = {}

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeTab(title)
        return self.tabs[title]


def prompt_row(p, cluster="fde", status="active", source="gsc"):
    return [{"prompt": p, "cluster": cluster, "status": status, "source": source}.get(h, "") for h in PROMPT_HEADERS]


@pytest.fixture
def sheet(monkeypatch):
    store = SheetsWriter(book=FakeBook())
    monkeypatch.setattr(SheetsWriter, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(cli, "sheets_configured", lambda: True)
    return store


def test_store_round_trip_is_raw():
    store = SheetsWriter(book=FakeBook())
    store.write_prompts([prompt_row("best fde course"), prompt_row("old one", status="dropped")])
    assert store.active_prompts() == [{"prompt": "best fde course", "cluster": "fde", "source": "gsc"}]
    row = ["2026-11-12", "06:30", "=1+1"] + [""] * (len(CHECK_HEADERS) - 3)
    store.append_checks([row])
    assert store.read_checks() == [row]  # dates, times and formula-like prompts come back as written
    assert store.book.tabs["Checks"].input_options == {"RAW"}
    assert store.read_blogs() is None and store.read_costs() == []
    store.write_blogs([["FDE Fees", "fde fees", "https://www.interviewbit.com/varsity/blog/fde-fees/"]])
    assert store.read_blogs() == [{"title": "FDE Fees", "primary_keyword": "fde fees",
                                   "url": "https://www.interviewbit.com/varsity/blog/fde-fees/"}]


def test_older_checks_tab_reads_by_its_own_header_row():
    store = SheetsWriter(book=FakeBook())
    tab = store.book.add_worksheet("Checks", 10, 3)
    tab.values = [["date", "time", "prompt"], ["2026-11-10", "09:00", "a"]]
    assert store.read_check_records() == [{"date": "2026-11-10", "time": "09:00", "prompt": "a"}]


def test_check_reads_the_active_set_from_the_sheet_and_appends(sheet, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "must-not-be-read.json")
    monkeypatch.setattr(cli, "load_settings", lambda: {"max_serp_calls": 10, "devices": ["desktop"],
                                                      "cost_per_check_usd": {"fake": 0.004}})
    sheet.write_prompts([prompt_row("a"), prompt_row("b"), prompt_row("c", status="dropped")])
    assert cli.main(["--provider", "fake", "check"]) == 0
    assert cli.main(["--provider", "fake", "check"]) == 0
    checks = sheet.read_check_records()
    assert [r["prompt"] for r in checks] == ["a", "b", "a", "b"]
    assert {r["prompt set"] for r in checks} == {prompt_set_id(["a", "b"])}
    assert [r["command"] for r in sheet.read_costs()] == ["check", "check"]
    assert sheet.book.tabs["Summary"].values[0][0] == "cluster"
    assert not list(tmp_path.glob("dry-run-*"))  # nothing written as CSVs


def test_check_without_active_prompts_in_the_sheet_fails(sheet, caplog):
    assert cli.main(["--provider", "fake", "check"]) == 1
    assert "no active prompts in the Sheet" in caplog.text


def test_blogs_come_from_the_sheet_when_the_file_is_missing(sheet):
    sheet.write_blogs([["FDE Fees", "fde fees", "https://www.interviewbit.com/varsity/blog/fde-fees/"]])
    assert [b.title for b in cli.varsity_blogs(sheet)] == ["FDE Fees"]
    assert cli.varsity_blogs(None) == []
