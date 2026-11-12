# Setup: scheduled checks on GitHub Actions

The workflow in `.github/workflows/check.yml` runs every active prompt once on DataForSEO (desktop)
every Monday and Thursday at about 06:30 IST, and whenever you start it by hand. History lives in a
Google Sheet, so a fresh runner needs nothing from your machine:

| Tab | What | Written by |
|---|---|---|
| Prompts | the latest `select`'s decisions; `status = active` is the set `check` runs | `select`, `migrate` |
| Checks | one row per check, appended every run | `check`, `migrate` |
| Summary | rates per cluster and device over every check in the tab | `check`, `migrate` |
| Costs | one row per `select` / `check` / `probe` run, estimated and billed USD | every paid run, `migrate` |
| Blogs | the Varsity blogs (title, primary keyword, url), for title matching and gap mapping | `migrate` |

The tracker creates the tabs itself. Steps 1 to 4 are done once, on your machine.

## 1. Service account

Skip this if you already have one for the tracker.

1. In the [Google Cloud console](https://console.cloud.google.com/), pick or create a project.
2. **APIs & Services → Library:** enable the **Google Sheets API** and the **Google Drive API**.
3. **IAM & Admin → Service accounts → Create service account.** No roles are needed.
4. Open the account, **Keys → Add key → Create new key → JSON**. Keep the downloaded file outside
   the repo (for example `~/.config/citation-tracker/service_account.json`). Never commit it.
5. Note its `client_email` (`…@….iam.gserviceaccount.com`).

## 2. Create the Sheet and share it

1. Create a blank Google Sheet, for example "Varsity AIO Citation Tracker". Use a new test Sheet,
   not the Organic Tracker.
2. Copy its ID from the URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`.
3. **Share** → paste the service account's `client_email` → **Editor** → untick "Notify people"
   → Share.

## 3. Point your local setup at the Sheet

In `.env` (never committed):

```
SHEET_ID=<the ID from step 2>
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service_account.json
```

## 4. Import your existing history (one time)

```bash
.venv/bin/python -m tracker migrate --dry-run   # logs what it would import; writes nothing
.venv/bin/python -m tracker migrate
.venv/bin/python -m tracker report              # now reads the Sheet; compare with report --dry-run
```

`migrate` imports:
- **Prompts:** `runs/active_prompts.json` as the active set, with the latest `prompts.csv` decisions.
- **Checks:** every check of a real run. Test runs (`check --prompts`, or untagged runs of a list
  no `select` produced) are left out and listed. Older untagged runs are tagged with the set they
  checked.
- **Costs:** `runs/cost_log.csv`.
- **Blogs:** `data/private/blogs.csv`.

It is safe to run again: checks and cost rows already in the Sheet are skipped. The Prompts and
Blogs tabs are replaced.

The two dashboards (`report` and `report --dry-run`) should show the same numbers. The only
difference is the "other runs" note: test runs aren't in the Sheet, so they aren't listed.

## 5. GitHub secrets

In the repository: **Settings → Secrets and variables → Actions → New repository secret**. Add
each of these:

| Secret | Value |
|---|---|
| `DATAFORSEO_LOGIN` | DataForSEO API login (from the DataForSEO dashboard, not your site login) |
| `DATAFORSEO_PASSWORD` | DataForSEO API password |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **whole contents** of the service account JSON file, not its path |
| `SHEET_ID` | the Sheet ID from step 2 |
| `OPENROUTER_KEY` | optional, for a scheduled `select` later; the workflow doesn't use it yet |

With the GitHub CLI, from the repo folder:

```bash
gh secret set DATAFORSEO_LOGIN
gh secret set DATAFORSEO_PASSWORD
gh secret set GOOGLE_SERVICE_ACCOUNT_JSON < /path/to/service_account.json
gh secret set SHEET_ID
```

Secrets are masked in logs. The workflow also hashes prompt text in its log lines
(`TRACKER_REDACT_LOGS=1`) and never prints a SERP response.

## 6. The dashboard artifact and repository visibility

The dashboard embeds prompts, GSC queries and check results. A workflow artifact of a **public**
repository can be downloaded by anyone signed in to GitHub, so the workflow uploads
`dashboard/index.html` (kept 30 days) **only when the repository is private**. On a public
repository the run still checks and updates the Sheet, then shows a warning instead of the
artifact.

To get the artifact, either make this repository private (**Settings → General → Danger Zone →
Change visibility**), or run the workflow from a private copy of the repository. Without it,
build the dashboard locally from the Sheet with `.venv/bin/python -m tracker report`.

## 7. Trigger a manual run

1. **Actions → check → Run workflow →** branch `main` **→ Run workflow**.
2. Open the run. The steps are tests (offline), check, report and the upload. Check logs the
   estimated cost first (38 prompts × $0.004 = $0.15). `--max-cost 0.40` caps it, retries included.
3. When it finishes, the new rows are at the bottom of the Checks tab. On a private repository,
   **Artifacts → dashboard** downloads a zip with `index.html`.

The schedule is `0 1 * * 1,4` (01:00 UTC = 06:30 IST, Mon and Thu). GitHub can start scheduled
runs several minutes late. On a public repository, it disables schedules after 60 days without a
commit; **Actions → check → Enable workflow** turns it back on.

## Failures

A failed run shows a red ✗ in the Actions tab, and GitHub emails you by default (Settings →
Notifications → Actions). Exit codes from `check`:

| Code | Meaning |
|---|---|
| 1 | no active prompts in the Prompts tab (run `migrate`, or `select` without `--dry-run`) |
| 2 | the SERP provider couldn't start, e.g. a missing DataForSEO secret |
| 3 | the cost estimate is over `--max-cost`; no SERP call was made |
| 5 | every check failed (bad login, provider outage); the rows are in Checks with the error |

A Sheets error (Sheet not shared with the service account, wrong ID) fails the step with a
traceback naming the Google API error. No Slack alerts yet; they come in phase 2.

## Changing the active set

`check` runs whatever the Prompts tab marks active. To change it, run `select` locally **without**
`--dry-run`. It replaces the Prompts tab, and the next scheduled run uses the new set. Earlier
checks of prompts that stay active still count on the dashboard.
