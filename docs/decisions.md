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

**Open item:** SERPHouse's public docs do not give a schema for the AI Overview block, or say how
AI Overviews that load late are handled. The parser looks under a few likely keys and marks
an AI Overview with no references as `incomplete`. Next step: capture real responses with
`probe --save` / `check --save-raw`, add them as fixtures, narrow the parser to the real
keys, and confirm the late-load behaviour with SERPHouse support.

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
- **AI Overview presence %** = AIO shown / successful checks
- **Varsity citation rate %** = Varsity cited / checks where an AIO was shown

Failed checks are counted but kept out of both denominators. Brand citations
(other interviewbit.com and scaler.com pages) are reported separately from Varsity.

## Cost guard
The `CallBudget` counts every HTTP attempt, retries included, and stops the run at
`max_serp_calls`. Expected prototype use: probes for unmatched candidates, plus about 50 prompts × 2
checks a day. That is well under the 4,000 trial credits.
