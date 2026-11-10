"""Command line entry point.

  python -m tracker select --blogs data/blogs.csv --gsc data/gsc.csv [--dry-run]
  python -m tracker check [--dry-run] [--save-raw]      # run twice a day at different times
  python -m tracker probe "best generative ai course in india"   # one SERP call, for spot checks
  python -m tracker report                                       # dashboard/index.html from runs/

Every command takes --provider serphouse|dataforseo|fake (default: SERP_PROVIDER, else
`serp_provider` in config/settings.yaml: dataforseo for check and probe, serphouse for select)
and --device desktop|mobile (repeatable; default: `devices` in config/settings.yaml).
"""
from __future__ import annotations

import argparse
import csv
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
from tracker.prompts import (ACTIVE, CandidateLog, LLMDrafter, draft_candidates, gsc_candidates, load_prompt_file,
                             prompt_file_set_id, prompt_set_id, select_prompts, tag_brands)

log = logging.getLogger("tracker")
RUNS = ROOT / "runs"
ACTIVE_PROMPTS = RUNS / "active_prompts.json"
COST_LOG = RUNS / "cost_log.csv"
COST_HEADERS = ["date", "time", "command", "provider", "devices", "checks", "serp calls", "estimated usd",
                "billed usd"]
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


def run_dir(dry_run: bool, stamp: str) -> Path:
    """Per-run folder under runs/: the dry-run CSVs and select's candidates.csv go here."""
    return RUNS / f"{'dry-run' if dry_run else 'run'}-{stamp}"


def make_writer(dry_run: bool, stamp: str):
    if dry_run:
        out = run_dir(dry_run, stamp)
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


def provider_name(args, settings) -> str:
    """--provider, then SERP_PROVIDER, then the per-command default in settings.yaml."""
    default = (settings.get("serp_provider") or {}).get(args.cmd) or "serphouse"
    if args.provider:
        return args.provider.lower()
    env = os.environ.get("SERP_PROVIDER", "").strip().lower()
    if env and env != default:
        log.warning("SERP_PROVIDER=%s overrides the %s default (%s); remove it from .env to use the "
                    "per-command defaults in settings.yaml", env, args.cmd, default)
    return env or default


def make_provider(args, settings, budget: CallBudget, device: str = "mobile"):
    name = provider_name(args, settings)
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
    if args.source in ("llm", "both") and not args.blogs:
        log.error("--blogs is required with --source %s", args.source)
        return 2
    if args.source == "none" and not args.prompts:
        log.error("--source none needs --prompts")
        return 2
    if args.source != "none" and not args.gsc:
        log.error("--gsc is required with --source %s", args.source)
        return 2
    clusters = load_clusters()
    rules = load_brand_rules()
    # with --source none, a GSC export is optional; it still adds GSC matches and demand to scoring
    gsc = load_gsc(Path(args.gsc), clusters) if args.gsc else []
    log.info("source: %s%s; %d GSC queries", args.source, f" + {args.prompts}" if args.prompts else "", len(gsc))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # GSC first, so when both sources produce the same search the GSC version is the one kept
    candidates = []
    if args.source in ("gsc", "both"):
        candidates += gsc_candidates(gsc, clusters, rules, float(settings.get("dedupe_threshold", 90)),
                                     int(settings.get("max_per_intent_group", 2)))
    if args.prompts:
        # a saved candidates.csv or a manual list: no drafting, but the same dedupe, probe filter and scoring
        loaded = load_prompt_file(Path(args.prompts), clusters)
        tag_brands(loaded, rules)
        if args.no_probe:  # curated list: keep every prompt as-is (still deduped and intent-grouped)
            for c in loaded:
                c.curated = True
        candidates += loaded
    if args.source in ("llm", "both"):
        blogs = [b for b in load_blogs(Path(args.blogs), clusters) if b.cluster is not None]
        log.info("%d blogs in active clusters", len(blogs))
        if args.limit:
            blogs = blogs[:args.limit]
            log.info("--limit %d: drafting for the first %d blogs only", args.limit, len(blogs))
        drafted = []
        drafter = make_drafter(settings)
        if drafter is not None:
            # saved per blog as drafted, before any probe SERP calls, so an interrupted run keeps them
            saved = CandidateLog(run_dir(args.dry_run, stamp) / "candidates.csv")
            log.info("saving drafted candidates to %s", saved.path)
            drafted = draft_candidates(blogs, clusters, drafter, int(settings.get("candidates_per_blog", 4)),
                                       on_drafted=saved.add)
        tag_brands(drafted, rules)
        log.info("%d LLM candidates drafted", len(drafted))
        if not drafted and args.source == "both":
            log.warning("no LLM candidates; continuing with GSC candidates only")
        candidates += drafted

    budget = CallBudget(args.max_serp_calls or int(settings["max_serp_calls"]))
    # probes only ask "is an AI Overview shown?", so one device is enough: the first configured
    provider = None if args.no_probe else make_provider(args, settings, budget, resolve_devices(args, settings)[0])
    decisions = select_prompts(candidates, gsc, clusters, provider, settings)
    kept = [d for d in decisions if d.status == ACTIVE]
    log.info("kept %d of %d candidates; %d probe SERP calls", len(kept), len(decisions), budget.used)
    if provider is not None:
        rate = (settings.get("cost_per_check_usd") or {}).get(provider.name)
        record_cost("select", [provider], budget.used, budget.used,
                    None if rate is None else float(rate) * budget.used)

    RUNS.mkdir(exist_ok=True)
    ACTIVE_PROMPTS.write_text(json.dumps(
        [{"prompt": d.prompt, "cluster": d.candidate.cluster, "source": d.candidate.source} for d in kept],
        indent=2))
    make_writer(args.dry_run, stamp).write_prompts([prompt_row(d) for d in decisions])
    return 0


def log_cost_estimate(provider: str, checks: int, settings) -> Optional[float]:
    """Log and return the estimated cost in USD, or None if the provider has no rate configured."""
    rate = (settings.get("cost_per_check_usd") or {}).get(provider)
    if rate is None:
        return None
    cost = checks * float(rate)
    log.info("estimated cost: %d checks x $%.3f = $%.2f on %s (retries cost extra)",
             checks, float(rate), cost, provider)
    return cost


def record_cost(command: str, providers: list, checks: int, calls: int, estimate: Optional[float],
                when: Optional[datetime] = None) -> None:
    """Append one row per run to runs/cost_log.csv. `billed usd` is what the provider reported
    (DataForSEO returns the cost of each call); blank when the provider doesn't report it."""
    if not providers or calls == 0:
        return
    when = when or datetime.now()
    spent = [p.spent_usd for p in providers if p.spent_usd is not None]
    billed = f"{sum(spent):.4f}" if spent else ""
    new = not COST_LOG.exists()
    COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(COST_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COST_HEADERS)
        w.writerow([when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), command, providers[0].name,
                    " ".join(p.device for p in providers), checks, calls,
                    "" if estimate is None else f"{estimate:.4f}", billed])
    if billed:
        log.info("billed: $%s on %s", billed, providers[0].name)


def run_checks(prompts: List[dict], provider, when: datetime, raw_dir: Optional[Path] = None, prompt_set: str = ""):
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
        rows.append(check_row(when, p["prompt"], p["cluster"], result, m, device=device,
                              prompt_set=prompt_set))
    return rows


def cmd_check(args, settings) -> int:
    if args.prompts:
        # read-only: checking a prompt file never touches active_prompts.json
        prompts = [{"prompt": c.prompt, "cluster": c.cluster, "source": c.source}
                   for c in load_prompt_file(Path(args.prompts), load_clusters())]
        prompt_set = prompt_file_set_id(Path(args.prompts))
    elif not ACTIVE_PROMPTS.exists():
        log.error("no %s; run `select` first, or pass --prompts", ACTIVE_PROMPTS)
        return 1
    else:
        prompts = json.loads(ACTIVE_PROMPTS.read_text())
        prompt_set = prompt_set_id(p["prompt"] for p in prompts)
    log.info("prompt set: %s (%d prompts)", prompt_set, len(prompts))
    devices = resolve_devices(args, settings)
    budget = CallBudget(args.max_serp_calls or int(settings["max_serp_calls"]))
    planned = len(prompts) * len(devices)
    providers = [make_provider(args, settings, budget, d) for d in devices]
    rate = (settings.get("cost_per_check_usd") or {}).get(providers[0].name)
    cost = log_cost_estimate(providers[0].name, planned, settings)
    if args.max_cost is not None and cost is not None and cost > args.max_cost:
        log.error("estimated cost $%.2f is over --max-cost $%.2f; no checks run", cost, args.max_cost)
        return 3
    if args.max_cost is not None and rate:
        # retries are billed calls too: the cap becomes a call limit, so they can never push spend past it
        cap_calls = int(args.max_cost / float(rate) + 1e-9)
        if cap_calls < budget.max_calls:
            budget.max_calls = cap_calls
            log.info("--max-cost $%.2f allows %d SERP calls: %d checks + up to %d retries",
                     args.max_cost, cap_calls, planned, max(cap_calls - planned, 0))
    if planned > budget.max_calls:
        log.warning("%d checks (%d prompts x %d devices) but budget is %d calls; later checks will be skipped",
                    planned, len(prompts), len(devices), budget.max_calls)
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
        rows += run_checks(prompts, provider, now, raw_dir, prompt_set)
    writer = make_writer(args.dry_run, stamp)
    writer.append_checks(rows)
    writer.write_summary(summarise(writer.read_checks()))
    col = CHECK_HEADERS.index("check status")
    counts = {s: sum(r[col] == s for r in rows) for s in ("ok", "incomplete", "no_aio", "failed")}
    log.info("%d checks %s; %d SERP calls used", len(rows), counts, budget.used)
    # the logged estimate covers every call made, retries included
    record_cost("check", providers, len(rows), budget.used, None if rate is None else float(rate) * budget.used)
    return 0


def cmd_probe(args, settings) -> int:
    budget = CallBudget(3)
    provider = make_provider(args, settings, budget, resolve_devices(args, settings)[0])
    result: SerpResult = provider.fetch_ai_overview(args.query)
    rate = (settings.get("cost_per_check_usd") or {}).get(provider.name)
    record_cost("probe", [provider], 1, budget.used, None if rate is None else float(rate) * budget.used)
    if args.save:
        Path(args.save).write_text(json.dumps(result.raw, indent=2))
    m = match_citations(result)
    print(f"status: {result.status}  shown: {result.shown}  error: {result.error or '-'}")
    for r in result.references:
        print(f"  [{r.position}] {r.url}")
    print(f"varsity cited: {m.varsity_cited} (pos {m.position})  brand cited: {m.brand_cited}  "
          f"organic rank: {m.organic_rank} ({m.rank_match or 'not found'})")
    return 0


DASHBOARD = ROOT / "dashboard"
DEFAULT_BLOGS = ROOT / "data/private/blogs.csv"


def cmd_report(args, settings) -> int:
    from tracker.report import build_report, load_runs, render

    clusters = load_clusters(active_only=False)
    if args.sample:
        from tracker.report.sample import sample_inputs

        inputs, blogs = sample_inputs()
        out = render(build_report(inputs, clusters, blogs, sample=True),
                     Path(args.out) if args.out else DASHBOARD / "sample.html")
        log.info("sample dashboard (fake data): %s", out)
        return 0
    blogs_path = Path(args.blogs) if args.blogs else DEFAULT_BLOGS
    blogs = load_blogs(blogs_path, clusters) if blogs_path.exists() else []
    if not blogs:
        log.warning("no blogs file at %s; gaps won't be mapped to a Varsity blog", blogs_path)
    inputs = load_runs(Path(args.runs) if args.runs else RUNS)
    if not inputs.checks:
        log.warning("no checks.csv under %s yet; the dashboard will be mostly empty", args.runs or RUNS)
    data = build_report(inputs, clusters, blogs)
    out = render(data, Path(args.out) if args.out else DASHBOARD / "index.html")
    log.info("dashboard: %s (%d checks, %d prompts, %d gaps, %d wins)", out, data["kpis"]["checks"],
             len(data["prompts"]), len(data["gaps"]), len(data["wins"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tracker", description="AI Overview citation tracker (Varsity)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--provider", choices=PROVIDERS, help="override SERP_PROVIDER")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="draft, filter and score prompts")
    s.add_argument("--blogs", help="CSV: title, primary_keyword, url (needed unless --source gsc)")
    s.add_argument("--gsc", help="CSV: query, impressions (or raw GSC export); optional with --source none")
    s.add_argument("--source", choices=("gsc", "llm", "both", "none"), default="both",
                   help="candidate sources: real GSC queries, LLM drafts per blog, both (default), or none "
                        "(only --prompts)")
    s.add_argument("--prompts", help="CSV of candidates to add (prompt, cluster; optional source, intent), e.g. a "
                                     "saved candidates.csv or manual prompts; combinable with --source")
    s.add_argument("--no-probe", action="store_true", help="skip probe SERP calls")
    s.add_argument("--limit", type=int, help="draft for only the first N blogs in active clusters (quick tests)")

    c = sub.add_parser("check", help="check active prompts once")
    c.add_argument("--save-raw", action="store_true", help="save raw SERP JSON under runs/")
    c.add_argument("--max-cost", type=float, help="abort before any SERP call if the estimated cost (USD) is "
                                                   "over this")
    c.add_argument("--prompts", help="CSV (prompt, cluster) to check instead of runs/active_prompts.json, "
                                     "which is left untouched")

    for sp in (s, c):
        sp.add_argument("--dry-run", action="store_true", help="write CSVs under runs/, not Sheets")
        sp.add_argument("--max-serp-calls", type=int, help="cost guard; overrides settings.yaml")

    pr = sub.add_parser("probe", help="fetch one query and print the parsed result")
    pr.add_argument("query")
    pr.add_argument("--save", help="write the raw response JSON to this path")

    rp = sub.add_parser("report", help="build the static dashboard from runs/")
    rp.add_argument("--out", help="output HTML (default: dashboard/index.html, or dashboard/sample.html)")
    rp.add_argument("--sample", action="store_true", help="render fake data, for screenshots; reads nothing")
    rp.add_argument("--runs", help="folder with select/check outputs (default: runs/)")
    rp.add_argument("--blogs", help="blogs CSV for mapping gaps to a Varsity blog (default: data/private/blogs.csv)")

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
        return {"select": cmd_select, "check": cmd_check, "probe": cmd_probe,
                "report": cmd_report}[args.cmd](args, settings)
    except ProviderError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
