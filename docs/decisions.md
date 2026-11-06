# Decisions

## Real SERP data over Gemini emulation
We need to know what Google actually shows to a searcher in India. An LLM asked to "act like
an AI Overview" gives plausible answers that have no link to Google's real citations. So
Gemini is used only to *draft* candidate searches. Every citation we report comes from a real
SERP fetch.

## One provider adapter
All SERP access goes through `SerpProvider.fetch_ai_overview(query) -> SerpResult` in
`tracker/checks/`. SERPHouse is the prototype provider because its free trial covers our
call volume. To move to DataForSEO, add `tracker/checks/dataforseo.py` and one line in
`get_provider`; matching, the prompt layer and output stay the same. The adapter also returns
`ok` / `no_aio` / `failed` itself, so "no AI Overview" can never be confused with "the call
broke".

The SERPHouse response shape was confirmed from real captures (2026-11-06). The parser now reads
only `results.results.ai_overview.references[]` (`link`, `source`, `title`). The order of the
references is the citation position. Those captures are the fixtures in `tests/fixtures/serphouse/`.
Request details (POST body, `tbs`) stay inside the SERPHouse adapter.

## Prompt layer filtered by code, not by the model
Gemini over-generates. Code decides what survives, and only on evidence: a close match to a
real GSC query with impressions (rapidfuzz `token_sort_ratio`, which penalises extra words so
"x" does not match "x fees"), or one probe SERP showing an AI Overview. Near-duplicates are
removed *before* probing so we never pay for them. Each candidate is logged with keep/drop and a
reason, so junk can be reviewed and the rules tuned.

Score = 0.35 intent + 0.30 demand (log-scaled GSC impressions) + 0.25 AIO on probe + 0.10
program weight. These weights are a starting point to tune after the junk review.

## Citation rate over raw counts
Raw citation counts grow with the number of prompts, so they say little. We report:
- **AI Overview presence %** = AIO shown (ok + incomplete) / successful checks
- **Varsity citation rate %** = Varsity cited / `ok` checks (AIO shown with sources)
- **Incomplete rate %** = incomplete / AIO shown (see the entry on AI Overviews that load late below)

Failed checks are counted but kept out of every denominator. Brand citations
(other interviewbit.com and scaler.com pages) are reported separately from Varsity.

## Cost guard
The `CallBudget` counts every HTTP attempt, retries included, and stops the run at
`max_serp_calls`. Expected prototype use: probes for unmatched candidates, plus about 50 prompts × 2
checks a day. That is well under the 4,000 trial credits.

## Clear SERPHouse's default Verbatim mode (`tbs=li:1`)
**Found:** during real testing (2026-11-06), queries that showed an AI Overview on a phone came
back from SERPHouse without one. The response's `search_parameters` showed that SERPHouse had
added `tbs=li:1` to the Google URL on its own. That is Google's Verbatim mode, and it suppresses
AI Overviews. Sending `"tbs": ""` in the request data removes it, and the AI Overview then
comes back.

**Fix:** the adapter always sends `tbs: ""`, and a test fails if it is ever missing from the
request body. We also use the `POST /serp/live` format with a JSON `{"data": {...}}` body. The
GET format with `gl`/`hl` from SERPHouse's AI Overview docs returned
`{"status": "error", "error": "...check_html_manually"}`, so we don't use it.

## AI Overviews that load late are recorded as `incomplete`
Sometimes SERPHouse returns `ai_overview` as only `{"title": "AI Overview"}`, with no contents
or references. Google showed an AI Overview, but its sources loaded after SERPHouse captured
the page. We saw this on both mobile and desktop for the same query. Calling it `no_aio` would
understate AI Overview presence. Calling it "not cited" would understate the citation rate. So
it gets its own status.

**Policy:**
- On `incomplete`, the adapter retries once, about 5 seconds later. The retry counts toward
  `max_serp_calls`, and it is skipped if the budget is used up.
- If the retry is still incomplete, the check is recorded as `incomplete`.
- Incomplete checks count as AI Overviews detected (presence %), but they are kept out of the
  citation rate, the same way failed checks are.
- The Summary reports **incomplete rate %** = incomplete / AI Overviews detected.

**Switch rule:** if the incomplete rate stays above about 15% during the trial, the citation
numbers rest on too small and possibly biased a share of AI Overviews. In that case we move to
DataForSEO, whose `load_async_ai_overview` option waits for AI Overviews that load late. That is
one new adapter file (see "One provider adapter").
