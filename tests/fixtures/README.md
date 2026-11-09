# Fixtures

## `serphouse/` — real SERPHouse responses (google.com, India, English, mobile)

| File | Query | What it shows |
|---|---|---|
| `aio_what_is_fde_mobile.json` | what is a forward deployed engineer | Full AI Overview: `results.results.ai_overview` with `title`, `contents[]` and 5 `references[]` (`source`, `link`, `title`) |
| `incomplete_fde_salary_india_mobile.json` | forward deployed engineer salary in india | AI Overview that loads late: `ai_overview` is only `{"title": "AI Overview"}`, with no contents or references |
| `no_aio_best_fde_course_india.json` | best forward deployed engineer course in india | No AI Overview (checked on a phone) |

Captured 2026-11-05/06. The no-AIO capture was made with SERPHouse's default `tbs=li:1` (Verbatim) and
verified as no-AIO on a real phone; the other two were made with `tbs` cleared. Responses contain no API keys.
To add one, save it with `python -m tracker probe "<query>" --save tests/fixtures/serphouse/<name>.json`.

## `dataforseo/` — hand-written DataForSEO responses (google.com, India, English, mobile)

| File | Query | What it shows |
|---|---|---|
| `aio_fde_salary_india_mobile.json` | forward deployed engineer salary in india | `ai_overview` item with 3 top-level `references[]` (Varsity at position 3, with a UTM) and an element whose nested references add one more (palantir.com); Varsity organic at `rank_group` 2 |
| `no_aio_best_fde_course_india.json` | best forward deployed engineer course in india | Organic items only, no `ai_overview` |
| `task_error.json` | x | Envelope OK, task `status_code` 40501 |

These follow the shape in DataForSEO's v3 `serp/google/organic/live/advanced` docs; no DataForSEO
call has been made yet. Replace them with real captures on the first live run:
`python -m tracker probe --provider dataforseo "<query>" --save tests/fixtures/dataforseo/<name>.json`.
