#!/usr/bin/env bash
# pm-gh.sh — the GitHub-native PM adapter (DESIGN.md §2, the work system).
#
# Mirrors bin/pm's subcommand surface on GitHub Issues + the org Project, so when the
# Chairman flips PM_BACKEND=github the mandates' pm workflow carries over verbatim —
# same verbs, same flags, ticket ids become issue numbers (org#N; historical pm#N ids
# resolve via docs/pm-id-map.json):
#
#   pm-gh.sh create  --project IT --title "…" [--assigned it] [--assignee <github-user>]
#                    [--desc "…"] [--priority 1-5] [--due YYYY-MM-DD]
#                    [--type epic|feature|story] [--parent N]   (the work-item tree:
#                    --type adds the [KIND] prefix + kind label and, for feature/story,
#                    REQUIRES an Acceptance/Done-when line in --desc; --parent links a
#                    native sub-issue and, when --project is omitted, inherits the
#                    parent's dept:* labels so the runner routes children for free;
#                    --assignee sets a real GitHub assignee, which only makes sense for a
#                    HUMAN — the warden passes the Chairman so his queue children land in
#                    his "Assigned to me" filter. Not to be confused with --assigned, the
#                    dept:* wake label: agents share one identity and can't be assignees.)
#   pm-gh.sh tree    --id N                      render the sub-issue tree under org#N
#   pm-gh.sh tasks   --project IT [--all]
#   pm-gh.sh move    --id N --to <Status: org-chart pm_stages, or a pm_holding_stages
#                    parking column — Blocked / "On Hold" — for work stalled on a blocker
#                    or deliberately deprioritized; move it back to its flow status on
#                    resume. Multi-word names need quotes: --to "In Progress">
#   pm-gh.sh comment --id N --body "…"
#   pm-gh.sh comments --id N
#   pm-gh.sh done    --id N [--pr <owner/repo#N|url>] [--done-when "…"] [--demo <comment-url>]
#                    (typed items pass the closure gate — scripts/workitems.py can-close:
#                    no open children; story needs a merged PR or done-when; feature needs
#                    its accepted [DEMO] or done-when — and the evidence is posted back as
#                    a [CLOSE] comment before the issue closes)
#   pm-gh.sh drop    --id N --reason "…"   won't-do: posts a [DROP] record, sets the board
#                    to Dropped (org-chart pm_terminal_stages), closes as "not planned".
#                    Refused with open children. Dropping an epic or objective is the
#                    Chairman's call alone — during an agent wake, file a [PROPOSAL] instead.
#
# Needs: gh auth (with project scope for move/create), the Project + labels from
# scripts/github-pm-setup.sh. Fails loud and early — a silent PM write that went
# nowhere is exactly the drift the reconciler used to exist to catch.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

# Identity comes from .env — no fallbacks (ticketing the wrong org is worse than a
# refusal). The owner defaults to the org repo's owner half.
REPO="${ORG_GH_REPO:?ORG_GH_REPO not set — cp .env.example .env and fill it in}"
OWNER="${GITHUB_OWNER:-${REPO%%/*}}"
PROJECT_TITLE="${PM_PROJECT_TITLE:-Org Project}"

command -v gh >/dev/null || { echo "pm-gh: gh CLI required" >&2; exit 1; }

usage() { sed -n '4,36p' "$0" >&2; exit 2; }
[ $# -ge 1 ] || usage
cmd="$1"; shift

# --- flag parsing (same names as bin/pm) --------------------------------------------------
project="" title="" assigned="" desc="" priority="" due="" id="" to="" body="" all=0
type="" parent="" pr="" done_when="" demo="" assignee="" reason=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project)   project="$2"; shift 2 ;;
    --title)     title="$2"; shift 2 ;;
    --assigned)  assigned="$2"; shift 2 ;;
    --assignee)  assignee="$2"; shift 2 ;;
    --desc)      desc="$2"; shift 2 ;;
    --priority)  priority="$2"; shift 2 ;;
    --due)       due="$2"; shift 2 ;;
    --id)        id="$2"; shift 2 ;;
    --to)        to="$2"; shift 2 ;;
    --body)      body="$2"; shift 2 ;;
    --type)      type="$2"; shift 2 ;;
    --parent)    parent="$2"; shift 2 ;;
    --pr)        pr="$2"; shift 2 ;;
    --done-when) done_when="$2"; shift 2 ;;
    --demo)      demo="$2"; shift 2 ;;
    --reason)    reason="$2"; shift 2 ;;
    --all)       all=1; shift ;;
    *) echo "pm-gh: unknown flag $1" >&2; usage ;;
  esac
done

dept_label() {  # project name → the dept:* wake label
  printf 'dept:%s' "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
}

project_number() {
  # Resolution order: $PM_PROJECT_NUMBER (the warden exports it to its children), the
  # org-chart global.pm_project_number pin (the REQUIRED pin of FIELD-NOTES §11 — the ONE
  # place board coordinates live), then a by-title lookup as the un-pinned fallback.
  # The pin costs nothing; the lookup bills the GraphQL pool on every single call.
  if [ -n "${PM_PROJECT_NUMBER:-}" ]; then printf '%s' "$PM_PROJECT_NUMBER"; return; fi
  local pin
  pin="$(sed -n 's/^[[:space:]]*pm_project_number:[[:space:]]*\([0-9][0-9]*\).*/\1/p' org-chart.yaml 2>/dev/null | head -1)"
  if [ -n "$pin" ]; then printf '%s' "$pin"; return; fi
  gh project list --owner "$OWNER" --format json \
    --jq ".projects[] | select(.title == \"$PROJECT_TITLE\") | .number" | head -1
}

# --- board-coordinate cache (FIELD-NOTES §14) ---------------------------------------------
# The project id, Status field id, and option ids only change when github-pm-setup.sh
# reprovisions, but set_status used to re-discover all of them on every call — ~9 GraphQL
# requests per board move, exactly one of which (the write) did any work. Projects v2 is
# GraphQL-only, and GraphQL is metered as a separate hourly pool from REST: the org once
# burned the whole pool on this while REST sat idle, and the runner's GH_RATE_FLOOR then
# parked every wake until the window reset. So: the coordinates live in a TSV cache under
# state/ (runtime-only, gitignored), written on first use, deleted by setup, refreshed at
# most once per call when a write looks stale.
BOARD_CACHE="state/pm-board.tsv"

board_get() { awk -F'\t' -v k="$1" '$1 == k { print $2; exit }' "$BOARD_CACHE" 2>/dev/null || true; }
board_opt() { awk -F'\t' -v n="$1" '$1 == "option" && $2 == n { print $3; exit }' "$BOARD_CACHE" 2>/dev/null || true; }

board_refresh() {  # 2 GraphQL requests, amortized over every later call
  local pn pid
  pn="$(project_number)"
  [ -n "$pn" ] || { echo "pm-gh: project '$PROJECT_TITLE' not found (run github-pm-setup.sh, then pin org-chart global.pm_project_number)" >&2; return 1; }
  pid="$(gh project view "$pn" --owner "$OWNER" --format json --jq .id)"
  [ -n "$pid" ] || { echo "pm-gh: could not resolve project #$pn (owner $OWNER)" >&2; return 1; }
  mkdir -p "$(dirname "$BOARD_CACHE")"
  {
    printf 'number\t%s\n' "$pn"
    printf 'project_id\t%s\n' "$pid"
    gh project field-list "$pn" --owner "$OWNER" --format json --jq '
      (.fields[] | select(.name == "Status"))
      | "status_field\t\(.id)", (.options[] | "option\t\(.name)\t\(.id)")'
  } > "$BOARD_CACHE.tmp" && mv "$BOARD_CACHE.tmp" "$BOARD_CACHE"
}

board_item_id() {  # $1 = issue number → its item id on the org board ('' when absent).
  # One targeted request, constant cost — never page the whole board to find one issue.
  gh api graphql \
    -f query='query($owner: String!, $name: String!, $issue: Int!) {
        repository(owner: $owner, name: $name) { issue(number: $issue) {
          projectItems(first: 10) { nodes { id project { id } } } } } }' \
    -f owner="${REPO%%/*}" -f name="${REPO#*/}" -F issue="$1" \
    --jq ".data.repository.issue.projectItems.nodes[] | select(.project.id == \"$(board_get project_id)\") | .id" \
    2>/dev/null | head -1 || true
}

set_status() {  # $1 = issue number, $2 = Status name, $3 = known project-item id (optional —
                # create passes the id item-add just returned, so its status set is 1 request)
  local pn pid item fid oid
  oid="$(board_opt "$2")"
  if [ -z "$oid" ]; then board_refresh || return 1; oid="$(board_opt "$2")"; fi
  [ -n "$oid" ] || { echo "pm-gh: Status option '$2' not found (canon: org-chart pm_stages + pm_holding_stages + pm_terminal_stages; run github-pm-setup.sh)" >&2; return 1; }
  pn="$(board_get number)"; pid="$(board_get project_id)"; fid="$(board_get status_field)"
  item="${3:-}"
  [ -n "$item" ] || item="$(board_item_id "$1")"
  if [ -z "$item" ]; then
    # Chairman-filed issues (objective.yml et al.) never go through `create`, so they
    # never land on the board — that silently defeated "move this to In Progress" for
    # exactly the items org#134 asked us to track. Add on first move instead of failing hard.
    item="$(gh project item-add "$pn" --owner "$OWNER" \
            --url "https://github.com/$REPO/issues/$1" --format json --jq .id)"
    [ -n "$item" ] || { echo "pm-gh: could not add issue #$1 to the project board" >&2; return 1; }
  fi
  if ! gh project item-edit --id "$item" --project-id "$pid" --field-id "$fid" \
        --single-select-option-id "$oid" >/dev/null 2>&1; then
    # Stale coordinates (the board was reprovisioned since the cache was written):
    # refresh once and retry LOUD — a second failure is real and must surface.
    board_refresh || return 1
    pn="$(board_get number)"; pid="$(board_get project_id)"; fid="$(board_get status_field)"; oid="$(board_opt "$2")"
    [ -n "$fid" ] && [ -n "$oid" ] || { echo "pm-gh: Status option '$2' missing after refresh (canon: org-chart pm_stages + pm_holding_stages + pm_terminal_stages; run github-pm-setup.sh)" >&2; return 1; }
    gh project item-edit --id "$item" --project-id "$pid" --field-id "$fid" --single-select-option-id "$oid" >/dev/null
  fi
  echo "org#$1 → $2"
}

workitems() { PYTHONPATH=scripts python3 scripts/workitems.py "$@"; }

case "$cmd" in
  create)
    [ -n "$title" ] || { echo "pm-gh: create needs --title" >&2; exit 2; }
    [ -n "$project" ] || [ -n "$parent" ] || { echo "pm-gh: create needs --project (or --parent to inherit its routing)" >&2; exit 2; }
    if [ -n "$type" ]; then
      case "$type" in epic|feature|story) ;; *) echo "pm-gh: --type must be epic|feature|story" >&2; exit 2 ;; esac
      # A feature/story is born with the bar its close will be judged against — no
      # acceptance line, no issue (the demo/close gates need something to bind to).
      if [ "$type" != "epic" ] && ! printf '%s' "$desc" | grep -qiE 'acceptance|done.when'; then
        echo "pm-gh: a $type needs an Acceptance or Done-when line in --desc (the closure gate binds to it)" >&2; exit 2
      fi
      prefix="[$(printf '%s' "$type" | tr '[:lower:]' '[:upper:]')] "
      case "$title" in "$prefix"*) ;; *) title="$prefix$title" ;; esac
    fi
    # Refuse a taxonomy-violating edge BEFORE creating, not after (no orphan issues).
    [ -n "$parent" ] && [ -n "$type" ] && { workitems check-edge "$parent" "$type" || exit 1; }
    full_body="$desc"
    [ -n "$priority" ] && full_body="$full_body"$'\n\n'"priority: $priority"
    [ -n "$due" ]      && full_body="$full_body"$'\n'"due: $due"
    labels=()
    [ -n "$project" ] && labels+=(--label "$(dept_label "$project")")
    [ -n "$assigned" ] && [ "$assigned" != "$project" ] && labels+=(--label "$(dept_label "$assigned")")
    if [ -z "$project" ]; then  # --parent only: children inherit the parent's routing
      for l in $(gh api "repos/$REPO/issues/$parent" --jq '.labels[].name | select(startswith("dept:"))'); do
        labels+=(--label "$l")
      done
      [ ${#labels[@]} -gt 0 ] || { echo "pm-gh: parent org#$parent carries no dept:* label to inherit — pass --project" >&2; exit 1; }
    fi
    [ -n "$type" ] && labels+=(--label "$type")
    # --assignee is a HUMAN's GitHub "Assigned to me" filter; --assigned above is a dept:*
    # WAKE label for an agent. They can never collapse into one flag: agents share the
    # Chairman's single GitHub identity, so an agent can't BE an assignee — "who does this
    # wake" and "whose list does this appear in" are different questions with different
    # answers. (labels[] is the gh-args accumulator; it is never empty by the checks above.)
    [ -n "$assignee" ] && labels+=(--assignee "$assignee")
    url="$(gh issue create --repo "$REPO" --title "$title" --body "${full_body:-—}" "${labels[@]}")"
    n="${url##*/}"
    pn="$(project_number || true)"
    if [ -n "$pn" ]; then
      # Keep the item id item-add returns — set_status then skips its board lookup.
      item="$(gh project item-add "$pn" --owner "$OWNER" --url "$url" --format json --jq .id 2>/dev/null || true)"
      set_status "$n" "Todo" ${item:+"$item"} >/dev/null
    fi
    [ -n "$parent" ] && workitems link "$parent" "$n"
    echo "created org#$n  $url"
    ;;
  tree)
    [ -n "$id" ] || { echo "pm-gh: tree needs --id" >&2; exit 2; }
    workitems tree "$id"
    ;;
  tasks)
    [ -n "$project" ] || { echo "pm-gh: tasks needs --project" >&2; exit 2; }
    state="open"; [ "$all" = 1 ] && state="all"
    # REST, not `gh issue list` (which is GraphQL under the hood): list reads belong on
    # the separately-metered REST pool the runner already ETags. Same output shape.
    gh api -X GET "repos/$REPO/issues" --paginate \
      -f labels="$(dept_label "$project")" -f state="$state" -F per_page=100 \
      --jq '.[] | select(.pull_request | not) | "org#\(.number)  [\(.state | ascii_upcase)]  \(.title)"'
    ;;
  move)
    [ -n "$id" ] && [ -n "$to" ] || { echo "pm-gh: move needs --id and --to" >&2; exit 2; }
    set_status "$id" "$to"
    ;;
  comment)
    [ -n "$id" ] && [ -n "$body" ] || { echo "pm-gh: comment needs --id and --body" >&2; exit 2; }
    gh issue comment "$id" --repo "$REPO" --body "$body" >/dev/null && echo "commented on org#$id"
    ;;
  comments)
    [ -n "$id" ] || { echo "pm-gh: comments needs --id" >&2; exit 2; }
    gh issue view "$id" --repo "$REPO" --comments
    ;;
  done)
    [ -n "$id" ] || { echo "pm-gh: done needs --id" >&2; exit 2; }
    # The closure gate — the tree's upward path. Facts are re-derived live by
    # workitems.py can-close (open children; story: merged PR / done-when; feature:
    # accepted [DEMO] / done-when); untyped childless tickets pass untouched.
    ev=()
    [ -n "$pr" ]        && ev+=(--pr "$pr")
    [ -n "$done_when" ] && ev+=(--done-when "$done_when")
    [ -n "$demo" ]      && ev+=(--demo "$demo")
    if ! out="$(workitems can-close "$id" ${ev[@]+"${ev[@]}"})"; then
      printf '%s\n' "$out" >&2
      echo "pm-gh: closure gate refused org#$id (scripts/workitems.py can-close)" >&2
      exit 1
    fi
    kind="$(printf '%s\n' "$out" | sed -n 's/^kind=//p')"
    if [ -n "$kind" ]; then  # typed items put their evidence on the record before closing
      payload="item: org#$id"$'\n'"kind: $kind"$'\n'"evidence:"
      [ -n "$pr" ]        && payload="$payload"$'\n'"  pr: $pr"
      [ -n "$done_when" ] && payload="$payload"$'\n'"  done_when: $done_when"
      [ -n "$demo" ]      && payload="$payload"$'\n'"  demo: $demo"
      gh issue comment "$id" --repo "$REPO" \
        --body "$(printf '[CLOSE] org#%s\n```yaml\n%s\n```\n' "$id" "$payload")" >/dev/null
    fi
    set_status "$id" "Done" || true   # board first; closing is the record either way
    gh issue close "$id" --repo "$REPO" && echo "closed org#$id"
    ;;
  drop)
    [ -n "$id" ] || { echo "pm-gh: drop needs --id" >&2; exit 2; }
    [ -n "$reason" ] || { echo "pm-gh: drop needs --reason — a won't-do without a recorded why is just a disappearance" >&2; exit 2; }
    # Dropping is deprioritization authority, scaled like the tree itself: a dept may bury
    # its own stories/features (with the reason on the record), but an epic or objective is
    # the Chairman's scope — an agent wake (AGENT_NAME set; the Chairman's own sessions
    # never are, same mechanism as objective_gate.py) gets refused toward a [PROPOSAL].
    kind=""
    issue_labels="$(gh api "repos/$REPO/issues/$id" --jq '.labels[].name' 2>/dev/null || true)"  # once, via REST — not one GraphQL lookup per kind
    for k in objective epic feature story; do
      printf '%s\n' "$issue_labels" | grep -qx "$k" && { kind="$k"; break; }
    done
    if [ -n "${AGENT_NAME:-}" ] && { [ "$kind" = "objective" ] || [ "$kind" = "epic" ]; }; then
      echo "pm-gh: dropping an $kind is the Chairman's call alone — file a [PROPOSAL] naming org#$id and why" >&2; exit 1
    fi
    open_kids="$(gh api "repos/$REPO/issues/$id/sub_issues?per_page=100" \
                 --jq '[.[] | select(.state == "open")] | length' 2>/dev/null || echo 0)"
    if [ "${open_kids:-0}" != "0" ]; then
      echo "pm-gh: org#$id still has $open_kids open child(ren) — drop or close them first (a dropped parent must not orphan live work)" >&2; exit 1
    fi
    gh issue comment "$id" --repo "$REPO" \
      --body "$(printf '[DROP] org#%s\n```yaml\nitem: org#%s\nkind: %s\nreason: %s\n```\n' "$id" "$id" "${kind:-untyped}" "$reason")" >/dev/null
    set_status "$id" "Dropped" || true   # board first; closing is the record either way
    gh issue close "$id" --repo "$REPO" --reason "not planned" && echo "dropped org#$id (closed as not planned)"
    ;;
  *) usage ;;
esac
