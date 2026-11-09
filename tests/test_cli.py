import json
from datetime import datetime

from tracker import cli
from tracker.checks.base import (CallBudget, OrganicResult, Reference, SerpResult, STATUS_INCOMPLETE,
                                 STATUS_NO_AIO, STATUS_OK)
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
        def draft(self, blog, cluster, n):
            if cluster.key == "fde":
                return ["forward deployed engineer salary india"], "fake:model"
            return ["best generative ai course in india", "generative ai course fees", "genai"], "fake:model"

    monkeypatch.setattr(cli, "make_drafter", lambda settings: Drafter())
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    root = cli.ROOT / "data/sample"
    code = cli.main(["--provider", "fake", "select", "--blogs", str(root / "blogs.csv"),
                     "--gsc", str(root / "gsc.csv"), "--dry-run"])
    assert code == 0
    active = json.loads((tmp_path / "active.json").read_text())
    assert {"prompt": "best generative ai course in india", "cluster": "genai"} in active
    assert all(p["prompt"] != "genai" for p in active)


def test_organic_rank_recorded_without_full_aio():
    org = [OrganicResult("https://www.interviewbit.com/varsity/blog/x/", position=3)]
    provider = FakeProvider({"a": SerpResult("a", STATUS_NO_AIO, organic=org),
                             "b": SerpResult("b", STATUS_INCOMPLETE, shown=True, organic=org)})
    rows = cli.run_checks([{"prompt": q, "cluster": "fde"} for q in ("a", "b")], provider, datetime(2026, 11, 6))
    for r in rows:
        d = dict(zip(CHECK_HEADERS, r))
        assert d["organic rank"] == 3 and d["rank match"] == "url"
        assert d["varsity cited"] == ""  # still unknown / not applicable


def test_select_survives_every_llm_failing(tmp_path, monkeypatch, caplog):
    from tracker.llm import LLMChain, LLMClient, LLMError
    from tracker.prompts import LLMDrafter

    class Down(LLMClient):
        provider = "fake"

        def complete(self, prompt):
            raise LLMError("timed out after 60s")

    chain = LLMChain([Down("a"), Down("b")], backoff=0, sleep=lambda s: None)
    monkeypatch.setattr(cli, "make_drafter", lambda settings: LLMDrafter(chain))
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    root = cli.ROOT / "data/sample"
    code = cli.main(["--provider", "fake", "select", "--blogs", str(root / "blogs.csv"),
                     "--gsc", str(root / "gsc.csv"), "--dry-run"])
    assert code == 0
    assert "LLM drafting stopped" in caplog.text and "Traceback" not in caplog.text


def test_select_without_any_llm_configured(tmp_path, monkeypatch, caplog):
    # conftest.offline_env clears every key, so build_chain finds nothing configured
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    root = cli.ROOT / "data/sample"
    code = cli.main(["--provider", "fake", "select", "--blogs", str(root / "blogs.csv"),
                     "--gsc", str(root / "gsc.csv"), "--dry-run"])
    assert code == 0 and "no LLM provider configured" in caplog.text
