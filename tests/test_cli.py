import json
from datetime import datetime

from tracker import cli
from tracker.checks.base import CallBudget, Reference, SerpResult, STATUS_OK
from tracker.checks.fake import FakeProvider
from tracker.output import CHECK_HEADERS

V = "https://www.interviewbit.com/varsity/blog/x/"


def test_run_checks_stops_at_budget(tmp_path):
    provider = FakeProvider({"a": SerpResult("a", STATUS_OK, shown=True, references=[Reference(V, position=1)],
                                             raw={"k": 1})}, budget=CallBudget(2))
    prompts = [{"prompt": q, "cluster": "genai"} for q in ("a", "b", "c")]
    rows = cli.run_checks(prompts, provider, datetime(2026, 11, 5, 18, 0), raw_dir=tmp_path)
    assert len(rows) == 2
    first = dict(zip(CHECK_HEADERS, rows[0]))
    assert first["varsity cited"] == "yes" and rows[1][CHECK_HEADERS.index("check status")] == "no_aio"
    assert json.loads((tmp_path / "000.json").read_text())["query"] == "a"


def test_check_dry_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    (tmp_path / "active.json").write_text(json.dumps([{"prompt": "a", "cluster": "fde"}]))
    assert cli.main(["--provider", "fake", "check", "--dry-run"]) == 0
    out = next(tmp_path.glob("dry-run-*"))
    assert (out / "checks.csv").exists() and (out / "summary.csv").exists()


def test_check_without_select_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "missing.json")
    assert cli.main(["--provider", "fake", "check", "--dry-run"]) == 1


def test_select_dry_run_with_fake_drafter(tmp_path, monkeypatch):
    class Drafter:
        def __init__(self, *a, **k):
            pass

        def draft(self, blog, cluster, n):
            if cluster.key == "fde":
                return ["forward deployed engineer salary india"]
            return ["best generative ai course in india", "generative ai course fees", "genai"]

    monkeypatch.setattr(cli, "GeminiDrafter", Drafter)
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    root = cli.ROOT / "data/sample"
    code = cli.main(["--provider", "fake", "select", "--blogs", str(root / "blogs.csv"),
                     "--gsc", str(root / "gsc.csv"), "--dry-run"])
    assert code == 0
    active = json.loads((tmp_path / "active.json").read_text())
    assert {"prompt": "best generative ai course in india", "cluster": "genai"} in active
    assert all(p["prompt"] != "genai" for p in active)
