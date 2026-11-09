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
`get_provider`; matching, the prompt layer and output stay the same (this is how the
DataForSEO adapter was added; see below). The adapter also returns
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

## DataForSEO adapter, and no incomplete retry on probes
**Found (2026-11-09):** SERPHouse returned incomplete AI Overviews (title only, no sources) for
15 of 15 commercial queries probed in one `select` run. That is far past the ~15% switch rule
above, so SERPHouse alone can't give a citation rate for BOFU searches.

**Changes:**
- **No incomplete retry during `select` probes.** A probe only asks "is an AI Overview shown?",
  and an incomplete result already answers yes. With nearly every probe incomplete, the retry was
  doubling probe cost for nothing. `fetch_ai_overview(query, retry_incomplete=False)` skips it;
  `check` keeps the one retry, because there the sources are the point.
- **DataForSEO adapter** (`tracker/checks/dataforseo.py`): `POST
  /v3/serp/google/organic/live/advanced`, India (`location_code` 2356), English, mobile, with
  `load_async_ai_overview: true` so DataForSEO waits for late-loading AI Overviews. References
  are the `ai_overview` item's `references[]` (`url`, `domain`, `title`) in order, then any extra
  ones nested in its elements. Organic results carry real URLs, so organic rank is a URL-level
  match. An `ai_overview` item with no references is still `incomplete`, so the two providers'
  numbers are comparable.
- **SERPHouse stays an option**, and the provider is chosen per command (`SERP_PROVIDER` or
  `--provider`). A sensible split is SERPHouse for cheap `select` probes, where incomplete is
  fine, and DataForSEO for `check`.

The first DataForSEO fixtures were hand-written from the docs. The real captures (below) replaced them.

## DataForSEO for checks, desktop first (2026-11-09)
**Found:** the real captures (`tests/fixtures/dataforseo/`, same query on mobile and desktop) came
back complete: 6 sources each, where SERPHouse had returned only the AI Overview title. Three
details differed from the docs:
- **Organic `url`s are `google.com/goto` redirects on desktop too**, with `domain: google.com`. The
  real site is only in `breadcrumb`, so organic rank is a domain-level match (`rank match = domain`)
  on both providers and both devices.
- **The sources list isn't every source.** On desktop, `references[]` had 3 entries, and the
  per-section `items[].references[]` cited 3 more. We record the list first, then the extra inline
  ones, deduped by URL, so a Varsity page cited only inline still counts.
- **An OK envelope can have no tasks.** One call returned `status_code` 20000 "Ok." with
  `tasks: []` and cost 0. It is recorded as `failed`, and the error names the envelope status.

**Decisions:**
- **Provider per command.** `check` and `probe` default to DataForSEO, because they need the
  sources. `select` probes default to SERPHouse, because they only need "is an AI Overview shown?",
  and an incomplete result answers that.
- **Desktop first.** `devices: [desktop]`. Desktop references carry real URLs, so Varsity
  matching works. Mobile is a list entry away (`[mobile, desktop]`), but see the next entry.
- **Budget.** About $1 of credit at $0.004 per call is about 250 checks: 50 prompts × 1 device × 2
  runs a day uses $0.40 a day. `check` logs the estimate before it starts, and `max_serp_calls`
  still caps every run.
- **Summary by device.** Mobile and desktop AI Overviews differ for the same query, so rates are
  never pooled across devices within a cluster. Only the ALL / ALL row combines them.

## Mobile references: source/title matching is the V1 follow-up
On mobile, 4 of the 6 DataForSEO references were `google.com/goto?url=CAES…` links: the same
opaque protobuf as SERPHouse's mobile organic links, with `domain: google.com`. Each one still has
a usable `source` ("AmbitionBox", "Arivu Skills") and page `title`.

**Not built in the prototype.** Matching on these isn't small:
- `varsity_cited` would mean matching the reference `title` against Varsity blog titles. `check`
  doesn't load `blogs.csv` today, and titles get rewritten in the AI Overview, so it needs fuzzy
  matching and a review of false positives.
- `brand_cited` needs a `source` → brand map ("InterviewBit", "Scaler"), and Varsity posts show
  under the InterviewBit source too, so a source match alone can't tell Varsity from other brand pages.
- The same page can appear twice, once as a real URL and once as a `goto` link (Arivu Skills,
  Glassdoor in the mobile capture). Deduping by source + title would change citation positions.
- Competing "domains" would be source names, not domains.

**For now:** opaque references keep their position in the list, so a later Varsity reference still
gets its real position. They are left out of competing domains rather than counted as
"google.com". Mobile checks would undercount Varsity citations, which is why desktop is the default.
**V1:** match opaque references by title against `blogs.csv` (rapidfuzz, with a threshold tuned on
real captures) and by `source` for brand. Record the match level as `url` / `title` / `source`,
the same way `rank match` does.

## Candidates saved before probing; manual prompt import
**Why:** LLM drafting is the scarce step. The OpenRouter free quota runs out mid-run and resets
at 05:30 IST. Probes come after drafting, and a run can fail or be stopped there (a SERP budget
hit, a provider error, Ctrl-C). Before this change, the drafts only lived in memory until the
Prompts tab was written at the very end, so any stop after drafting threw away a day's quota.

**Decisions:**
- **Save as drafted.** Each blog's drafts are appended to `runs/<run>/candidates.csv` the moment
  they come back, before any probe. A crash mid-drafting keeps every blog drafted so far.
  Only LLM drafts are saved. GSC candidates are rebuilt from the export for free, so
  `--source gsc --prompts candidates.csv` reproduces `--source both` without the LLM.
- **One file format** for saved candidates and hand-made prompts: `prompt, cluster`, plus optional
  `source` and `intent`. Manual prompts are tagged `source = manual`, so they stay visible in the
  Prompts tab. They get no free pass: dedupe, the GSC-or-probe filter and scoring apply exactly
  as for drafted ones, so a manual prompt Google shows no AI Overview for is still dropped.
- **`check --prompts`** checks a file without reading or writing `runs/active_prompts.json`.
  Test runs ("do 5 checks work end to end?") then can't overwrite the real prompt set.
- **`check --max-cost`** turns the logged estimate into a hard stop. The daily script uses $0.40
  (50 prompts × 1 device × $0.004 = $0.20, with headroom for retries) against a ~$1 credit.

**End-to-end test (2026-11-09):** 5 prompts from `data/private/test_prompts.csv`, DataForSEO
desktop:
- 4 `ok` (all 4 FDE prompts) and 1 `no_aio` ("best generative ai course in india").
- Varsity was cited once, at position 5, for "fde course fees"
  (`/varsity/blog/forward-deployed-engineer-course-fees-in-india/`), with a domain-level organic
  rank of 6.
- Billed $0.012 against a $0.02 estimate. The estimate is an upper bound.
- `runs/active_prompts.json` was unchanged.
