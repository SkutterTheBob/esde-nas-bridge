@echo off
REM Wrapper for es_systems.xml <command>. ES-DE launches commands with its
REM own working directory, not this repo's -- which breaks both
REM `python -m src.launch_wrapper` (needs the repo root on sys.path to find
REM the `src` package) and anything resolved relative to the current
REM directory. %~dp0 expands to this .bat file's own folder (with a
REM trailing backslash), which is the repo root as long as this file stays
REM at the repo root -- so `cd /d` here fixes both problems at once,
REM regardless of where/how ES-DE actually invokes this.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m src.launch_wrapper %*
