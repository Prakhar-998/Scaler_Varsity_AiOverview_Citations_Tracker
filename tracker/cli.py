"""Command line entry point.

  python -m tracker select --blogs data/blogs.csv --gsc data/gsc.csv [--dry-run]
  python -m tracker check [--dry-run] [--save-raw]      # run twice a day at different times
  python -m tracker probe "best generative ai course in india"   # one SERP call, for spot checks
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from tracker.checks import BudgetExceeded, CallBudget, get_provider
from tracker.checks.base import STATUS_FAILED, SerpResult
from tracker.config import ROOT, load_blogs, load_clusters, load_gsc, load_settings
from tracker.match import match_citations
from tracker.output import CHECK_HEADERS, CsvWriter, SheetsWriter, check_row, prompt_row, summarise
from tracker.prompts import ACTIVE, GeminiDrafter, draft_candidates, resolve_gemini_model, select_prompts

log = logging.getLogger("tracker")
RUNS = ROOT / "runs"
ACTIVE_PROMPTS = RUNS / "active_prompts.json"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader; real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def make_writer(dry_run: bool, stamp: str):
    if dry_run:
        out = RUNS / f"dry-run-{stamp}"
        log.info("dry run: writing CSVs to %s instead of Sheets", out)
        return CsvWriter(out)
    return SheetsWriter(os.environ.get("SHEET_ID", ""), os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""))


def cmd_select(args, settings) -> int:
    clusters = load_clusters()
    blogs = load_blogs(Path(args.blogs), clusters)
    gsc = load_gsc(Path(args.gsc), clusters)
    log.info("%d blogs in active clusters, %d GSC queries", sum(b.cluster is not None for b in blogs), len(gsc))

    model = resolve_gemini_model(settings)
    log.info("drafting with Gemini model %s", model)
    drafter = GeminiDrafter(os.environ.get("GEMINI_API_KEY", ""), model)
    candidates = draft_candidates(blogs, clusters, drafter, int(settings.get("candidates_per_blog", 4)))
    log.info("%d candidates drafted", len(candidates))

    budget = CallBudget(args.max_serp_calls or int(settings["max_serp_calls"]))
    provider = None if args.no_probe else get_provider(args.provider, budget)
    decisions = select_prompts(candidates, gsc, clusters, provider, settings)
    kept = [d for d in decisions if d.status == ACTIVE]
    log.info("kept %d of %d candidates; %d probe SERP calls", len(kept), len(decisions), budget.used)

    RUNS.mkdir(exist_ok=True)
    ACTIVE_PROMPTS.write_text(json.dumps(
        [{"prompt": d.prompt, "cluster": d.candidate.cluster} for d in kept], indent=2))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    make_writer(args.dry_run, stamp).write_prompts([prompt_row(d) for d in decisions])
    return 0


def run_checks(prompts: List[dict], provider, when: datetime, raw_dir: Optional[Path] = None):
    rows = []
    for i, p in enumerate(prompts):
        try:
            result = provider.fetch_ai_overview(p["prompt"])
        except BudgetExceeded:
            log.warning("SERP budget used up after %d of %d prompts", i, len(prompts))
            break
        if raw_dir is not None and result.raw is not None:
            (raw_dir / f"{i:03d}.json").write_text(json.dumps({"query": p["prompt"], "body": result.raw}, indent=2))
        # organic rank is independent of the AI Overview, so match every non-failed check
        m = match_citations(result) if result.status != STATUS_FAILED else None
        rows.append(check_row(when, p["prompt"], p["cluster"], result, m))
    return rows


def cmd_check(args, settings) -> int:
    if not ACTIVE_PROMPTS.exists():
        log.error("no %s; run `select` first", ACTIVE_PROMPTS)
        return 1
    prompts = json.loads(ACTIVE_PROMPTS.read_text())
    budget = CallBudget(args.max_serp_calls or int(settings["max_serp_calls"]))
    if len(prompts) > budget.max_calls:
        log.warning("%d prompts but budget is %d calls; later prompts will be skipped", len(prompts), budget.max_calls)
    provider = get_provider(args.provider, budget)
    now = datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    raw_dir = None
    if args.save_raw:
        raw_dir = RUNS / f"raw-{stamp}"
        raw_dir.mkdir(parents=True, exist_ok=True)

    rows = run_checks(prompts, provider, now, raw_dir)
    writer = make_writer(args.dry_run, stamp)
    writer.append_checks(rows)
    writer.write_summary(summarise(writer.read_checks()))
    col = CHECK_HEADERS.index("check status")
    counts = {s: sum(r[col] == s for r in rows) for s in ("ok", "incomplete", "no_aio", "failed")}
    log.info("%d checks %s; %d SERP calls used", len(rows), counts, budget.used)
    return 0


def cmd_probe(args, settings) -> int:
    provider = get_provider(args.provider, CallBudget(3))
    result: SerpResult = provider.fetch_ai_overview(args.query)
    if args.save:
        Path(args.save).write_text(json.dumps(result.raw, indent=2))
    m = match_citations(result)
    print(f"status: {result.status}  shown: {result.shown}  error: {result.error or '-'}")
    for r in result.references:
        print(f"  [{r.position}] {r.url}")
    print(f"varsity cited: {m.varsity_cited} (pos {m.position})  brand cited: {m.brand_cited}  "
          f"organic rank: {m.organic_rank} ({m.rank_match or 'not found'})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tracker", description="AI Overview citation tracker (Varsity)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--provider", help="override SERP_PROVIDER (serphouse | fake)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="draft, filter and score prompts")
    s.add_argument("--blogs", required=True, help="CSV: title, primary_keyword, url")
    s.add_argument("--gsc", required=True, help="CSV: query, impressions (or raw GSC export)")
    s.add_argument("--no-probe", action="store_true", help="skip probe SERP calls")

    c = sub.add_parser("check", help="check active prompts once")
    c.add_argument("--save-raw", action="store_true", help="save raw SERP JSON under runs/")

    for sp in (s, c):
        sp.add_argument("--dry-run", action="store_true", help="write CSVs under runs/, not Sheets")
        sp.add_argument("--max-serp-calls", type=int, help="cost guard; overrides settings.yaml")

    pr = sub.add_parser("probe", help="fetch one query and print the parsed result")
    pr.add_argument("query")
    pr.add_argument("--save", help="write the raw response JSON to this path")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    settings = load_settings()
    return {"select": cmd_select, "check": cmd_check, "probe": cmd_probe}[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
