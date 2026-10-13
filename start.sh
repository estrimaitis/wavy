#!/usr/bin/env bash
# Launch Wavy on macOS / Linux. (Windows: use start.bat)
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./setup.sh
WAVY_OPEN_BROWSER=1 exec .venv/bin/python -m server.app
