#!/usr/bin/env bash
# Clears the 🛑 kill switch so agents may act again.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f .halt
echo "▶️  resumed — .halt cleared."
