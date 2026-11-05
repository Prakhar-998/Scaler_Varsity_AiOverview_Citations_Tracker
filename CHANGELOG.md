# Changelog

## [Unreleased]

### Added
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
