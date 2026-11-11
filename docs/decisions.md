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

## A static dashboard, generated from the run outputs
**What:** `python -m tracker report` writes one HTML file with the data embedded. There is no
server, no build step, and no network calls: plain SVG charts, no chart library from a CDN.
- **Why static:** it's a prototype demo. One file opens anywhere, can be attached or shared, and
  can't break because a CDN or an API is down. Re-running `report` after each `check` is the
  refresh.
- **Why it reads CSVs, not Sheets:** Sheets aren't set up yet, and every run already writes
  CSVs under `runs/`. The builder takes plain rows, so reading the Sheets tabs later is a new
  loader, not a new dashboard.

**Definitions:** the same as the Summary tab. Failed checks are left out of every rate, and
incomplete AI Overviews count toward presence but not the citation rate. Gaps and wins use the
*latest* check per prompt and device, so one bad run doesn't hide a win, and a failed check never
hides the last good result. Headline rates use all checks.

**Gap priority:** buying intent first (fees > best > comparison/worth > audience > course >
salary), then GSC impressions. A high-intent query with little demand is still nearer a sale
than a high-demand salary query. Each gap names the Varsity blog to improve: the blog it was
drafted from, otherwise the closest title or primary-keyword match in the same cluster
(rapidfuzz `token_set_ratio` ≥ 60), otherwise "no matching blog: new content?".

**Cost:** check rows don't record the provider, so cost comes from `runs/cost_log.csv`. Every run
appends a row, and DataForSEO's per-call `cost` field gives the billed amount. Runs from before
the log are listed with their logged estimate and shown as "estimated" on the dashboard.

**Data safety:** the real dashboard embeds GSC queries and results, so `dashboard/` is
gitignored. `report --sample` builds a fake dataset (`.example` competitor domains) through the
same builder, for portfolio screenshots.

**Known limit (fixed 2026-11-10, see below):** test runs landed in `runs/` like real ones and
were mixed into the headline rates and the trend.

## Dashboard counts only the active prompt set; intent groups in selection (2026-11-10)

**Prompt-set id on every check.** `check` tags each row with the set it ran on: `set-<8 hex>` (a
hash of the sorted, lowercased active prompts) or `file:<name>` for `check --prompts`. The
dashboard computes the id of `runs/active_prompts.json` and counts only matching checks in the
KPIs, clusters, gaps, wins, by-blog, domains, table and trend. Everything else is listed in an
"other runs" note, so a test run or an old prompt set can't move the citation rate.
- **Why a hash, not the select run stamp:** it needs no new file or format change, and it can be
  recomputed from any prompt list, which is how untagged checks from before this change are
  handled: an untagged run counts as the active set only if it checked exactly the active
  prompts. On 2026-11-10 that keeps the 50-check daily run and excludes the three 5-prompt test
  runs. A run cut short by the SERP budget would also be excluded; acceptable for old data.
- **Why `file:` for prompt files:** a test file could hold the same prompts as the active set;
  check --prompts is a test by definition, so it never counts.
- **Spend is the exception:** the cost KPI still covers every run, because that money was spent.

**Trend per check run, not per date.** Checks run twice a day at different times, and the two runs
are the point of the comparison. A run is one check timestamp (all devices).

**By blog.** Cited Varsity pages are grouped by normalised URL (UTMs, `www`, trailing slash
ignored), with each prompt's best position. "Mapped blogs never cited" uses the same blog mapping
as the gaps, so it shows which existing posts have tracked demand but no citations yet.

**Organic rank: "not in top 10".** A blank read as missing data. A successful check with no
Varsity result means none on page one (DataForSEO's default depth), so it says so; failed or
unchecked rows stay blank. The footnote says the desktop match is domain-level: organic links come
back as opaque `google.com` redirects, so any interviewbit.com result counts, and it may not be
the mapped blog.

**Intent groups for selection.** The 2026-11-10 active set spent 9 of 50 slots on variants of the
same search ("fde academy fees/cost/price", "fde.academy fees"; "best/top gen ai/genai/generative
ai courses"). Fuzzy matching at 90 doesn't catch synonyms, and lowering the threshold would merge
genuinely different searches. Instead a canonical key folds only the listed synonyms
(gen ai/genai/generative ai, best/top, course/courses, fees/fee/cost/price), drops punctuation and
sorts the words; at most 2 prompts per key are kept, highest impressions (GSC) or score (select)
first. Two, not one, because the wording variants can show different AI Overviews and that's worth
seeing. Fuzzy matching still applies across groups. It runs in the GSC source and in select's
dedupe before probing, so dropped variants never cost a probe. It takes effect at the next
`select`; the current active set is unchanged.

## Curated prompt files with --no-probe (2026-11-10)

`select --prompts <csv> --no-probe` used to drop every prompt with no GSC match ("no GSC match;
probing disabled"), so a hand-picked list mostly vanished. Without a probe there is no evidence to
filter on, and the person who wrote the list is the evidence, so the file is now kept as-is with
reason "curated, not probed". Dedupe and intent grouping still apply, because duplicates waste
check credits whoever picked them. Curated prompts take their `keep_top` slots first, then GSC or
probed candidates fill the rest by score. With probing on, file prompts are filtered as before.

**SERPHouse timeout:** 30s per attempt, up to 3 attempts, each failure logged. The worst case per
probe is about 1.5 minutes with a warning every 30s, not minutes of silence.

## Retry transient DataForSEO task errors; --max-cost includes retries (2026-11-11)

DataForSEO answered 5 of 37 checks on 2026-11-10 with HTTP 200 but task status 40101 "Internal SE
Server Error". The HTTP-level retry didn't see it, because the request itself succeeded, so each one
became a `failed` check. These are Google-side fetch errors, not bad input, so the adapter now
retries a task once after 5s when its status is 40101 or a 5xxxx internal error. Other 4xxxx codes
(invalid field, auth, balance) won't change on a retry and fail at once. One retry, not more:
a second 40101 for the same query in the same minute is likely to repeat, and each retry is billed.
The retry also applies to `probe` and to DataForSEO select probes, since a failed probe loses the
candidate just the same.

**Cost cap:** retries are billed, so `--max-cost` counts them. Before the run it still compares
the planned checks with the cap (exit 3 if over). Then it caps SERP calls at cap ÷ rate, so retries
and the late-load retry draw from the same pool, and spend can never pass the cap. If they use it
up, the remaining checks are skipped with a warning rather than overspending. The cost log's
estimate is now calls made × rate, retries included.

## History per prompt, not per set; a second "any page" citation KPI (2026-11-11)

**Per prompt.** Counting only checks tagged with the current set threw history away whenever the
set changed: the 2026-11-10 select went from 50 to 37 prompts, and the 15:49 run's checks of the 18
prompts that stayed disappeared. Now a check counts when its prompt is active now and its run was
a real run. Real means tagged `set-...`, or untagged but checking exactly a set some `select`
produced (every prompts.csv under runs/ is read for this). Test runs (`file:...`, or an untagged
list no select produced) never count. On today's data: 92 checks (74 from today, 18 from 11-10
15:49), and 47 left out (15 test checks, 32 checks of removed prompts). The trend is the
exception: each run's point uses all the prompts that run checked, so a past point shows what that
run measured. Prompt counts per point are in the tooltip, because a changing set shifts the rate.

**Two citation KPIs.** "Varsity blog cited" (/varsity/blog/) stays the main metric: it's what the
blog team can act on. "Varsity / InterviewBit cited — any page" adds program pages and any other
interviewbit.com / scaler.com page, because an AI Overview citing the FDE program page is still a
win for the business. Checks now record the first such page and its position (`brand url`,
`brand position`). Page type is a heuristic: "program page" when the URL path contains course,
program or academy, otherwise "other page", and the URL is always shown next to it. Checks from
before this change know a brand page was cited but not which, shown as "page not recorded".

## Wins scoreboard over every run; prompts matched by normalised text (2026-11-11)

**Normalised matching.** History was matched to active prompts by exact text (only trimmed and
lowercased). The pilot run checked "help me evaluate the best online learning paths for forward
deployed engineering." with a full stop; the active prompt has none, so its #9 learning-path blog
citation was dropped. Matching now lowercases, trims, collapses spaces and strips trailing
punctuation (`. , ; : ! ? …`); checks take the active spelling so every section groups them as one
prompt. Plural or reworded prompts stay separate on purpose: that's intent grouping's job at
selection, not history's. Effect on today's data: one check changed, blog cited 4 → 5 of 123.

**Scoreboard, not latest check.** A latest-check-only Wins list hides consistency: a prompt cited
in 3 of 4 runs and one cited once look the same, and a citation lost in the latest run simply
disappears. The scoreboard keeps one row per prompt cited in any run, sorted by share of runs
cited, then best position, and tags "lost in latest run". A win is any of our pages (blog, other
InterviewBit, Scaler), matching the any-page KPI; the blog-only count is in the note. Y counts runs
that got an answer (ok or no AI Overview): failed and incomplete checks say nothing about
citations. Devices are folded per run: a run is cited if any device was.

**InterviewBit vs Scaler.** The any-page rate mixes three different owners (blog team, InterviewBit
pages, Scaler pages), so the KPI note and cluster tooltip split it. Only the first brand page per
check is recorded, so a check citing both the blog and a Scaler page counts as blog.

## Wins are Varsity + InterviewBit; Scaler gets its own board; every page recorded (2026-11-11)

**Regression check first.** All three blog wins were lost in the 13:26 run, the first after the
retry and page-recording changes. The saved raw responses (--save-raw) for "fde engineer course
fee", "help me evaluate the best online learning paths for forward deployed engineering" and
"forward deployed engineer course for working professionals" contain no /varsity/blog/ URL,
either in `references[]` or nested in `items[].references[]` / `items[].links[]`. Several
references are opaque `google.com/goto` links (the token is encrypted), but their `source` labels
are NDTV, Quality Thought, Codebasics, YouTube, Coding Blocks, IIT Roorkee Futurense and Scaler,
never InterviewBit, which is the label Varsity references carry. Across every saved raw response the
parser misses no InterviewBit/Scaler URL. So the losses are real changes in Google's sources, not a
parser regression, and the parser was not changed.

**Scaler is a sister brand, not a win.** The blog team acts on Varsity and InterviewBit pages; a
Scaler page being cited is good for the group but isn't their result, so counting it as a win
inflated the headline. Scaler citations get a separate board with the same columns. The any-page
KPI keeps its definition (blog + InterviewBit + Scaler) and its note splits it by site.

**Every page, not the first.** `brand url` held only the first non-blog page, so an AI Overview
citing the blog and a Scaler page, or two of our pages, lost the rest. `our pages` records all of
them with positions, so a double citation shows on both boards. Older rows fall back to `cited url`
+ `brand url`; the oldest ones know only that a brand page was cited, so those runs count on
neither board (they'd otherwise be guessed). Opaque goto references labelled "Scaler" (one in the
13:26 learning-paths answer) weren't counted at the time; see the next section.

## Hidden links count by label or title; any page excludes Scaler (2026-11-11)

**Hidden links.** Some AI Overview references are `google.com/goto` links with an encrypted
target; only `source` (the site label) and `title` are readable. Ignoring them under-counted us:
the 13:26 learning-paths answer cites a Scaler page that way. Now a hidden link labelled
InterviewBit or Scaler is one of our pages with match type `label`, shown as "InterviewBit page
(URL hidden)" or "Scaler page (URL hidden)". It counts toward the any-page KPI (InterviewBit) or
the Scaler section, never the blog KPI, because the label can't say which page it is.

**Blog by title, InterviewBit labels only.** A hidden InterviewBit link counts as a Varsity blog
when its title matches a blog title in data/private/blogs.csv (match type `title`, the blog URL
recorded). Calibrated on the saved raw responses: Google rewrites titles, and the real fees blog
scored 89.5 (token_set_ratio) against its own title, while other InterviewBit pages scored under
60, so the threshold is 85. Scaler-labelled links are never title-matched: Scaler's "AI Engineer
Roadmap 2026" scored 89.8 against the GenAI roadmap blog and would have been a false blog citation.
Site suffixes ("- InterviewBit") and trailing ellipses are stripped; titles under 3 words never match.

**Any page excludes Scaler.** The any-page KPI now means the Varsity blog or another InterviewBit
page, the same set as the wins scoreboard, so the two numbers agree. On today's data it went from
13.8% (17 of 123) to 4.9% (6 of 123): 3 Scaler citations moved out, and 8 old checks that
recorded only "a brand page was cited" can't be attributed to either site, so they no longer
count. The Summary's `any page citation rate %` follows the same definition.

**Past runs aren't re-matched.** Checks are matched when they run; the 13:26 checks were written
before hidden links were counted, so the dashboard won't show that run's hidden Scaler link.
Re-matching a run from its saved raw responses would be a separate, explicit step.

