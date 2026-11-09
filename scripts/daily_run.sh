#!/usr/bin/env bash
# Daily run: select prompts (GSC + LLM drafts), then check them on DataForSEO, as dry runs (CSVs
# under runs/). Logs to runs/logs/<date>.log. Stops on the first error with a non-zero exit code.
#
#   scripts/daily_run.sh
#
# Exit codes: 0 ok, 3 check's cost estimate over MAX_COST, 4 too few active prompts, other = a
# failed step. No secrets here: keys come from .env, which the tracker reads itself.
#
# When CITATION_TRACKER_ONE_SHOT=1 (set by the LaunchAgent, see README), the agent unloads and
# disables itself after this run, so it doesn't repeat until it is loaded again.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
MIN_ACTIVE=10
MAX_COST=0.40
LABEL=com.prakhar.citationtracker

mkdir -p runs/logs
LOG="runs/logs/$(date +%Y-%m-%d).log"
exec > >(tee -a "$LOG") 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }

finish() {
  local code=$?
  log "daily run finished with exit code $code"
  if [[ "${CITATION_TRACKER_ONE_SHOT:-}" == "1" ]]; then
    log "one-shot: disabling and unloading $LABEL"
    # disable first so it stays off after a reboot; bootout last, since it ends this job
    launchctl disable "gui/$(id -u)/$LABEL" || true
    launchctl bootout "gui/$(id -u)/$LABEL" || true
  fi
  exit "$code"
}
trap finish EXIT

log "daily run started in $REPO"

# Providers are explicit so a SERP_PROVIDER line in .env can't change them.
log "step 1: select"
"$PY" -m tracker select --source both --provider serphouse \
  --blogs data/private/blogs.csv --gsc data/private/gsc.csv --dry-run

active=$("$PY" -c 'import json; print(len(json.load(open("runs/active_prompts.json"))))')
log "select kept $active active prompts"
if (( active < MIN_ACTIVE )); then
  log "fewer than $MIN_ACTIVE active prompts; not running check"
  exit 4
fi

# check logs "estimated cost: N checks x \$0.004 = \$X" before any SERP call, and exits with code 3
# without spending anything if the estimate is over MAX_COST.
log "step 2: check (DataForSEO; aborts if the estimated cost is over \$$MAX_COST)"
"$PY" -m tracker check --provider dataforseo --max-cost "$MAX_COST" --dry-run

log "done"
