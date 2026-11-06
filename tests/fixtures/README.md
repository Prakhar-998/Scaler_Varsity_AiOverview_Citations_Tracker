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
