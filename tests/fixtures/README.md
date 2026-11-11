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

## `dataforseo/` — real DataForSEO responses (google.com, India, English, `load_async_ai_overview`)

| File | Query | What it shows |
|---|---|---|
| `aio_fde_salary_india_desktop.json` | forward deployed engineer salary in india | Desktop. Full AI Overview: 3 `references[]` in the sources list, plus 3 more sources cited only inline (`items[].references[]`). Organic `url`s are `google.com/goto` redirects; the site is in `breadcrumb` |
| `aio_fde_salary_india_mobile.json` | forward deployed engineer salary in india | Mobile, same query. 6 references, 4 of them `google.com/goto` links with `domain: google.com` but a usable `source` and `title` |
| `aio_fde_learning_paths_desktop.json` | help me evaluate the best online learning paths for forward deployed engineering | Desktop, from the 2026-11-11 13:26 check run (`--save-raw`). 15 references, 2 of them hidden `google.com/goto` links; one is labelled `source: "Scaler"` with no readable URL. No Varsity blog cited |
| `no_tasks_ok_envelope.json` | forward deployed engineer salary in india | Envelope `status_code` 20000 "Ok." but `tasks: []` and cost 0 |

Captured 2026-11-09. Responses contain no credentials. To add one:
`python -m tracker probe --provider dataforseo "<query>" --save tests/fixtures/dataforseo/<name>.json`.
