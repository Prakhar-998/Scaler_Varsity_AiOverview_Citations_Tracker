# Varsity AI Overview Citation Tracker

Tracks how often Google AI Overviews cite Varsity blog pages (`interviewbit.com/varsity/blog/`)
for high-intent (BOFU) searches in India. It uses real Google results from a SERP API, not an
LLM pretending to be Google.

**Status: prototype.** The aim is to prove two things: that AI Overview citations can be fetched and
parsed reliably for Google India, and that an automatic prompt layer can choose useful searches
without junk.

## How it works

```
blogs.csv + gsc.csv + config/programs.yaml
        │
        ▼
 select ── candidates from two sources (--source gsc | llm | both, default both):
             gsc: real GSC queries in an active cluster with a BOFU term (no LLM, always works)
             llm: ~4 BOFU searches drafted per blog, to find searches we don't appear for yet
        ── code filter: keep only if it matches a real GSC query OR one probe shows an AI Overview
        ── merge, dedupe across sources, score (intent, GSC demand, AIO presence, program weight),
           keep top 50
        ── writes the Prompts tab + runs/active_prompts.json
        │
        ▼
 check  ── one SERP call per active prompt (google.com, India, English, mobile)
        ── match references: Varsity cited? position? other brand pages? competitors?
        ── appends to the Checks tab, rebuilds the Summary tab
```

Run `check` twice a day at different times. Each check gets one status:

| Status | Meaning | In citation rate? |
|---|---|---|
| `ok` | AI Overview shown, sources parsed | yes |
| `incomplete` | AI Overview shown, but its sources loaded too late to capture (`check` retries once; `select` probes don't) | no |
| `no_aio` | SERP loaded, no AI Overview | n/a |
| `failed` | request or parse failed | no (left out of every rate) |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # fill in keys; never commit .env
```

| Variable | Purpose |
|---|---|
| `SERP_PROVIDER` | optional; forces one provider (`serphouse`, `dataforseo`, `fake`) for every command. Leave unset to use the per-command defaults |
| `SERPHOUSE_API_KEY` | SERPHouse API key |
| `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | DataForSEO API login and password (from the DataForSEO dashboard) |
| `OPENROUTER_KEY` | OpenRouter key, the default LLM for drafting (`OPENROUTER_API_KEY` also works) |
| `OPENROUTER_MODELS` | optional; comma-separated `:free` models tried in order (overrides `config/settings.yaml`) |
| `GEMINI_API_KEY` | only if you turn Gemini on in `llm.chain` (off by default) |
| `GEMINI_MODEL` | optional; overrides `gemini_model` in `config/settings.yaml` (default `gemini-3.8-flash`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | path to the service account file, or the JSON itself |
| `SHEET_ID` | the **test** sheet (share it with the service account email) |

Inputs:
- `blogs.csv` has the columns `title, primary_keyword, url`
- `gsc.csv` has `query, impressions`; a raw GSC "Queries" export also works

Samples are in `data/sample/`. Keep real exports in `data/private/`, which is gitignored.

### SERP providers and devices
Both adapters fetch google.com, India, English, for the configured device, and return the same statuses.
- **dataforseo** (default for `check` and `probe`): `POST /v3/serp/google/organic/live/advanced`
  with `location_code` 2356 and `load_async_ai_overview: true`, which returns complete AI Overviews.
  About $0.004 per call; `check` logs the estimated cost (checks × rate) before it starts.
  A task that fails with a transient status (40101 "Internal SE Server Error", or a 5xxxx
  internal error) is retried once after 5s; the retry is a billed call.
- **serphouse** (default for `select` probes): `POST /serp/live`. For most commercial queries it
  returns the AI Overview without its sources (`incomplete`): 15 of 15 in one `select` run. That is
  enough for a probe, which only asks whether an AI Overview is shown.

The per-command defaults are `serp_provider` in `config/settings.yaml`. `--provider` (before or
after the command name) overrides them, then `SERP_PROVIDER`. The provider and device in use are
logged at the start of every run.

Devices come from `devices` in `config/settings.yaml` (default `[desktop]`; set `[mobile, desktop]`
to check both). `--device desktop|mobile` overrides it for one run and can be repeated. `check` runs
every prompt once per device; `select` probes and `probe` use the first device. Mobile citations
are only partly matched for now (see "Mobile references" in `docs/decisions.md`).

### Candidate sources
- **gsc**: queries from `--gsc` that match an active cluster's `match_terms` and contain a BOFU
  term (best, top, course(s), fee(s), cost, price, vs, versus, worth, certification, certificate,
  salary, training, program(me), institute, online, review(s), compare, which, near me).
  - Own-brand queries (`brand_terms` in `config/programs.yaml`: Scaler, InterviewBit, Varsity)
    are kept only as comparisons. If one also names a competitor or has a comparison word (vs,
    versus, compare, better than, alternatives, difference between), it's tagged
    `brand_comparison`, e.g. "scaler vs fde academy". Navigational or pure-brand queries
    ("scaler login", "scaler fde course reviews", "interviewbit varsity") are dropped.
  - Competitor-brand queries (`competitors`) are kept and tagged `competitor`.
  - Ranked by impressions; near-duplicates are dropped. Searches that differ only in synonyms,
    plurals or word order form one intent group (gen ai/genai/generative ai, best/top,
    course/courses and fees/fee/cost/price are treated as the same), and at most
    `max_per_intent_group` (2) per group are kept. The same rule applies in select's dedupe
    across all sources, before any probe.
- **llm**: an LLM drafts BOFU searches per blog, mainly to find searches we don't appear for in GSC.
  Models are tried in order from `llm.chain` in `config/settings.yaml`:
  - The default chain is OpenRouter free models only. Gemini is available but off.
  - Each call has a 60s timeout and up to 2 retries on 429/5xx/529, then moves to the next model.
    A model that fails is skipped for the rest of the run.
  - If every model fails, `select` logs a warning and carries on with GSC candidates only.
- **both** (default): GSC candidates go first, so a search found by both sources keeps its GSC
  version and is noted "also from llm".
- **none**: only the `--prompts` file.

`--prompts <csv>` adds candidates from a file, with no drafting. It needs the columns `prompt` and
`cluster`; `source` and `intent` are optional, and `source` defaults to `manual`. It works for a
saved `candidates.csv` or a hand-made list. These candidates still go through dedupe, the probe
filter and scoring, and the flag combines with any `--source`.

With `--no-probe`, the file is treated as a curated list: every prompt is kept as-is (reason
"curated, not probed"), even without a GSC match. They still go through dedupe and intent
grouping, and they take their `keep_top` slots first.

Each probe logs `probe 12/37: <prompt>`. SERPHouse requests time out after 30s per attempt (up to
3 attempts) and every failed attempt is logged, so a bad network can't hang `select` silently.

Drafted LLM candidates are written to `runs/<run>/candidates.csv` per blog as they are drafted,
before any probe SERP call. If a run stops, re-use them without drafting again:
`select --source gsc --prompts runs/<run>/candidates.csv ...` gives the same result as `both`.

## Run

```bash
# 1. pick prompts (spends SERP calls only on probes for candidates with no GSC match)
.venv/bin/python -m tracker select --blogs data/private/blogs.csv --gsc data/private/gsc.csv --dry-run

#    GSC only: no LLM, no key needed. Add --no-probe to make no SERP calls either.
.venv/bin/python -m tracker select --source gsc --gsc data/private/gsc.csv --dry-run

#    quick LLM test on the first 3 blogs in active clusters
.venv/bin/python -m tracker select --source llm --limit 3 --no-probe \
    --blogs data/private/blogs.csv --gsc data/private/gsc.csv --dry-run

# 2. check them (twice a day); --save-raw keeps the JSON for fixtures
.venv/bin/python -m tracker check --dry-run --save-raw

# spot-check one query against what you see on Google
.venv/bin/python -m tracker probe "best generative ai course in india" --save /tmp/raw.json

# check both devices this run, or probe through SERPHouse instead of DataForSEO
.venv/bin/python -m tracker check --dry-run --device desktop --device mobile
.venv/bin/python -m tracker probe --provider serphouse "forward deployed engineer salary in india"

# end-to-end test on a small prompt file; runs/active_prompts.json is left untouched
.venv/bin/python -m tracker check --prompts data/private/test_prompts.csv --dry-run --max-cost 0.05
```

Without `--dry-run`, output goes to Google Sheets. With it, CSVs are written to `runs/dry-run-*/`.
`--max-serp-calls` caps SERP calls per run (default in `config/settings.yaml`; retries count).
`check --max-cost <usd>` exits with code 3 before any SERP call if the estimate is over the limit.
Otherwise it becomes a hard cap that includes retries: $0.40 at $0.004 per call allows 100 calls,
so 50 checks leave room for 50 retries. If retries use up the cap, the remaining checks are
skipped with a warning. The cost log's estimate counts every call made, retries included.

## Dashboard

```bash
.venv/bin/python -m tracker report            # dashboard/index.html from runs/
.venv/bin/python -m tracker report --sample   # dashboard/sample.html, fake data for screenshots
```

`report` reads `runs/active_prompts.json`, the latest `prompts.csv`, every `checks.csv` under
`runs/`, `runs/cost_log.csv`, and `data/private/blogs.csv` (to map gaps to a blog). Every number
except spend uses only checks of the **active prompt set**: each check row records the set it ran on
(`prompt set`), and test runs (`check --prompts`, tagged `file:<name>`) and earlier prompt sets are
left out and listed in a small "other runs" note. It writes **one self-contained HTML file**:
the data is embedded, the charts are plain SVG, and it makes no network calls. Open it in a
browser, and re-run `report` after each `check` to refresh it. Light and dark mode follow the
OS, with a toggle.

Sections:
1. **Headline numbers:** prompts tracked, checks run, AI Overview presence %, Varsity citation
   rate %, incomplete rate %, last check, and DataForSEO cost so far. Cost covers every run: the
   billed amount where a run logged it, otherwise the run's estimate.
2. **By cluster:** AI Overview presence and citation rate per cluster.
3. **Gaps (the action list):** an AI Overview with sources that doesn't cite Varsity, using the
   latest check per prompt and device. Sorted by buying intent, then GSC impressions. Each row
   shows who is cited instead, our organic rank, and the Varsity blog to improve (the blog the
   prompt was drafted from, or the closest title/keyword match in the cluster). Organic rank is
   the best Varsity result in the top 10, or "not in top 10". On desktop it's a domain-level
   match (any interviewbit.com result), because the organic links are opaque Google redirects.
4. **Wins:** Varsity cited, with URL and position.
   **By blog:** each Varsity page cited, the prompts citing it and its best position; then the
   blogs tracked prompts map to that were never cited.
5. **Top competing domains:** the share of the latest AI Overviews that cite each domain.
6. **All prompts:** sortable, filterable by cluster, device and status.
7. **Trend:** citation rate per check run (one point per run timestamp). It shows "needs 2+
   check runs" until there are two.

`dashboard/` is gitignored, because the real dashboard embeds GSC queries and results. Only
the generator (`tracker/report/`) is committed. The `--sample` data is invented: its competitor
domains use the reserved `.example` TLD.

Every `check`, `probe` and `select` run appends a row to `runs/cost_log.csv` (provider, devices,
checks, SERP calls, estimated and billed USD). DataForSEO reports what it billed for each call.

## Scheduled daily run

`scripts/daily_run.sh` runs `select --source both` (SERPHouse probes) and then, only if at least
10 prompts are active, `check` on DataForSEO, as dry runs.
- **Logs:** `runs/logs/<date>.log`.
- **Stops:** on the first error, with a non-zero exit code.
- **Cost cap:** `check` aborts if its cost estimate is over $0.40.
- **Exit codes:** 0 ok, 3 over the cost cap, 4 fewer than 10 active prompts, anything else a
  failed step.

Providers are passed explicitly, so a `SERP_PROVIDER` line in `.env` can't swap them.

It is scheduled with a LaunchAgent that is not in the repo:
`~/Library/LaunchAgents/com.prakhar.citationtracker.plist`. It runs the script at 05:45 local
time, after the OpenRouter free quota resets at 05:30 IST, and writes stdout/stderr to
`runs/logs/launchd.log`. It is **one-shot**: the plist sets `CITATION_TRACKER_ONE_SHOT=1`, and the
script then disables and unloads the agent when it finishes, so it doesn't repeat (even after a
reboot) until it is loaded again.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.prakhar.citationtracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/prakharjaiswal/Citation_Tracker/scripts/daily_run.sh</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/prakharjaiswal/Citation_Tracker</string>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>45</integer></dict>
    <key>EnvironmentVariables</key>
    <dict><key>CITATION_TRACKER_ONE_SHOT</key><string>1</string></dict>
    <key>StandardOutPath</key><string>/Users/prakharjaiswal/Citation_Tracker/runs/logs/launchd.log</string>
    <key>StandardErrorPath</key><string>/Users/prakharjaiswal/Citation_Tracker/runs/logs/launchd.log</string>
    <key>RunAtLoad</key><false/>
</dict>
</plist>
```

```bash
mkdir -p runs/logs                      # launchd won't create the log folder
# load (also re-arms it after a one-shot run, which disables it)
launchctl enable gui/$(id -u)/com.prakhar.citationtracker
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.prakhar.citationtracker.plist
# check it's loaded
launchctl list | grep citationtracker   # "-  0  com.prakhar.citationtracker" = loaded, not running
# unload
launchctl bootout gui/$(id -u)/com.prakhar.citationtracker
```

For the run to happen:
- You must be **logged in**. A LaunchAgent runs in your user session.
- The Mac must **not be shut down**.
- If it is **asleep** at 05:45, launchd runs the job when the Mac next wakes, not at 05:45.
- Keep it **plugged in**: a laptop on battery with the lid closed may not wake for long.

To wake it just before the run (one-off, for tomorrow):

```bash
sudo pmset schedule wake "11/10/2026 05:40:00"
pmset -g sched                          # confirm; cancel with: sudo pmset schedule cancelall
```

## Output

| Tab | Columns |
|---|---|
| Prompts | prompt, cluster, source (gsc/llm), brand tag (competitor / brand_comparison), intent, score, status (active/dropped), reason, source inputs |
| Checks | date, time, prompt, cluster, device, ai overview shown, varsity cited, cited url, position, brand cited, organic rank, rank match (`url` = exact Varsity URL, `domain` = only interviewbit.com was visible), competing domains, check status, error |
| Summary | per cluster and device, ALL per device, and ALL / ALL when more than one device ran: checks, ok, incomplete, no_aio, failed, AI Overview presence %, Varsity citation rate %, incomplete rate %, brand citation rate % |

Sample `Checks` rows, built from the real DataForSEO and SERPHouse fixtures:

```
date,time,prompt,cluster,device,ai overview shown,varsity cited,cited url,position,brand cited,organic rank,rank match,competing domains,check status,error
2026-11-09,17:12,forward deployed engineer salary in india,fde,desktop,yes,no,,,no,,,"linkedin.com, fde.academy, ambitionbox.com, buildfastwithai.com, secondtalent.com, nareshit.com",ok,
2026-11-09,17:08,forward deployed engineer salary in india,fde,mobile,yes,no,,,no,,,"arivuskills.com, glassdoor.co.in",ok,
2026-11-06,17:08,forward deployed engineer salary in india,fde,mobile,yes,,,,,,,,incomplete,AI Overview sources did not load
2026-11-05,18:19,best forward deployed engineer course in india,fde,mobile,no,,,,,,,,no_aio,
```

The first two rows are DataForSEO and the last two SERPHouse. SERPHouse missed the sources for
the same query. The mobile DataForSEO row lists only 2 competing domains because 4 of its 6
references are opaque `google.com/goto` links.

## Tests

```bash
.venv/bin/pytest
```

All tests run offline, using fake providers and fake HTTP sessions. The SERPHouse tests use real
captured responses in `tests/fixtures/serphouse/`. The DataForSEO tests use real
captures in `tests/fixtures/dataforseo/` (see the README in that folder).

## Layout

```
tracker/checks/   SERP adapter interface, SERPHouse and DataForSEO adapters, fake provider, call budget
tracker/match/    URL normalisation, Varsity / brand / competitor matching
tracker/prompts/  Gemini drafting, filtering, scoring, dedupe
tracker/output/   row building, summary, Sheets and CSV writers
tracker/config/   YAML + CSV loaders
tracker/report/   dashboard data builder, HTML template, sample data
config/           programs.yaml (clusters), settings.yaml
docs/decisions.md key choices and why
```
