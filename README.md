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

### Candidate sources
- **gsc**: queries from `--gsc` that match an active cluster's `match_terms` and contain a BOFU
  term (best, top, course(s), fee(s), cost, price, vs, versus, worth, certification, certificate,
  salary, training, program(me), institute, online, review(s), compare, which, near me).
  - Queries naming our own brands (`brand_terms` in `config/programs.yaml`: Scaler,
    InterviewBit, Varsity) are dropped.
  - Competitor-brand queries (`competitors`) are kept and tagged `competitor = yes`.
  - Ranked by impressions; near-duplicates are dropped.
- **llm**: an LLM drafts BOFU searches per blog, mainly to find searches we don't appear for in GSC.
  Models are tried in order from `llm.chain` in `config/settings.yaml`:
  - The default chain is OpenRouter free models only. Gemini is available but off.
  - Each call has a 60s timeout and up to 2 retries on 429/5xx/529, then moves to the next model.
    A model that fails is skipped for the rest of the run.
  - If every model fails, `select` logs a warning and carries on with GSC candidates only.
- **both** (default): GSC candidates go first, so a search found by both sources keeps its GSC
  version and is noted "also from llm".

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
```

Without `--dry-run`, output goes to Google Sheets. With it, CSVs are written to `runs/dry-run-*/`.
`--max-serp-calls` caps SERP calls per run (default in `config/settings.yaml`; retries count).

## Output

| Tab | Columns |
|---|---|
| Prompts | prompt, cluster, source (gsc/llm), competitor, intent, score, status (active/dropped), reason, source inputs |
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
