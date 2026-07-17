#!/usr/bin/env bash
# backup-state.sh — snapshot the org's operational memory, which is otherwise a single
# copy on one laptop: state/ (spend ledger, demand metrics, semantic-memory store, warden
# rap sheet, cursors) plus the two live-edited root files (org-chart.yaml, blockers.yaml).
# Losing state/ loses the org's entire P&L and history; this is the difference between
# "annoying" and "the experiment's data is gone."
#
# One tar.gz per day (re-running the same day refreshes it, so the daily watcher is
# idempotent), pruned to the newest BACKUP_KEEP. Default destination is backups/ in the
# repo (gitignored — better than nothing, same disk); point BACKUP_DIR somewhere real
# (external drive, synced folder) for actual durability.
#
# Wired into schedule.sh as a WATCHER (daily). Tunables via env:
#   BACKUP_DIR   destination directory (default: <repo>/backups)
#   BACKUP_KEEP  daily archives to retain (default: 14)
set -euo pipefail
cd "$(dirname "$0")/.."

dest="${BACKUP_DIR:-$(pwd)/backups}"
keep="${BACKUP_KEEP:-14}"
day="$(date -u +%Y%m%d)"
out="$dest/org-state.$day.tar.gz"

[ -d state ] || { echo "backup-state: no state/ yet — nothing to back up"; exit 0; }
mkdir -p "$dest"

# --ignore-failed-read (GNU) / plain tar (BSD): a file disappearing mid-archive (a cursor
# being rewritten) must not fail the backup. Build the file list first, tolerate races.
tmp="$out.tmp"
if tar --version 2>/dev/null | grep -q GNU; then
  tar --ignore-failed-read -czf "$tmp" state org-chart.yaml blockers.yaml 2>/dev/null || true
else
  tar -czf "$tmp" state org-chart.yaml blockers.yaml 2>/dev/null || true
fi
[ -s "$tmp" ] || { echo "backup-state: archive came out empty — NOT replacing $out" >&2; rm -f "$tmp"; exit 1; }
mv "$tmp" "$out"
echo "backup-state: wrote $out ($(wc -c < "$out" | tr -d '[:space:]') bytes)"

# Prune to the newest $keep dailies (lexicographic == chronological): newest first, skip the
# $keep survivors, delete the rest. NOT `head -n -N` — that's GNU-only; macOS head dies with
# `head: illegal line count`, which under `set -euo pipefail` failed this script on EVERY run
# (after the archive lands, so it looked half-alive: tarballs piling up unpruned, rc always
# nonzero, the health stamp never written).
ls -1 "$dest"/org-state.*.tar.gz 2>/dev/null | sort -r | tail -n +$((keep + 1)) | while read -r old; do
  rm -f "$old"
  echo "backup-state: pruned $old"
done
