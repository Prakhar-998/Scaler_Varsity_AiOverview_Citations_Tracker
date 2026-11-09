# Decisions

## Real SERP data over Gemini emulation
We need to know what Google actually shows to a searcher in India. An LLM asked to "act like
an AI Overview" gives plausible answers that have no link to Google's real citations. So
An LLM is used only to *draft* candidate searches. Every citation we report comes from a real
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
The LLM over-generates. Code decides what survives, and only on evidence: a close match to a
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

## Organic rank on mobile: URL match where possible, domain match otherwise
On mobile, most organic `link`s SERPHouse returns are `google.com/goto?url=...` redirects. In the
2026-11-06 captures, 15 of the 16 organic results in raw5 and raw8 were redirects. We checked
whether `url=` holds the URL-encoded destination. It does not. Every value is a base64
protobuf (`08 01 12 …`) wrapping about 150 bytes of opaque, apparently encrypted data with no
readable URL. It can't be decoded.

**Rule:**
- During normalisation, Google redirects are unwrapped whenever the destination is readable
  (`/url?q=https…`, or `/goto?url=https…`). Those links get a URL-level match.
- If the destination is opaque, the rank falls back to the domain in `displayed_link`
  (e.g. `https://www.interviewbit.com`). That is a domain-level match. It could be any
  interviewbit.com page, not necessarily a Varsity post.
- The Checks tab records `rank match` = `url` or `domain` in the column right after `organic rank`. An exact URL match
  wins over a domain match, even a higher-ranked one.
- Social results (LinkedIn, Reddit) show text such as "740+ reactions" instead of a site URL.
  They get no domain and never match.
- No other provider is called for this. Organic rank is a secondary metric; AI Overview
  reference links are real URLs and are not affected.

Organic rank is now recorded on every non-failed check, including `no_aio` and `incomplete`,
because it does not depend on the AI Overview.

## GSC-grounded candidates, with the LLM for gap discovery
Real GSC queries are now a candidate source of their own (`--source gsc`). It needs no LLM
and always works. A query becomes a candidate when it matches an active cluster's
`match_terms` and contains a BOFU term (best, fees, vs, worth, salary, training, near me, and
so on). Then:
- Pure own-brand queries (Scaler, InterviewBit, Varsity) are dropped. That covers navigational
  searches ("scaler login", "interviewbit varsity") and our brand alone ("scaler fde course
  reviews"). They tell us about brand demand, not about whether we reach people who don't know us
  yet, and Google answers them with our own pages anyway.
- Own brand *compared with* someone else is kept and tagged `brand_comparison`. That means our
  brand together with a competitor, or with a comparison word: vs, versus, compare, better than,
  alternatives, difference between. "scaler vs fde academy" is a buyer choosing between us and a
  rival, and whose pages the AI Overview cites there matters. "which" and a bare "or" don't count
  as comparison words. "which scaler program is best" chooses between our own programs.
- Competitor-brand queries ("fde academy course fees") are kept and tagged, because buyers
  comparing alternatives are exactly the audience we want.
- Queries are ranked by impressions, and near-duplicates are dropped in favour of the
  higher-impression one.

The LLM still drafts searches per blog (`--source llm`), but its job changes. GSC only shows
searches where we already get impressions. The LLM's value is finding BOFU searches we *don't*
appear for: the gaps. With `--source both` (the default), GSC candidates go first, so when the
two overlap the GSC version, with its real demand, is kept and noted "also from llm". LLM
candidates with no GSC match still have to show an AI Overview on a probe to survive.

On the real FDE + GenAI export (2026-11-09), the GSC source alone gave 172 candidates and filled
the top 50 (8 competitor, 1 brand_comparison) with no LLM or SERP calls. Along the way, 6 pure-brand
and 52 near-duplicate queries were dropped. Two-word GSC queries are allowed ("fde salary"). LLM drafts still need at
least 3 words.

## LLM provider fallback after the Gemini outages
In November 2026, a direct call to `gemini-3.8-flash` hung for 60s with no response, and other
automations were getting 529s on 3.8 and 3.6 Flash. A demo can't depend on one provider. So:
- **Provider-agnostic layer** (`tracker/llm/`): one `complete(prompt)` interface, with an
  OpenRouter client (OpenAI-compatible) and a Gemini client.
- **Fallback chain** set in `settings.yaml` (`llm.chain`). The default is OpenRouter free models
  only; Gemini is off by default.
  - The models are hosted by different providers, so one outage can't take out all of them:
    `nvidia/nemotron-3.5-super-vl:free` (Nvidia), `xingchen-agi/xing4.0-29b-a4b:free` (Phala),
    `google/gemma-4-31b-it:free` (Google AI Studio, placed last).
  - We skipped the reasoning and code-tuned free models (slow or off-task) and the tiny 1.7–2.6B
    ones (more risk of junk). Override the list with `OPENROUTER_MODELS`.
- **Bounded waits**:
  - Every call has a 60s timeout. A 429/5xx/529 gets at most 2 retries with exponential backoff
    (2s, then 4s), then the chain moves on.
  - A timeout or any other error moves on straight away.
  - A model that fails is skipped for the rest of the run, so a dead model costs at most one
    timeout, not one per blog.
- **Never crash**: when every option has failed, drafting stops with a warning and `select`
  carries on with GSC candidates. A Gemini 404 is now a warning that moves the chain on, not a
  fatal error.
