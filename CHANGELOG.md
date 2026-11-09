# Changelog

## [Unreleased]

### Added
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
- Whitespace and newlines inside GSC queries are collapsed.
- Tests no longer read the real `.env`, so they can't reach the network with live keys.
- If the Gemini model returns 404, `select` now exits with a clear message naming the model and
  how to change it, instead of a traceback.
- Organic rank is now recorded on `no_aio` and `incomplete` checks, not only `ok` ones.
- SERPHouse requests now always send `tbs: ""`. SERPHouse added Google Verbatim (`tbs=li:1`) by
  default, which suppressed AI Overviews.
- SERPHouse requests use `POST /serp/live` with a JSON `{"data": {...}}` body. The GET `gl`/`hl`
  format returned `check_html_manually` errors.
