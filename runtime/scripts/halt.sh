#!/usr/bin/env bash
# Local kill switch. Drops the .halt flag the runner and every agent cycle check
# before acting, pausing the org immediately. Run scripts/resume.sh to clear it.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "halted by operator at $(date)" > .halt
echo "halted — all agents paused. Run scripts/resume.sh to clear."
