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

# --- the status canon, straight from org-chart.yaml (never restate it) ------------------
# Flow statuses (ordered pipeline) + holding columns (parked) + terminal-alternates
# (Dropped). All are options of the board's ONE built-in Status field. Names may be
# multi-word ("In Progress"), so trim around commas — never strip spaces wholesale.
trim_list() { sed -n "s/^[[:space:]]*$1:[[:space:]]*\[\(.*\)\]/\1/p" org-chart.yaml \
              | sed 's/[[:space:]]*,[[:space:]]*/,/g; s/^[[:space:]]*//; s/[[:space:]]*$//'; }
stages="$(trim_list pm_stages)"
[ -n "$stages" ] || { echo "could not read pm_stages from org-chart.yaml" >&2; exit 1; }
holding="$(trim_list pm_holding_stages)"
terminal="$(trim_list pm_terminal_stages)"
status_options="$stages${holding:+,$holding}${terminal:+,$terminal}"
echo "statuses (org-chart pm_stages): $stages"
[ -n "$holding" ] && echo "holding columns (org-chart pm_holding_stages): $holding"
[ -n "$terminal" ] && echo "terminal-alternates (org-chart pm_terminal_stages): $terminal"

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

# The board's BUILT-IN Status single-select mirrors the canon. Every project is born with
# one (Todo/In Progress/Done) and it cannot be deleted — which is exactly why the org uses
# it instead of a parallel custom field: it's what every GitHub view groups and counts by,
# and a second field is a standing drift invitation. gh cannot edit options, so reconcile
# goes through the GraphQL API and REPLACES the option list wholesale when any canon name
# is missing (same-named options keep their items; options renamed away lose theirs — a
# canon change is a rare, deliberate act). Two UI-only switches accompany this (Project →
# ⋯ → Workflows): keep "Item added → Todo" ON; turn "Item closed → Done" OFF — that
# workflow can't tell Done from Dropped, and the pm-gh.sh done/drop gates set the
# terminal state themselves.
if [ "$pn" != "<new>" ]; then
  fid="$(gh project field-list "$pn" --owner "$OWNER" --format json \
         --jq '.fields[] | select(.name == "Status") | .id' 2>/dev/null || true)"
  have_opts="$(gh project field-list "$pn" --owner "$OWNER" --format json \
               --jq '[.fields[] | select(.name == "Status") | .options[].name] | join(",")' 2>/dev/null || true)"
  missing="$(IFS=,; for o in $status_options; do case ",$have_opts," in *",$o,"*) ;; *) printf '%s,' "$o" ;; esac; done)"
  if [ -z "$fid" ]; then
    echo "  WARNING: no built-in Status field found (unexpected on a ProjectV2) — skipping option reconcile" >&2
  elif [ -z "$missing" ]; then
    echo "  Status options already cover the canon ($status_options)"
  else
    echo "  Status options missing: ${missing%,} — replacing option list with the canon"
    mutation="$(printf '%s' "$status_options" | python3 -c '
import json, sys
names = [s.strip() for s in sys.stdin.read().split(",") if s.strip()]
opts = ", ".join("{name: %s, color: GRAY, description: \"\"}" % json.dumps(n) for n in names)
print("mutation($f: ID!) { updateProjectV2Field(input: {fieldId: $f, singleSelectOptions: [%s]})"
      " { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }" % opts)')"
    run gh api graphql -f query="$mutation" -F f="$fid" --jq '.data | keys[0]'
  fi
  legacy="$(gh project field-list "$pn" --owner "$OWNER" --format json \
            --jq '.fields[] | select(.name == "Stage") | .name' 2>/dev/null || true)"
  [ -n "$legacy" ] && echo "  ↳ a legacy Stage field exists — the org now runs on Status; delete Stage in the Project UI once nothing references it."
  run gh project link "$pn" --owner "$OWNER" --repo "$ORG_REPO"
  run gh project link "$pn" --owner "$OWNER" --repo "$PRODUCT_REPO"
fi

echo "✓ Phase 2 PM scaffolding ready: labels + project (Status = org-chart pm_stages + pm_holding_stages + pm_terminal_stages)"
echo "  Next: file the first [OBJECTIVE] issue — agents work the board via scripts/pm-gh.sh"
