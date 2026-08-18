#!/bin/sh
# Launches the terminal UI -- mirrors launch.sh: sets the working directory
# to this script's own location (the repo root) before invoking Python, so
# `-m src.tui` can find the `src` package and config.yaml regardless of the
# caller's own cwd.
cd "$(dirname "$0")"
exec .venv/bin/python -m src.tui "$@"
