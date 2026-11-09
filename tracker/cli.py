"""Command line entry point.

  python -m tracker select --blogs data/blogs.csv --gsc data/gsc.csv [--dry-run]
  python -m tracker check [--dry-run] [--save-raw]      # run twice a day at different times
  python -m tracker probe "best generative ai course in india"   # one SERP call, for spot checks

Every command takes --provider serphouse|dataforseo|fake (default: SERP_PROVIDER, else serphouse)
and --device desktop|mobile (repeatable; default: `devices` in config/settings.yaml).
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

from tracker.checks import DEVICES, BudgetExceeded, CallBudget, get_provider
from tracker.checks.base import STATUS_FAILED, SerpResult
from tracker.config import ROOT, load_blogs, load_brand_rules, load_clusters, load_gsc, load_settings
from tracker.match import match_citations
from tracker.output import CHECK_HEADERS, CsvWriter, SheetsWriter, check_row, prompt_row, summarise
from tracker.llm import build_chain
from tracker.prompts import ACTIVE, LLMDrafter, draft_candidates, gsc_candidates, select_prompts, tag_brands

log = logging.getLogger("tracker")
RUNS = ROOT / "runs"
ACTIVE_PROMPTS = RUNS / "active_prompts.json"
PROVIDERS = ("serphouse", "dataforseo", "fake")


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


class ProviderError(Exception):
    """The chosen SERP provider can't be built (unknown name, device or missing credentials)."""


def resolve_devices(args, settings) -> List[str]:
    """--device (repeatable) wins over `devices` in settings.yaml; duplicates are dropped."""
    devices = args.device or settings.get("devices") or ["mobile"]
    if isinstance(devices, str):
        devices = [devices]
    devices = list(dict.fromkeys(str(d).lower() for d in devices))
    bad = [d for d in devices if d not in DEVICES]
    if bad:
        raise ProviderError(f"unknown device(s) {', '.join(bad)} in settings.yaml devices; "
                            f"use {', '.join(DEVICES)}")
    return devices


def make_provider(args, budget: CallBudget, device: str = "mobile"):
    name = (args.provider or os.environ.get("SERP_PROVIDER") or "serphouse").lower()
    try:
        provider = get_provider(name, budget, device=device)
    except ValueError as exc:
        raise ProviderError(f"SERP provider {name!r}: {exc}") from None
    log.info("SERP provider: %s (%s)", provider.name, device)
    return provider


def make_drafter(settings) -> Optional[LLMDrafter]:
    chain = build_chain(settings)
    if not chain.options:
        log.warning("no LLM provider configured (check OPENROUTER_KEY / settings.yaml llm.chain); "
                    "skipping LLM drafting")
        return None
    log.info("LLM chain: %s", " -> ".join(o.label for o in chain.options))
    return LLMDrafter(chain)


def cmd_select(args, settings) -> int:
    if args.source != "gsc" and not args.blogs:
        log.error("--blogs is required with --source %s", args.source)
        return 2
    clusters = load_clusters()
    rules = load_brand_rules()
    gsc = load_gsc(Path(args.gsc), clusters)
    log.info("source: %s; %d GSC queries", args.source, len(gsc))

    # GSC first, so when both sources produce the same search the GSC version is the one kept
    candidates = []
    if args.source in ("gsc", "both"):
        candidates += gsc_candidates(gsc, clusters, rules, float(settings.get("dedupe_threshold", 90)))
    if args.source in ("llm", "both"):
        blogs = [b for b in load_blogs(Path(args.blogs), clusters) if b.cluster is not None]
        log.info("%d blogs in active clusters", len(blogs))
        if args.limit:
            blogs = blogs[:args.limit]
            log.info("--limit %d: drafting for the first %d blogs only", args.limit, len(blogs))
        drafted = []
        drafter = make_drafter(settings)
        if drafter is not None:
            drafted = draft_candidates(blogs, clusters, drafter, int(settings.get("candidates_per_blog", 4)))
        tag_brands(drafted, rules)
        log.info("%d LLM candidates drafted", len(drafted))
        if not drafted and args.source == "both":
            log.warning("no LLM candidates; continuing with GSC candidates only")
        candidates += drafted

    budget = CallBudget(args.max_serp_calls or int(settings["max_serp_calls"]))
    # probes only ask "is an AI Overview shown?", so one device is enough: the first configured
    provider = None if args.no_probe else make_provider(args, budget, resolve_devices(args, settings)[0])
    decisions = select_prompts(candidates, gsc, clusters, provider, settings)
    kept = [d for d in decisions if d.status == ACTIVE]
    log.info("kept %d of %d candidates; %d probe SERP calls", len(kept), len(decisions), budget.used)

    RUNS.mkdir(exist_ok=True)
    ACTIVE_PROMPTS.write_text(json.dumps(
        [{"prompt": d.prompt, "cluster": d.candidate.cluster, "source": d.candidate.source} for d in kept],
        indent=2))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    make_writer(args.dry_run, stamp).write_prompts([prompt_row(d) for d in decisions])
    return 0


def run_checks(prompts: List[dict], provider, when: datetime, raw_dir: Optional[Path] = None):
    """One check per prompt on the provider's device. Stops with a warning if the budget runs out."""
    rows = []
    device = provider.device
    for i, p in enumerate(prompts):
        try:
            result = provider.fetch_ai_overview(p["prompt"])
        except BudgetExceeded:
            log.warning("SERP budget used up after %d of %d prompts (%s)", i, len(prompts), device)
            break
        if raw_dir is not None and result.raw is not None:
            (raw_dir / f"{device}-{i:03d}.json").write_text(
                json.dumps({"query": p["prompt"], "device": device, "body": result.raw}, indent=2))
        # organic rank is independent of the AI Overview, so match every non-failed check
        m = match_citations(result) if result.status != STATUS_FAILED else None
        rows.append(check_row(when, p["prompt"], p["cluster"], result, m, device=device))
    return rows


def cmd_check(args, settings) -> int:
    if not ACTIVE_PROMPTS.exists():
        log.error("no %s; run `select` first", ACTIVE_PROMPTS)
        return 1
    prompts = json.loads(ACTIVE_PROMPTS.read_text())
    devices = resolve_devices(args, settings)
    budget = CallBudget(args.max_serp_calls or int(settings["max_serp_calls"]))
    planned = len(prompts) * len(devices)
    if planned > budget.max_calls:
        log.warning("%d checks (%d prompts x %d devices) but budget is %d calls; later checks will be skipped",
                    planned, len(prompts), len(devices), budget.max_calls)
    providers = [make_provider(args, budget, d) for d in devices]
    now = datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    raw_dir = None
    if args.save_raw:
        raw_dir = RUNS / f"raw-{stamp}"
        raw_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for provider in providers:
        if budget.remaining <= 0:
            log.warning("SERP budget used up; skipping %s", provider.device)
            continue
        rows += run_checks(prompts, provider, now, raw_dir)
    writer = make_writer(args.dry_run, stamp)
    writer.append_checks(rows)
    writer.write_summary(summarise(writer.read_checks()))
    col = CHECK_HEADERS.index("check status")
    counts = {s: sum(r[col] == s for r in rows) for s in ("ok", "incomplete", "no_aio", "failed")}
    log.info("%d checks %s; %d SERP calls used", len(rows), counts, budget.used)
    return 0


def cmd_probe(args, settings) -> int:
    provider = make_provider(args, CallBudget(3), resolve_devices(args, settings)[0])
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
    p.add_argument("--provider", choices=PROVIDERS, help="override SERP_PROVIDER")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="draft, filter and score prompts")
    s.add_argument("--blogs", help="CSV: title, primary_keyword, url (needed unless --source gsc)")
    s.add_argument("--gsc", required=True, help="CSV: query, impressions (or raw GSC export)")
    s.add_argument("--source", choices=("gsc", "llm", "both"), default="both",
                   help="candidate sources: real GSC queries, LLM drafts per blog, or both (default)")
    s.add_argument("--no-probe", action="store_true", help="skip probe SERP calls")
    s.add_argument("--limit", type=int, help="draft for only the first N blogs in active clusters (quick tests)")

    c = sub.add_parser("check", help="check active prompts once")
    c.add_argument("--save-raw", action="store_true", help="save raw SERP JSON under runs/")

    for sp in (s, c):
        sp.add_argument("--dry-run", action="store_true", help="write CSVs under runs/, not Sheets")
        sp.add_argument("--max-serp-calls", type=int, help="cost guard; overrides settings.yaml")

    pr = sub.add_parser("probe", help="fetch one query and print the parsed result")
    pr.add_argument("query")
    pr.add_argument("--save", help="write the raw response JSON to this path")

    # also accepted after the command name; SUPPRESS keeps a value given before it
    for sp in (s, c, pr):
        sp.add_argument("--provider", choices=PROVIDERS, default=argparse.SUPPRESS,
                        help="SERP provider for this run; overrides SERP_PROVIDER")
        sp.add_argument("--device", action="append", choices=DEVICES,
                        help="device for this run, repeatable (check runs each); overrides `devices` in "
                             "settings.yaml. select probes and probe use the first")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    settings = load_settings()
    try:
        return {"select": cmd_select, "check": cmd_check, "probe": cmd_probe}[args.cmd](args, settings)
    except ProviderError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
