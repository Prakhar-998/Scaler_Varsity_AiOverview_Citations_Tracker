import pytest
import csv
import json
from datetime import datetime
from pathlib import Path

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
    assert json.loads((tmp_path / "mobile-000.json").read_text())["query"] == "a"
    assert first["device"] == "mobile"


def test_check_dry_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    (tmp_path / "active.json").write_text(json.dumps([{"prompt": "a", "cluster": "fde"}]))
    assert cli.main(["--provider", "fake", "check", "--dry-run"]) == 0
    out = next(tmp_path.glob("dry-run-*"))
    assert (out / "checks.csv").exists() and (out / "summary.csv").exists()
    from tracker.prompts import prompt_set_id
    row = next(csv.DictReader(open(out / "checks.csv")))
    assert row["prompt set"] == prompt_set_id(["a"])  # tagged with the active set's id


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
    # drafted by the LLM and also a real GSC query: the GSC version is kept
    assert {"prompt": "best generative ai course in india", "cluster": "genai", "source": "gsc"} in active
    assert all(p["prompt"] != "genai" for p in active)
    rows = list(csv.DictReader(open(next(tmp_path.glob("dry-run-*")) / "prompts.csv")))
    best = next(r for r in rows if r["prompt"] == "best generative ai course in india" and r["status"] == "active")
    assert best["source"] == "gsc" and "also from llm" in best["source inputs"]


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


def _select(tmp_path, monkeypatch, *extra, blogs=True):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    root = cli.ROOT / "data/sample"
    argv = ["--provider", "fake", "select", "--gsc", str(root / "gsc.csv"), "--dry-run", *extra]
    if blogs:
        argv += ["--blogs", str(root / "blogs.csv")]
    code = cli.main(argv)
    active = json.loads((tmp_path / "active.json").read_text()) if (tmp_path / "active.json").exists() else []
    return code, active


def test_source_gsc_never_touches_the_llm(tmp_path, monkeypatch):
    def boom(settings):
        raise AssertionError("LLM must not be used with --source gsc")

    monkeypatch.setattr(cli, "make_drafter", boom)
    code, active = _select(tmp_path, monkeypatch, "--source", "gsc", blogs=False)
    assert code == 0
    assert {p["prompt"] for p in active} == {"forward deployed engineer salary india",
                                             "best generative ai course in india", "generative ai course fees"}
    assert {p["source"] for p in active} == {"gsc"}  # "forward deployed engineer" has no BOFU term


def test_both_falls_back_to_gsc_only_when_every_llm_fails(tmp_path, monkeypatch, caplog):
    from tracker.llm import LLMChain, LLMClient, LLMError
    from tracker.prompts import LLMDrafter

    class Down(LLMClient):
        provider = "openrouter"

        def complete(self, prompt):
            raise LLMError("HTTP 529", retryable=True)

    chain = LLMChain([Down("a:free"), Down("b:free")], max_retries=2, backoff=0, sleep=lambda s: None)
    monkeypatch.setattr(cli, "make_drafter", lambda settings: LLMDrafter(chain))
    code, active = _select(tmp_path, monkeypatch)
    assert code == 0
    assert active and {p["source"] for p in active} == {"gsc"}
    assert "continuing with GSC candidates only" in caplog.text


def test_source_llm_requires_blogs(tmp_path, monkeypatch, caplog):
    code, _ = _select(tmp_path, monkeypatch, "--source", "llm", blogs=False)
    assert code == 2 and "--blogs is required" in caplog.text


def test_limit_drafts_only_first_n_blogs(tmp_path, monkeypatch, caplog):
    import logging

    seen = []

    class Drafter:
        def draft(self, blog, cluster, n):
            seen.append(blog.title)
            return ["best generative ai course for working professionals"], "fake:model"

    monkeypatch.setattr(cli, "make_drafter", lambda settings: Drafter())
    with caplog.at_level(logging.INFO):
        code, _ = _select(tmp_path, monkeypatch, "--source", "llm", "--limit", "1", "--no-probe")
    assert code == 0
    assert seen == ["What Is a Forward Deployed Engineer? Role and Salary in India"]  # first of 2 sample blogs
    assert "drafting 1/1 via fake:model" in caplog.text


def test_no_limit_drafts_all_blogs(tmp_path, monkeypatch):
    seen = []

    class Drafter:
        def draft(self, blog, cluster, n):
            seen.append(blog.title)
            return [], "fake:model"

    monkeypatch.setattr(cli, "make_drafter", lambda settings: Drafter())
    _select(tmp_path, monkeypatch, "--source", "llm", "--no-probe")
    assert len(seen) == 2


def test_provider_flag_after_the_command(tmp_path, monkeypatch, capsys):
    assert cli.main(["probe", "--provider", "fake", "what is a forward deployed engineer"]) == 0
    assert "status: no_aio" in capsys.readouterr().out


def test_serp_provider_env_selects_provider_for_every_command(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("SERP_PROVIDER", "fake")
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    (tmp_path / "active.json").write_text(json.dumps([{"prompt": "a", "cluster": "fde"}]))
    with caplog.at_level(logging.INFO):
        assert cli.main(["probe", "q"]) == 0
        assert cli.main(["check", "--dry-run"]) == 0
        code, _ = _select(tmp_path, monkeypatch, "--source", "gsc", blogs=False)
    assert code == 0
    assert caplog.text.count("SERP provider: fake") == 3


def test_flag_beats_env_and_missing_credentials_are_a_clear_error(monkeypatch, caplog):
    monkeypatch.setenv("SERP_PROVIDER", "fake")
    assert cli.main(["probe", "--provider", "dataforseo", "q"]) == 2
    assert "SERP provider 'dataforseo': DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD must be set" in caplog.text
    assert "Traceback" not in caplog.text


def _check(tmp_path, monkeypatch, *extra):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    (tmp_path / "active.json").write_text(json.dumps([{"prompt": "a", "cluster": "fde"},
                                                      {"prompt": "b", "cluster": "genai"}]))
    code = cli.main(["--provider", "fake", "check", "--dry-run", *extra])
    out = next(tmp_path.glob("dry-run-*"))
    return code, list(csv.DictReader(open(out / "checks.csv"))), list(csv.DictReader(open(out / "summary.csv")))


def test_check_uses_devices_setting_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_settings", lambda: {"max_serp_calls": 10, "devices": ["desktop"]})
    code, checks, summary = _check(tmp_path, monkeypatch)
    assert code == 0 and {r["device"] for r in checks} == {"desktop"} and len(checks) == 2
    assert [(r["cluster"], r["device"]) for r in summary] == [("fde", "desktop"), ("genai", "desktop"),
                                                             ("ALL", "desktop")]


def test_device_flag_overrides_and_runs_each_device(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_settings", lambda: {"max_serp_calls": 10, "devices": ["desktop"]})
    code, checks, summary = _check(tmp_path, monkeypatch, "--device", "mobile", "--device", "desktop")
    assert code == 0
    assert [(r["prompt"], r["device"]) for r in checks] == [("a", "mobile"), ("b", "mobile"),
                                                           ("a", "desktop"), ("b", "desktop")]
    keys = [(r["cluster"], r["device"]) for r in summary]
    assert keys == [("fde", "desktop"), ("fde", "mobile"), ("genai", "desktop"), ("genai", "mobile"),
                    ("ALL", "desktop"), ("ALL", "mobile"), ("ALL", "ALL")]
    assert summary[-1]["checks"] == "4"


def test_unknown_device_in_settings_is_a_clear_error(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cli, "load_settings", lambda: {"max_serp_calls": 10, "devices": ["tablet"]})
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    (tmp_path / "active.json").write_text("[]")
    assert cli.main(["--provider", "fake", "check", "--dry-run"]) == 2
    assert "unknown device(s) tablet" in caplog.text


def test_probe_uses_first_device(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(cli, "load_settings", lambda: {"devices": ["desktop", "mobile"]})
    with caplog.at_level(logging.INFO):
        assert cli.main(["probe", "--provider", "fake", "q"]) == 0
    assert "SERP provider: fake (desktop)" in caplog.text


def test_per_command_default_provider(monkeypatch):
    settings = cli.load_settings()
    parse = cli.build_parser().parse_args
    assert cli.provider_name(parse(["check"]), settings) == "dataforseo"
    assert cli.provider_name(parse(["probe", "q"]), settings) == "dataforseo"
    assert cli.provider_name(parse(["select", "--gsc", "x.csv"]), settings) == "serphouse"
    assert cli.provider_name(parse(["check", "--provider", "serphouse"]), settings) == "serphouse"


def test_serp_provider_env_overrides_the_default_with_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("SERP_PROVIDER", "serphouse")
    args = cli.build_parser().parse_args(["check"])
    assert cli.provider_name(args, cli.load_settings()) == "serphouse"
    assert "SERP_PROVIDER=serphouse overrides the check default (dataforseo)" in caplog.text


def test_check_logs_estimated_cost(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(cli, "load_settings", lambda: {"max_serp_calls": 10, "devices": ["desktop", "mobile"],
                                                      "cost_per_check_usd": {"fake": 0.004}})
    with caplog.at_level(logging.INFO):
        code, _, _ = _check(tmp_path, monkeypatch)
    assert code == 0
    assert "estimated cost: 4 checks x $0.004 = $0.02 on fake" in caplog.text  # 2 prompts x 2 devices


def test_drafted_candidates_saved_before_any_probe(tmp_path, monkeypatch):
    seen_on_disk = []

    class Drafter:
        def draft(self, blog, cluster, n):
            return ["best generative ai course for working professionals", "genai course fees in india"], "fake:m"

    class Provider(FakeProvider):
        def fetch_ai_overview(self, query, retry_incomplete=True):
            files = list(tmp_path.glob("dry-run-*/candidates.csv"))
            seen_on_disk.append(len(list(csv.DictReader(open(files[0])))) if files else 0)
            return super().fetch_ai_overview(query, retry_incomplete)

    monkeypatch.setattr(cli, "make_drafter", lambda settings: Drafter())
    monkeypatch.setattr(cli, "get_provider", lambda name, budget, device="mobile": Provider(budget=budget))
    code, _ = _select(tmp_path, monkeypatch, "--source", "llm")
    assert code == 0 and seen_on_disk and seen_on_disk[0] == 4  # 2 blogs x 2 drafts, on disk before probe 1
    rows = list(csv.DictReader(open(next(tmp_path.glob("dry-run-*/candidates.csv")))))
    assert rows[0] == {"prompt": "best generative ai course for working professionals", "cluster": "fde",
                       "source": "llm", "intent": "best", "source blog": rows[0]["source blog"]}


def _prompt_file(tmp_path, text):
    path = tmp_path / "prompts.csv"
    path.write_text(text)
    return str(path)


def test_source_none_uses_only_the_prompt_file(tmp_path, monkeypatch):
    def boom(settings):
        raise AssertionError("no drafting with --prompts and --source none")

    monkeypatch.setattr(cli, "make_drafter", boom)
    path = _prompt_file(tmp_path, "prompt,cluster\nbest fde course in india,fde\nfde course fees,fde\n"
                                  "best fde course in india,fde\nnot a cluster prompt here,xyz\n")
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    provider = FakeProvider({q: SerpResult(q, STATUS_INCOMPLETE, shown=True)
                             for q in ("best fde course in india", "fde course fees")})
    monkeypatch.setattr(cli, "get_provider", lambda name, budget, device="mobile": provider)
    code = cli.main(["select", "--source", "none", "--prompts", path, "--dry-run"])
    assert code == 0
    active = json.loads((tmp_path / "active.json").read_text())
    assert sorted(active, key=lambda p: p["prompt"]) == [
        {"prompt": "best fde course in india", "cluster": "fde", "source": "manual"},
        {"prompt": "fde course fees", "cluster": "fde", "source": "manual"}]
    assert sorted(provider.calls) == ["best fde course in india", "fde course fees"]  # duplicate never probed
    rows = list(csv.DictReader(open(next(tmp_path.glob("dry-run-*")) / "prompts.csv")))
    assert any(r["reason"] == "exact duplicate" for r in rows)


def test_prompts_combine_with_gsc_and_reuse_a_saved_candidates_file(tmp_path, monkeypatch):
    path = _prompt_file(tmp_path, "prompt,cluster,source,intent,source blog\n"
                                  "best generative ai course in india,genai,llm,best,Some blog\n"
                                  "generative ai course for working professionals,genai,llm,,Some blog\n")
    code, active = _select(tmp_path, monkeypatch, "--source", "gsc", "--prompts", path, "--no-probe", blogs=False)
    assert code == 0
    by_prompt = {p["prompt"]: p["source"] for p in active}
    assert by_prompt["best generative ai course in india"] == "gsc"  # GSC version wins the duplicate
    # no GSC match and no probe, but kept: --prompts with --no-probe is a curated list
    assert by_prompt["generative ai course for working professionals"] == "llm"


def test_source_none_without_prompts_is_an_error(tmp_path, monkeypatch, caplog):
    code, _ = _select(tmp_path, monkeypatch, "--source", "none", blogs=False)
    assert code == 2 and "--source none needs --prompts" in caplog.text


def test_check_prompt_file_leaves_active_prompts_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    real = json.dumps([{"prompt": "the real set", "cluster": "fde"}])
    (tmp_path / "active.json").write_text(real)
    path = _prompt_file(tmp_path, "prompt,cluster\nwhat is a forward deployed engineer,fde\n"
                                  "best generative ai course in india,genai\n")
    assert cli.main(["--provider", "fake", "check", "--prompts", path, "--dry-run"]) == 0
    checks = list(csv.DictReader(open(next(tmp_path.glob("dry-run-*")) / "checks.csv")))
    assert [r["prompt"] for r in checks] == ["what is a forward deployed engineer", "best generative ai course in india"]
    assert (tmp_path / "active.json").read_text() == real
    assert {r["prompt set"] for r in checks} == {"file:" + Path(path).name}  # never passes as the active set


def test_check_aborts_before_any_call_when_over_max_cost(tmp_path, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: {"max_serp_calls": 10, "devices": ["desktop"],
                                                      "cost_per_check_usd": {"fake": 0.25}})
    monkeypatch.setattr(cli, "run_checks", lambda *a, **k: calls.append(a) or [])
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    (tmp_path / "active.json").write_text(json.dumps([{"prompt": "a", "cluster": "fde"},
                                                      {"prompt": "b", "cluster": "fde"}]))
    assert cli.main(["--provider", "fake", "check", "--dry-run", "--max-cost", "0.40"]) == 3
    assert calls == [] and "estimated cost $0.50 is over --max-cost $0.40" in caplog.text
    assert cli.main(["--provider", "fake", "check", "--dry-run", "--max-cost", "0.50"]) == 0


def test_check_appends_to_the_cost_log(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_settings", lambda: {"max_serp_calls": 10, "devices": ["desktop"],
                                                      "cost_per_check_usd": {"fake": 0.004}})
    _check(tmp_path, monkeypatch)
    _check(tmp_path, monkeypatch)
    rows = list(csv.DictReader(open(tmp_path / "cost_log.csv")))
    assert len(rows) == 2
    assert {k: rows[0][k] for k in ("command", "provider", "devices", "checks", "serp calls", "estimated usd",
                                    "billed usd")} == {"command": "check", "provider": "fake", "devices": "desktop",
                                                       "checks": "2", "serp calls": "2", "estimated usd": "0.0080",
                                                       "billed usd": ""}


def test_dataforseo_tracks_billed_cost(load_fixture):
    from tests.test_dataforseo import FakeResponse, FakeSession
    from tracker.checks.dataforseo import DataForSeoProvider

    session = FakeSession([FakeResponse(body=load_fixture("dataforseo/aio_fde_salary_india_desktop.json")),
                           FakeResponse(body=load_fixture("dataforseo/no_tasks_ok_envelope.json"))])
    provider = DataForSeoProvider("l", "p", session=session, backoff=0)
    provider.fetch_ai_overview("a")
    provider.fetch_ai_overview("b")
    assert provider.spent_usd == pytest.approx(0.004)  # the empty-tasks response cost 0


def test_select_prompts_no_probe_keeps_the_curated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "ACTIVE_PROMPTS", tmp_path / "active.json")
    path = _prompt_file(tmp_path, "prompt,cluster\nwhat is a forward deployed engineer,fde\n"
                                  "best generative ai course in india,genai\n")
    assert cli.main(["select", "--source", "none", "--prompts", path, "--no-probe", "--dry-run"]) == 0
    assert sorted(p["prompt"] for p in json.loads((tmp_path / "active.json").read_text())) == [
        "best generative ai course in india", "what is a forward deployed engineer"]
    rows = list(csv.DictReader(open(next(tmp_path.glob("dry-run-*")) / "prompts.csv")))
    assert {r["reason"] for r in rows} == {"curated, not probed"}
