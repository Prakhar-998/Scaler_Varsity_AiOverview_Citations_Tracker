# Changelog

## [Unreleased]

### Added
- Checks record every Varsity/InterviewBit/Scaler page cited, with positions (`our pages` column,
  last), not only the first blog and first other brand page.
- Dashboard: a **Scaler.com (sister brand)** section with the same columns as the wins scoreboard.
  Wins now count only the Varsity blog and other InterviewBit pages; an AI Overview citing both the
  blog and Scaler shows in both.
- Dashboard: a **wins scoreboard** right after the KPIs, headed "Cited for X of N tracked
  prompts": one row per active prompt any of our pages was cited for in any run, with the page,
  best position, cited in X of Y runs, last cited, and a "lost in latest run" tag. It replaces the
  latest-check-only Wins section; the prompts table gains the same "cited in" column.
- Dashboard: any-page citations split into Varsity blog, other InterviewBit and Scaler (KPI note,
  cluster tooltip), and page types name the site ("Scaler program page").
- Checks record the first other InterviewBit/Scaler page cited (`brand url`, `brand position`),
  and the Summary has an `any page citation rate %`.
- Dashboard: a second citation KPI, "Varsity / InterviewBit cited — any page", in the KPIs, the
  cluster chart and the prompts table, with the cited URL and its page type (blog, program page,
  other page).
- Checks record the prompt set they ran on (`prompt set` column, last): `set-<hash>` for the
  active set, `file:<name>` for `check --prompts`.
- Dashboard: a **By blog** section (cited Varsity pages with the prompts citing each and their best
  position, plus mapped blogs never cited) and an "other runs" note listing excluded runs.
- Intent groups in selection: gen ai/genai/generative ai, best/top, course/courses and
  fees/fee/cost/price are treated as the same and word order is ignored; at most
  `max_per_intent_group` (2) prompts per group are kept, in the GSC source and in select's dedupe.

- `report` command: a self-contained static dashboard at `dashboard/index.html`. It has headline
  KPIs, per-cluster rates, gaps (sorted by intent and impressions, mapped to a Varsity blog),
  wins, top competing domains, a sortable and filterable all-prompts table, and a per-date trend.
  It uses plain SVG and makes no network calls, in light and dark mode. `report --sample` renders
  fake data for screenshots.
- `runs/cost_log.csv`: one row per `check` / `probe` / `select` run, with the estimated and billed
  USD (DataForSEO reports each call's cost).
- `select` writes drafted LLM candidates to `runs/<run>/candidates.csv` per blog as they are
  drafted, before any probe SERP call.
- `select --prompts <csv>` loads candidates from a file (`prompt`, `cluster`; optional `source`,
  `intent`; source defaults to `manual`) without drafting. It combines with any `--source`, and
  `--source none` uses only the file. `--gsc` is optional with `--source none`.
- `check --prompts <csv>` checks a prompt file directly, without reading or writing
  `runs/active_prompts.json`.
- `check --max-cost <usd>` aborts with exit code 3 before any SERP call if the estimate is over.
- `scripts/daily_run.sh`: select, then check if at least 10 prompts are active. It logs to
  `runs/logs/<date>.log` and caps check cost at $0.40. The README documents a one-shot LaunchAgent
  for 05:45.
- `devices` setting (default `[desktop]`) and a repeatable `--device desktop|mobile` flag. `check`
  runs every prompt once per device. Checks gets a `device` column after `cluster`, and the Summary
  is broken down by device.
- `check` logs the estimated cost (checks × `cost_per_check_usd`, $0.004 for DataForSEO) before it
  starts.
- Per-command default SERP provider (`serp_provider` in `settings.yaml`): DataForSEO for `check`
  and `probe`, SERPHouse for `select` probes.
- DataForSEO SERP adapter (`SERP_PROVIDER=dataforseo`): `POST /v3/serp/google/organic/live/advanced`
  for India (`location_code` 2356), English, mobile, with `load_async_ai_overview` so AI Overviews
  that load late come back with their sources. Credentials come from `DATAFORSEO_LOGIN` /
  `DATAFORSEO_PASSWORD`. SERPHouse is still the default.
- `--provider serphouse|dataforseo|fake` is accepted after the command name for `probe`, `select`
  and `check` (as well as before it), and the provider in use is logged. Missing credentials exit
  with a clear message.
- `--source gsc|llm|both` for `select` (default `both`). The GSC source uses real queries in an
  active cluster with a BOFU term. It drops navigational and pure own-brand queries, keeps our
  brand compared with a competitor (tagged `brand_comparison`), and tags competitor-brand
  queries `competitor`. It needs no LLM. The Prompts tab gets `source` and `brand tag` columns.
- Provider-agnostic LLM layer with a fallback chain in `settings.yaml`. The default is OpenRouter
  free models (`OPENROUTER_KEY` / `OPENROUTER_API_KEY`, `OPENROUTER_MODELS`); Gemini is optional
  and off. Each call has a 60s timeout and up to 2 retries on 429/5xx/529, and if every model
  fails `select` carries on with GSC candidates only.
- A per-blog progress log, e.g. `drafting 12/51 via openrouter:<model>: <title> -> 4 candidates`.
- `--limit N` drafts for only the first N blogs in active clusters.
- Brand rules in `config/programs.yaml`: `brand_terms` and `competitors`.
- Google redirect links (`/url?q=`, `/goto?url=`) are decoded during URL normalisation when the
  destination is readable.
- `rank match` column in Checks, right after `organic rank`: `url` for an exact Varsity URL match, `domain` when only the
  interviewbit.com domain was visible (opaque mobile `goto` links, which can't be decoded).
- `incomplete` check status: an AI Overview was shown, but its sources did not load. These
  checks are retried once (the retry counts toward the SERP call cap) and are kept out of the
  citation rate.
- `incomplete` count and `incomplete rate %` columns in the Summary.
- SERP provider adapter interface with a SERPHouse adapter (google.com, India, English, mobile),
  a fake provider, retries, and a per-run SERP call budget.
- URL normalisation (www, scheme, trailing slash, query/UTM, fragments, AMP variants) and
  citation matching: Varsity cited + position, brand cited (other interviewbit.com/scaler.com),
  competing domains, best organic rank.
- Cluster config (`config/programs.yaml`), settings, and CSV loaders for blogs and GSC queries.
- Prompt layer: Gemini drafts BOFU searches. A code-only filter keeps a search if it matches a GSC
  query or a probe shows an AI Overview. Kept searches are scored, near-duplicates removed, top N
  kept, and every candidate is logged with a reason.
- Prompts / Checks / Summary output to Google Sheets, or CSVs with `--dry-run`.
- `select`, `check` and `probe` commands.
- Offline test suite.

### Changed
- Dashboard history is per prompt: checks of prompts still active count from every real run, so a
  change to the active set no longer discards earlier runs. Trend points use the prompts each run
  checked.
- `check --max-cost` is a hard cap that includes retries (it limits SERP calls to cap ÷ rate), and
  the cost log's estimate counts every call made instead of the planned checks.
- `select --prompts <csv> --no-probe` keeps every prompt from the file (reason "curated, not
  probed") instead of dropping those with no GSC match; dedupe and intent grouping still apply.
- `select` logs each probe as `probe n/total: <prompt>`.
- Dashboard numbers (except spend) use only checks of the active prompt set; test runs and earlier
  prompt sets no longer count.
- Dashboard trend has one point per check run instead of per date.
- Organic rank shows "not in top 10" instead of a blank, with a footnote that the desktop match is
  domain-level.
- `SERP_PROVIDER` is now optional. When set, it overrides the per-command defaults for every
  command, with a warning. It is commented out in `.env.example`.
- The hand-written DataForSEO fixtures were replaced with real mobile and desktop captures.
- `select` probes no longer retry incomplete AI Overviews. An incomplete result already proves an
  AI Overview is shown, so the retry only spent a SERP call. `check` still retries once.
- Gemini is no longer the default drafter, and a Gemini 404 no longer stops `select`; the chain
  moves to the next model.
- Intent patterns now cover the GSC BOFU terms (salary, training, reviews, which, near me, and so on).
- `--blogs` is optional with `--source gsc`.
- Default Gemini model is now `gemini-3.8-flash`; `gemini-2.5-flash` is no longer available to new
  users. Set it with `gemini_model` in `config/settings.yaml` or the `GEMINI_MODEL` env var
  (the env var wins).
- The parser reads the confirmed SERPHouse shape, `results.results.ai_overview.references[]`
  (`link`, `source`, `title`); the guessed field names are gone. HTML entities in titles are decoded.
- The hand-written SERPHouse fixtures were replaced with real captures in `tests/fixtures/serphouse/`.
- AI Overview presence % now counts incomplete AI Overviews as detected.

### Fixed
- Dashboard: past checks match active prompts by normalised text (case, spaces, trailing
  punctuation), so the pilot run's "...forward deployed engineering." check now counts.
- DataForSEO tasks that fail with a transient status (40101 "Internal SE Server Error", 5xxxx) are
  retried once after 5s instead of being recorded as failed (5 of 37 checks on 2026-11-10).
- SERPHouse requests time out after 30s per attempt (was 60s) and each failed attempt is logged,
  so a bad network no longer hangs `select` silently.
- DataForSEO organic results: `url` is a `google.com/goto` redirect on both devices, so the site now
  comes from `breadcrumb` (the `domain` field is always google.com).
- Opaque `google.com/goto` AI Overview references are no longer reported as a "google.com"
  competing domain.
- A DataForSEO response with no tasks now includes the envelope's `status_code` and
  `status_message` in the error.
- Whitespace and newlines inside GSC queries are collapsed.
- Tests no longer read the real `.env`, so they can't reach the network with live keys.
- If the Gemini model returns 404, `select` now exits with a clear message naming the model and
  how to change it, instead of a traceback.
- Organic rank is now recorded on `no_aio` and `incomplete` checks, not only `ok` ones.
- SERPHouse requests now always send `tbs: ""`. SERPHouse added Google Verbatim (`tbs=li:1`) by
  default, which suppressed AI Overviews.
- SERPHouse requests use `POST /serp/live` with a JSON `{"data": {...}}` body. The GET `gl`/`hl`
  format returned `check_html_manually` errors.
