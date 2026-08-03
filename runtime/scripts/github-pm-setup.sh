#!/usr/bin/env bash
# github-pm-setup.sh — idempotent provisioning (DESIGN.md §2): canonical role/type
# labels and the single org Project whose built-in Status field mirrors the org-chart.yaml
# status canon (pm_stages + holding + terminal) — the one place the list is defined (same
# canon the linter enforces on prose).
#
# HUMAN COMMISSIONING TOOL. Run outside every agent wake from the operator account (needs
# gh auth with project scope). The controller and agents never invoke this script.
# Safe to re-run: labels are create-or-update, the project and field are found before created.
#
#   scripts/github-pm-setup.sh            # provision org repo labels + the Project
#   DRY_RUN=1 scripts/github-pm-setup.sh  # print what would happen
set -euo pipefail
cd "$(dirname "$0")/.."

# Repository identities come from the compiled organization document. Operator environment
# variables cannot redirect commissioning to a different repository.
ORG_REPO="$(python3 -c 'import json; print(json.load(open("organization.yaml"))["repositories"]["org"]["slug"])')"
PRODUCT_REPO="$(python3 -c 'import json; print(json.load(open("organization.yaml"))["repositories"]["product"]["slug"])')"
OWNER="${ORG_REPO%%/*}"
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

# --- labels: role routing (the wake signal) + the template types -------------------------
# The role roster comes from org-chart.yaml (the single source of truth) — adding a
# role there is enough; re-run this script to create its label. The reference org
# shipped two follow-up commits for labels this script hardcoded; the chart read ends that.
roles="$(sed -n '/^roles:/,$ s/^  - name:[[:space:]]*\([A-Za-z0-9_-]*\).*/\1/p' org-chart.yaml)"
[ -n "$roles" ] || { echo "could not read roles from org-chart.yaml" >&2; exit 1; }
palette=(1D76DB 0E8A16 5319E7 006B75 6f42c1 E99695 0052CC C5DEF5)
for repo in "$ORG_REPO" "$PRODUCT_REPO"; do
  echo "── labels on $repo"
  i=0
  for d in $roles; do
    color="${palette[$((i % ${#palette[@]}))]}"; i=$((i + 1))
    # Governance review is org-repo only; it does not own product work intake.
    if [ "$d" = "governance-reviewer" ] && [ "$repo" != "$ORG_REPO" ]; then continue; fi
    run gh label create "role:$d" --repo "$repo" --force --color "$color" \
        --description "wake: $d owns this"
  done
  if [ "$repo" = "$ORG_REPO" ]; then
    run gh label create "objective" --repo "$repo" --force --color B60205 \
        --description "Human Principal work injection"
  fi
  run gh label create "proposal"  --repo "$repo" --force --color FBCA04 \
      --description "agent → Human Principal: needs a human verdict"
  # Work-item tree kinds; the controller enforces typed parent edges and closure.
  # `feature` predates the tree (the Feature issue template applies it); --force here
  # just pins its description to the closure rule it now carries.
  run gh label create "epic"      --repo "$repo" --force --color 3E4B9E \
      --description "work-item tree: delivery track under an objective (closes by rollup)"
  run gh label create "feature"   --repo "$repo" --force --color 0052CC \
      --description "work-item tree: shippable slice under an epic (closes on merged PR or accepted [DEMO])"
  run gh label create "story"     --repo "$repo" --force --color C2E0C6 \
      --description "work-item tree: unit of work under a feature (closes on merged PR or accepted [DEMO])"
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
  run gh project link "$pn" --owner "$OWNER" --repo "$ORG_REPO"
  run gh project link "$pn" --owner "$OWNER" --repo "$PRODUCT_REPO"
fi

# The Project number is desired commissioning data for the installed controller.
pin="$(sed -n 's/^[[:space:]]*pm_project_number:[[:space:]]*\([0-9][0-9]*\).*/\1/p' org-chart.yaml | head -1)"
if [ "$pn" != "<new>" ] && [ "${pin:-}" != "$pn" ]; then
  echo "  WARNING: org-chart global.pm_project_number is ${pin:-unset} but the board is #$pn — commission it"
fi

echo "✓ Phase 2 PM scaffolding ready: labels + project (Status = org-chart pm_stages + pm_holding_stages + pm_terminal_stages)"
echo "  Next: file the first [OBJECTIVE] issue — agents work the board via scripts/pm-gh.sh"
