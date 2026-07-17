#!/usr/bin/env bash
# github-pm-setup.sh — idempotent Phase 2 provisioning (GITHUB-NATIVE-PLAN.md): the dept/type
# labels and the single org Project whose Stage field mirrors org-chart.yaml pm_stages — the
# one place the stage list is defined (same canon the linter enforces on prose).
#
# Run from the org laptop (needs gh auth with project scope: `gh auth refresh -s project`).
# Safe to re-run: labels are create-or-update, the project and field are found before created.
#
#   scripts/github-pm-setup.sh            # provision org repo labels + the Project
#   DRY_RUN=1 scripts/github-pm-setup.sh  # print what would happen
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

# Identity comes from .env — no fallbacks (provisioning the wrong org is worse than a
# refusal). The owner defaults to the org repo's owner half.
ORG_REPO="${ORG_GH_REPO:?ORG_GH_REPO not set — cp .env.example .env and fill it in}"
PRODUCT_REPO="${PRODUCT_GH_REPO:?PRODUCT_GH_REPO not set — cp .env.example .env and fill it in}"
OWNER="${GITHUB_OWNER:-${ORG_REPO%%/*}}"
PROJECT_TITLE="${PM_PROJECT_TITLE:-Org Project}"
run() { if [ "${DRY_RUN:-0}" = "1" ]; then echo "DRY: $*"; else "$@"; fi; }

command -v gh >/dev/null || { echo "gh CLI required" >&2; exit 1; }

# --- the stage canon, straight from org-chart.yaml (never restate it) -------------------
# Flow stages (ordered pipeline) + holding columns (parked states). Both are options of the
# one Stage field; holding stages are NOT part of the ordered flow (org-chart pm_holding_stages).
stages="$(sed -n 's/^[[:space:]]*pm_stages:[[:space:]]*\[\(.*\)\]/\1/p' org-chart.yaml | tr -d ' ')"
[ -n "$stages" ] || { echo "could not read pm_stages from org-chart.yaml" >&2; exit 1; }
holding="$(sed -n 's/^[[:space:]]*pm_holding_stages:[[:space:]]*\[\(.*\)\]/\1/p' org-chart.yaml | tr -d ' ')"
stage_options="$stages${holding:+,$holding}"
echo "stages (org-chart pm_stages): $stages"
[ -n "$holding" ] && echo "holding columns (org-chart pm_holding_stages): $holding"

# --- labels: dept routing (the wake signal) + the template types -------------------------
# The department roster comes from org-chart.yaml (the single source of truth) — adding a
# department there is enough; re-run this script to create its label. The reference org
# shipped two follow-up commits for labels this script hardcoded; the chart read ends that.
depts="$(sed -n 's/^  - name:[[:space:]]*\([A-Za-z0-9_-]*\).*/\1/p' org-chart.yaml)"
[ -n "$depts" ] || { echo "could not read departments from org-chart.yaml" >&2; exit 1; }
palette=(1D76DB 0E8A16 5319E7 006B75 6f42c1 E99695 0052CC C5DEF5)
for repo in "$ORG_REPO" "$PRODUCT_REPO"; do
  echo "── labels on $repo"
  i=0
  for d in $depts; do
    color="${palette[$((i % ${#palette[@]}))]}"; i=$((i + 1))
    # dept:warden is ORG-REPO ONLY — the warden's queue and hygiene findings live where
    # the org lives, and the product repo has no warden to wake.
    if [ "$d" = "warden" ] && [ "$repo" != "$ORG_REPO" ]; then continue; fi
    run gh label create "dept:$d" --repo "$repo" --force --color "$color" \
        --description "wake: $d owns this"
  done
  run gh label create "objective" --repo "$repo" --force --color B60205 \
      --description "Chairman work injection"
  run gh label create "proposal"  --repo "$repo" --force --color FBCA04 \
      --description "agent → Chairman: needs a human verdict"
  # work-item tree kinds (objective → epic → feature → story; scripts/workitems.py).
  # `feature` predates the tree (the Feature issue template applies it); --force here
  # just pins its description to the closure rule it now carries.
  run gh label create "epic"      --repo "$repo" --force --color 3E4B9E \
      --description "work-item tree: delivery track under an objective (closes by rollup)"
  run gh label create "feature"   --repo "$repo" --force --color 0052CC \
      --description "work-item tree: shippable slice under an epic (closes on [DEMO] or done-when)"
  run gh label create "story"     --repo "$repo" --force --color C2E0C6 \
      --description "work-item tree: unit of work under a feature (closes on merged PR or done-when)"
done

# --- the one Project ----------------------------------------------------------------------
echo "── project: $PROJECT_TITLE"
pn="$(gh project list --owner "$OWNER" --format json \
      --jq ".projects[] | select(.title == \"$PROJECT_TITLE\") | .number" 2>/dev/null || true)"
if [ -z "$pn" ]; then
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "DRY: gh project create --owner $OWNER --title \"$PROJECT_TITLE\""
    pn="<new>"
  else
    pn="$(gh project create --owner "$OWNER" --title "$PROJECT_TITLE" --format json --jq .number)"
    echo "  created project #$pn"
  fi
else
  echo "  project #$pn exists"
fi

# Stage single-select mirroring pm_stages. gh cannot edit a field's options in place, so an
# existing Stage field is left alone (delete it in the UI to re-mint after a canon change).
if [ "$pn" != "<new>" ]; then
  have_stage="$(gh project field-list "$pn" --owner "$OWNER" --format json \
                --jq '.fields[] | select(.name == "Stage") | .name' 2>/dev/null || true)"
  if [ -z "$have_stage" ]; then
    run gh project field-create "$pn" --owner "$OWNER" --name "Stage" \
        --data-type SINGLE_SELECT --single-select-options "$stage_options"
  else
    echo "  Stage field exists (options are not reconciled — check against: $stage_options)"
    echo "  ↳ gh cannot add options in place; add any new holding columns ($holding) in the Project UI, or delete the Stage field there to re-mint."
  fi
  run gh project link "$pn" --owner "$OWNER" --repo "$ORG_REPO"
  run gh project link "$pn" --owner "$OWNER" --repo "$PRODUCT_REPO"
fi

echo "✓ Phase 2 PM scaffolding ready: labels + project (Stage = org-chart pm_stages + pm_holding_stages)"
echo "  Next: file the first [OBJECTIVE] issue — agents work the board via scripts/pm-gh.sh"
