# Changelog

## [Unreleased]

### Added
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
- Default Gemini model is now `gemini-3.8-flash`; `gemini-2.5-flash` is no longer available to new
  users. Set it with `gemini_model` in `config/settings.yaml` or the `GEMINI_MODEL` env var
  (the env var wins).
- The parser reads the confirmed SERPHouse shape, `results.results.ai_overview.references[]`
  (`link`, `source`, `title`); the guessed field names are gone. HTML entities in titles are decoded.
- The hand-written SERPHouse fixtures were replaced with real captures in `tests/fixtures/serphouse/`.
- AI Overview presence % now counts incomplete AI Overviews as detected.

### Fixed
- If the Gemini model returns 404, `select` now exits with a clear message naming the model and
  how to change it, instead of a traceback.
- Organic rank is now recorded on `no_aio` and `incomplete` checks, not only `ok` ones.
- SERPHouse requests now always send `tbs: ""`. SERPHouse added Google Verbatim (`tbs=li:1`) by
  default, which suppressed AI Overviews.
- SERPHouse requests use `POST /serp/live` with a JSON `{"data": {...}}` body. The GET `gl`/`hl`
  format returned `check_html_manually` errors.
