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
 select ── Gemini drafts ~4 BOFU searches per blog
        ── code filter: keep only if it matches a real GSC query OR one probe shows an AI Overview
        ── score (intent, GSC demand, AIO presence, program weight), dedupe, keep top 50
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
| `incomplete` | AI Overview shown, but its sources loaded too late to capture (retried once) | no |
| `no_aio` | SERP loaded, no AI Overview | n/a |
| `failed` | request or parse failed | no (left out of every rate) |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # fill in keys; never commit .env
```

| Variable | Purpose |
|---|---|
| `SERP_PROVIDER` | `serphouse` (default) or `fake` |
| `SERPHOUSE_API_KEY` | SERPHouse API key |
| `GEMINI_API_KEY` | drafts candidate searches |
| `GEMINI_MODEL` | optional; overrides `gemini_model` in `config/settings.yaml` (default `gemini-3.8-flash`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | path to the service account file, or the JSON itself |
| `SHEET_ID` | the **test** sheet (share it with the service account email) |

Inputs:
- `blogs.csv` has the columns `title, primary_keyword, url`
- `gsc.csv` has `query, impressions`; a raw GSC "Queries" export also works

Samples are in `data/sample/`.

## Run

```bash
# 1. pick prompts (spends SERP calls only on probes for candidates with no GSC match)
.venv/bin/python -m tracker select --blogs data/blogs.csv --gsc data/gsc.csv --dry-run

# 2. check them (twice a day); --save-raw keeps the JSON for fixtures
.venv/bin/python -m tracker check --dry-run --save-raw

# spot-check one query against what you see on Google
.venv/bin/python -m tracker probe "best generative ai course in india" --save /tmp/raw.json
```

Without `--dry-run`, output goes to Google Sheets. With it, CSVs are written to `runs/dry-run-*/`.
`--max-serp-calls` caps SERP calls per run (default in `config/settings.yaml`; retries count).

## Output

| Tab | Columns |
|---|---|
| Prompts | prompt, cluster, intent, score, status (active/dropped), reason, source inputs |
| Checks | date, time, prompt, cluster, ai overview shown, varsity cited, cited url, position, brand cited, organic rank, rank match (`url` = exact Varsity URL, `domain` = only interviewbit.com was visible), competing domains, check status, error |
| Summary | per cluster and ALL: checks, ok, incomplete, no_aio, failed, AI Overview presence %, Varsity citation rate %, incomplete rate %, brand citation rate % |

Sample `Checks` rows, built from the real SERPHouse fixtures:

```
date,time,prompt,cluster,ai overview shown,varsity cited,cited url,position,brand cited,organic rank,rank match,competing domains,check status,error
2026-11-06,17:12,what is a forward deployed engineer,fde,yes,no,,,no,,,"linkedin.com, iit.edu, posthog.com, roadmap.sh, nareshit.com",ok,
2026-11-06,17:08,forward deployed engineer salary in india,fde,yes,,,,,,,,incomplete,AI Overview sources did not load
2026-11-05,18:19,best forward deployed engineer course in india,fde,no,,,,,,,,no_aio,
```

## Tests

```bash
.venv/bin/pytest
```

All tests run offline, using fake providers and fake HTTP sessions. The SERPHouse tests use real
captured responses in `tests/fixtures/serphouse/` (see the README in that folder).

## Layout

```
tracker/checks/   SERP adapter interface, SERPHouse adapter, fake provider, call budget
tracker/match/    URL normalisation, Varsity / brand / competitor matching
tracker/prompts/  Gemini drafting, filtering, scoring, dedupe
tracker/output/   row building, summary, Sheets and CSV writers
tracker/config/   YAML + CSV loaders
config/           programs.yaml (clusters), settings.yaml
docs/decisions.md key choices and why
```
