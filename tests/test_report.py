import csv
from datetime import datetime

from tracker.config import Blog, Cluster
from tracker.output import CHECK_HEADERS, PROMPT_HEADERS
from tracker.prompts import prompt_set_id
from tracker.report.data import RunInputs, build_report, load_runs

CLUSTERS = [Cluster("fde", "Forward Deployed Engineer", 1.0, True, ["fde"]),
            Cluster("genai", "Generative AI", 0.5, True, ["genai"])]
BLOGS = [Blog("Best FDE Training Programs in India", "fde course", "https://www.interviewbit.com/varsity/blog/fde-training/",
              "fde"),
         Blog("GenAI Roadmap", "generative ai roadmap", "https://www.interviewbit.com/varsity/blog/genai-roadmap/", "genai")]
V = "https://www.interviewbit.com/varsity/blog/fde-fees/"


def check(prompt, cluster="fde", status="ok", cited="no", date="2026-11-10", time="09:00", device="desktop",
          domains="", rank="", url="", pos="", pset=""):
    row = dict.fromkeys(CHECK_HEADERS, "")
    row.update({"date": date, "time": time, "prompt": prompt, "cluster": cluster, "device": device,
                "check status": status, "ai overview shown": "yes" if status in ("ok", "incomplete") else "no",
                "varsity cited": cited if status == "ok" else "", "competing domains": domains,
                "organic rank": rank, "cited url": url, "position": pos, "prompt set": pset})
    return row


def prompt(p, cluster="fde", source="gsc", intent="other", inputs="", status="active", tag=""):
    row = dict.fromkeys(PROMPT_HEADERS, "")
    row.update({"prompt": p, "cluster": cluster, "source": source, "intent": intent, "status": status,
                "source inputs": inputs, "brand tag": tag})
    return row


def report(checks, prompts=(), costs=()):
    return build_report(RunInputs(list(prompts), "run", list(checks), list(costs)), CLUSTERS, BLOGS,
                        generated_at=datetime(2026, 11, 10, 10, 0))


def test_kpis_match_summary_definitions():
    r = report([check("a", cited="yes"), check("b"), check("c", status="incomplete"), check("d", status="no_aio"),
                check("e", status="failed")],
               costs=[{"provider": "dataforseo", "billed usd": "0.0120"}, {"provider": "serphouse", "billed usd": ""},
                      {"provider": "dataforseo", "billed usd": "0.0040"},
                      {"provider": "dataforseo", "estimated usd": "0.2000", "billed usd": ""}])
    k = r["kpis"]
    assert (k["checks"], k["ok"], k["failed"]) == (5, 2, 1)
    assert k["aio_presence"] == 75.0     # (2 ok + 1 incomplete) / 4 non-failed
    assert k["citation_rate"] == 50.0    # 1 cited / 2 ok
    assert k["incomplete_rate"] == 33.3  # 1 / 3 AIOs shown
    assert k["cost_usd"] == 0.216 and k["last_check_at"] == "2026-11-10 09:00"
    assert (k["cost_billed_usd"], k["cost_runs_billed"], k["cost_estimated_usd"], k["cost_runs_estimated"]) == (
        0.016, 2, 0.2, 1)  # billed where logged, else the run's estimate


def test_gaps_sorted_by_intent_then_impressions_with_mapped_blog():
    prompts = [prompt("fde salary india", intent="salary", inputs="gsc: fde salary india (900 impr)"),
               prompt("best fde course", intent="best", inputs="gsc: best fde course (50 impr)"),
               prompt("fde course fees", intent="fees", inputs="gsc: fde course fees (10 impr)"),
               prompt("top fde training", intent="best", inputs="blog: GenAI Roadmap; gsc: top fde training (300 impr)")]
    r = report([check("fde salary india", domains="glassdoor.co.in", rank="6"),
                check("best fde course", domains="fde.academy, learnbay.co"),
                check("fde course fees", domains="fde.academy"),
                check("top fde training", domains="x.com")], prompts)
    assert [g["prompt"] for g in r["gaps"]] == ["fde course fees", "top fde training", "best fde course",
                                                "fde salary india"]
    best = next(g for g in r["gaps"] if g["prompt"] == "best fde course")
    assert best["competitors"] == ["fde.academy", "learnbay.co"]
    assert best["blog_url"].endswith("/fde-training/") and best["blog_how"] == "closest title/keyword match"
    top = next(g for g in r["gaps"] if g["prompt"] == "top fde training")
    assert top["blog_title"] == "GenAI Roadmap" and top["blog_how"] == "drafted from"
    assert r["gaps"][-1]["organic_rank"] == 6


def test_latest_check_decides_win_or_gap_and_failed_does_not_hide_it():
    r = report([check("fde course fees", time="08:00"),
                check("fde course fees", time="09:00", cited="yes", url=V, pos="4"),
                check("fde course fees", time="10:00", status="failed")])
    assert r["gaps"] == []
    assert r["wins"] == [{"prompt": "fde course fees", "cluster": "fde", "device": "desktop",
                          "checked_at": "2026-11-10 09:00", "intent": "fees", "impressions": 0,
                          "cited_url": V, "position": 4}]


def test_incomplete_and_no_aio_are_neither_gaps_nor_wins():
    r = report([check("a", status="incomplete"), check("b", status="no_aio")])
    assert r["gaps"] == [] and r["wins"] == []


def test_competing_domains_share_of_aios_with_sources():
    r = report([check("a", domains="youtube.com, x.com"), check("b", domains="youtube.com"),
                check("c", domains=""), check("d", status="no_aio")])
    assert r["aio_with_sources"] == 3
    assert r["domains"][0] == {"domain": "youtube.com", "count": 2, "share": 66.7}


def test_devices_are_separate_rows_and_unchecked_active_prompts_listed():
    current = prompt_set_id(["a", "never checked"])
    r = report([check("a", device="desktop", pset=current), check("a", device="mobile", pset=current)],
               [prompt("a", tag="competitor"), prompt("never checked"), prompt("dropped one", status="dropped")])
    rows = {(p["prompt"], p["device"]): p for p in r["prompts"]}
    assert set(rows) == {("a", "desktop"), ("a", "mobile"), ("never checked", "")}
    assert rows[("a", "mobile")]["brand_tag"] == "competitor"
    assert rows[("never checked", "")]["status"] == "not checked"
    assert r["kpis"]["prompts_tracked"] == 2


def test_trend_one_point_per_check_run_and_clusters_in_config_order():
    r = report([check("a", date="2026-11-09", cited="yes"), check("b", date="2026-11-09"),
                check("c", time="06:00", cluster="genai"), check("d", time="06:00", device="mobile"),
                check("a", time="18:00", cited="yes")])
    assert [(t["run"], t["citation_rate"]) for t in r["trend"]] == [
        ("2026-11-09 09:00", 50.0), ("2026-11-10 06:00", 0.0), ("2026-11-10 18:00", 100.0)]  # two runs on one day
    assert [c["cluster"] for c in r["clusters"]] == ["fde", "genai"]


def test_history_is_per_prompt_and_test_runs_never_count():
    prompts = [prompt("a"), prompt("b"), prompt("c")]                 # active now: a, b, c (c was just added)
    old_set = prompt_set_id(["a", "b", "x"])
    r = build_report(RunInputs(prompts, "run", [
        check("a", time="09:00", cited="yes", pset=prompt_set_id(["a", "b", "c"])),
        check("b", time="09:00", pset=prompt_set_id(["a", "b", "c"])), check("c", time="09:00"),
        check("a", time="06:00", pset=old_set), check("x", time="06:00", pset=old_set),   # x was removed since
        check("a", time="05:00"), check("b", time="05:00"),                  # untagged, a set select produced
        check("a", time="07:00", pset="file:test_prompts.csv"),             # check --prompts test run
        check("a", time="04:00")], [], known_sets=[prompt_set_id(["a", "b"])]),  # untagged, unknown list
        CLUSTERS, BLOGS)
    k = r["kpis"]
    assert k["checks"] == 6  # 3 at 09:00 + a at 06:00 + a, b at 05:00: earlier runs still count for a and b
    assert k["checks_excluded"] == 3
    assert sorted((o["checked_at"][-5:], o["why"]) for o in r["other_runs"]) == [
        ("04:00", "untagged run of a prompt list no select produced (test run)"),
        ("06:00", "checks of prompts no longer active"),
        ("07:00", "check --prompts test_prompts.csv (test run)")]
    # trend: one point per real run, over the prompts that run checked (x included at 06:00)
    assert [(t["run"][-5:], t["prompts"]) for t in r["trend"]] == [("05:00", 2), ("06:00", 2), ("09:00", 3)]


def test_past_checks_match_active_prompts_by_normalised_text():
    s = prompt_set_id(["best fde course"])
    inputs = RunInputs([], "run", [check("Best  FDE course. ", cited="yes", url=V, pos="9", time="05:00", pset=s),
                                   check("best fde course", time="09:00", pset=s),
                                   check("best fde courses", time="09:00", pset=s)], [], active=["best fde course"])
    r = build_report(inputs, CLUSTERS, BLOGS)
    assert r["kpis"]["checks"] == 2 and r["kpis"]["cited"] == 1    # the pilot spelling counts; "courses" doesn't
    assert {c["prompt"] for c in r["by_blog"]["cited"][0]["prompts"]} == {"best fde course"}  # active spelling
    assert [p["prompt"] for p in r["prompts"]] == ["best fde course"]


def test_active_prompts_json_defines_the_current_set():
    inputs = RunInputs([prompt("a"), prompt("b")], "run", [check("a"), check("b")], [], active=["a"])
    r = build_report(inputs, CLUSTERS, BLOGS)
    assert r["kpis"]["prompts_tracked"] == 1 and r["kpis"]["checks"] == 1   # b was dropped; a keeps its check
    assert [o["why"] for o in r["other_runs"]] == ["checks of prompts no longer active"]


def test_blog_and_any_page_citation_rates_and_cited_page():
    def brand(p, url, pos):
        row = check(p)
        row.update({"brand cited": "yes", "brand url": url, "brand position": pos})
        return row
    r = report([check("a", cited="yes", url=V, pos="2"), brand("b", "https://www.scaler.com/academy/fde/", "3"),
                brand("c", "", ""), check("d"), check("e", status="no_aio")])
    k = r["kpis"]
    assert (k["citation_rate"], k["any_rate"], k["any_cited"]) == (25.0, 75.0, 3)
    assert r["clusters"][0]["any_rate"] == 75.0
    rows = {p["prompt"]: p for p in r["prompts"]}
    assert (rows["a"]["any"], rows["a"]["cited_kind"], rows["a"]["any_position"]) == ("yes", "blog", 2)
    assert (rows["b"]["cited_url"], rows["b"]["cited_kind"], rows["b"]["any_position"]) == (
        "https://www.scaler.com/academy/fde/", "program page", 3)
    assert rows["c"]["cited_kind"] == "page not recorded"   # brand cited before brand url was recorded
    assert (rows["d"]["any"], rows["d"]["cited_url"]) == ("no", "")
    gap = next(g for g in r["gaps"] if g["prompt"] == "b")
    assert gap["brand_cited"] and gap["brand_kind"] == "program page"


def test_by_blog_lists_cited_pages_and_mapped_blogs_never_cited():
    fees = "https://www.interviewbit.com/varsity/blog/fde-fees/?utm_source=x"
    r = report([check("fde course fees", cited="yes", url=fees, pos="4"),
                check("fde course fees", time="18:00", cited="yes", url=V, pos="2"),
                check("fde fees india", cited="yes", url=V, pos="5"),
                check("best fde course"), check("genai roadmap 2026", cluster="genai")])
    cited = r["by_blog"]["cited"]
    assert len(cited) == 1  # UTM and trailing-slash variants are one page
    assert cited[0]["best_position"] == 2 and cited[0]["checks"] == 3
    assert cited[0]["prompts"] == [{"prompt": "fde course fees", "position": 2},
                                   {"prompt": "fde fees india", "position": 5}]
    never = {b["title"]: b["prompts"] for b in r["by_blog"]["never_cited"]}
    assert never == {"Best FDE Training Programs in India": ["best fde course", "fde course fees", "fde fees india"],
                     "GenAI Roadmap": ["genai roadmap 2026"]}


def _write(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)


def test_load_runs_collects_every_selects_set_id(tmp_path):
    _write(tmp_path / "dry-run-20261109-100000/prompts.csv", PROMPT_HEADERS, [prompt("old"), prompt("d", status="dropped")])
    _write(tmp_path / "run-20261110-090000/prompts.csv", PROMPT_HEADERS, [prompt("new")])
    assert load_runs(tmp_path).known_sets == [prompt_set_id(["old"]), prompt_set_id(["new"])]


def test_load_runs_uses_latest_prompts_and_all_checks(tmp_path):
    _write(tmp_path / "dry-run-20261109-100000/prompts.csv", PROMPT_HEADERS, [prompt("old")])
    _write(tmp_path / "run-20261110-090000/prompts.csv", PROMPT_HEADERS, [prompt("new")])
    _write(tmp_path / "dry-run-20261109-120000/checks.csv", CHECK_HEADERS, [check("a", date="2026-11-09")])
    _write(tmp_path / "dry-run-20261110-120000/checks.csv", CHECK_HEADERS, [check("b")])
    _write(tmp_path / "cost_log.csv", ["provider", "billed usd"], [{"provider": "dataforseo", "billed usd": "0.004"}])
    inputs = load_runs(tmp_path)
    assert inputs.prompts_from == "run-20261110-090000" and [p["prompt"] for p in inputs.prompts] == ["new"]
    assert [c["prompt"] for c in inputs.checks] == ["a", "b"] and len(inputs.costs) == 1


def test_older_checks_without_a_device_column_still_load(tmp_path):
    old = [h for h in CHECK_HEADERS if h != "device"]
    _write(tmp_path / "dry-run-20261108-100000/checks.csv", old, [{k: v for k, v in check("a").items() if k in old}])
    r = build_report(load_runs(tmp_path), CLUSTERS, BLOGS)
    assert r["prompts"][0]["device"] == "" and r["kpis"]["checks"] == 1


def test_render_embeds_data_safely(tmp_path):
    import json
    import re

    from tracker.report import render

    r = report([check("fde </script><script>alert(1)</script> course", domains="x.com")])
    html = render(r, tmp_path / "index.html").read_text()
    assert "/*__REPORT_DATA__*/" not in html and "</script><script>alert" not in html
    blob = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(blob.replace("<\\/", "</"))["prompts"][0]["prompt"].startswith("fde </script>")
    assert "cdn" not in html.lower() and "fetch(" not in html  # self-contained, no network


def test_report_command_writes_the_dashboard(tmp_path, monkeypatch):
    from tracker import cli

    _write(tmp_path / "runs/dry-run-20261110-120000/checks.csv", CHECK_HEADERS, [check("best fde course")])
    out = tmp_path / "dash/index.html"
    assert cli.main(["report", "--runs", str(tmp_path / "runs"), "--out", str(out),
                     "--blogs", str(tmp_path / "missing.csv")]) == 0
    assert "Varsity AI Overview Citation Tracker" in out.read_text()


def test_sample_mode_uses_only_fake_data(tmp_path, monkeypatch):
    import json
    import re

    from tracker import cli

    monkeypatch.setattr(cli, "RUNS", tmp_path / "must-not-be-read")
    out = tmp_path / "sample.html"
    assert cli.main(["report", "--sample", "--out", str(out)]) == 0
    html = out.read_text()
    data = json.loads(re.search(r'type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    assert data["sample"] is True and len(data["trend"]) >= 2 and data["gaps"] and data["wins"]
    domains = {d for p in data["prompts"] for d in p["competitors"]}
    assert domains and all(d.endswith(".example") for d in domains)  # never a real competitor
