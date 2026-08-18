@echo off
REM Double-click this to launch the terminal UI -- no manual venv
REM activation needed, same trick launch.bat uses: cd /d "%~dp0" makes this
REM the repo root regardless of where it's launched from, and invoking
REM ".venv\Scripts\python.exe" by its full path runs inside that venv
REM without ever needing `activate`.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m src.tui %*
if errorlevel 1 pause
