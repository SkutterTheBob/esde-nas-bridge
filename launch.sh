#!/bin/sh
# Wrapper for es_systems.xml <command>, mirroring launch.bat's fix for
# Windows: sets the working directory to this script's own location (the
# repo root) before invoking Python, so `-m src.launch_wrapper` can find
# the `src` package and config.yaml regardless of ES-DE's own cwd.
cd "$(dirname "$0")"
exec .venv/bin/python -m src.launch_wrapper "$@"
