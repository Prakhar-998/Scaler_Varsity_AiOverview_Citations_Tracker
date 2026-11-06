import csv
from datetime import datetime

from tracker.checks.base import Reference, SerpResult, STATUS_INCOMPLETE, STATUS_NO_AIO, STATUS_OK
from tracker.match import match_citations
from tracker.output import CHECK_HEADERS, CsvWriter, check_row, summarise

WHEN = datetime(2026, 11, 5, 9, 30)
V = "https://www.interviewbit.com/varsity/blog/x/"


def row(cluster, result):
    m = match_citations(result) if result.status == STATUS_OK else None
    return check_row(WHEN, result.query, cluster, result, m)


def test_check_row_ok_cited():
    r = row("genai", SerpResult("q", STATUS_OK, shown=True, references=[Reference(V, position=2)]))
    d = dict(zip(CHECK_HEADERS, r))
    assert d["date"] == "2026-11-05" and d["time"] == "09:30"
    assert d["ai overview shown"] == "yes" and d["varsity cited"] == "yes" and d["position"] == 2


def test_no_aio_and_failed_are_distinct():
    no = dict(zip(CHECK_HEADERS, row("genai", SerpResult("q", STATUS_NO_AIO))))
    bad = dict(zip(CHECK_HEADERS, row("genai", SerpResult.failed("q", "HTTP 500"))))
    assert no["check status"] == "no_aio" and no["ai overview shown"] == "no"
    assert bad["check status"] == "failed" and bad["ai overview shown"] == "" and bad["error"] == "HTTP 500"


def test_incomplete_row_has_aio_but_unknown_citation():
    d = dict(zip(CHECK_HEADERS, row("fde", SerpResult("q", STATUS_INCOMPLETE, shown=True))))
    assert d["check status"] == "incomplete" and d["ai overview shown"] == "yes"
    assert d["varsity cited"] == "" and d["brand cited"] == ""


def test_summary_excludes_failed_from_rates():
    rows = [
        row("genai", SerpResult("a", STATUS_OK, shown=True, references=[Reference(V, position=1)])),
        row("genai", SerpResult("b", STATUS_OK, shown=True, references=[Reference("https://z.com", position=1)])),
        row("genai", SerpResult("c", STATUS_NO_AIO)),
        row("genai", SerpResult.failed("d", "x")),
        row("fde", SerpResult("e", STATUS_NO_AIO)),
    ]
    s = {r[0]: r for r in summarise(rows)}
    assert s["genai"] == ["genai", 4, 2, 1, 1, "66.7", "50.0", "0.0"]
    assert s["fde"][5] == "0.0" and s["fde"][6] == ""
    assert summarise(rows)[-1][0] == "ALL" and s["ALL"][1] == 5


def test_csv_writer_appends(tmp_path):
    w = CsvWriter(tmp_path)
    w.append_checks([["a"] * len(CHECK_HEADERS)])
    w.append_checks([["b"] * len(CHECK_HEADERS)])
    lines = list(csv.reader(open(tmp_path / "checks.csv")))
    assert lines[0] == CHECK_HEADERS and len(lines) == 3
    assert len(w.read_checks()) == 2
