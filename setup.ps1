#Requires -Version 5.1
# Run this from the repo root after cloning/copying it to a new machine
# (e.g. the Basement PC): .\setup.ps1
#
# Automates the parts that are identical on every machine (venv, deps) and
# tells you exactly what's left to customize (usually just this machine's
# ES-DE and RetroArch install paths -- the NAS/systems config is normally
# shared across machines pointed at the same library).

$ErrorActionPreference = "Stop"

Write-Host "=== esde-nas-bridge setup ===" -ForegroundColor Cyan

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
} else {
    Write-Host "Virtual environment already exists, leaving it alone."
}

Write-Host "Installing dependencies..."
& .\.venv\Scripts\pip.exe install -r requirements.txt --quiet

if (-not (Test-Path "config\config.yaml")) {
    Write-Host "Creating config\config.yaml from the example..."
    Copy-Item "config\config.example.yaml" "config\config.yaml"
    $isNewConfig = $true
} else {
    Write-Host "config\config.yaml already exists, leaving it alone."
    $isNewConfig = $false
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host ""

if ($isNewConfig) {
    Write-Host "Before running anything else, edit config\config.yaml for THIS machine:" -ForegroundColor Yellow
    Write-Host "  - media_root / gamelists_root: point at THIS machine's ES-DE install"
    Write-Host "    (check es_log.txt in your ES-DE folder if you're not sure of the path)"
    Write-Host "  - retroarch.binary / core_dir: point at THIS machine's RetroArch install"
    Write-Host "  - nas.main-nas.root: should already be correct if using the same mapped"
    Write-Host "    drive letter as your other machine(s) -- verify with: dir Y:\"
    Write-Host "  - systems / skraper_imports: usually identical across machines pointed"
    Write-Host "    at the same NAS -- consider copying these sections from a working"
    Write-Host "    machine's config.yaml rather than retyping them"
    Write-Host ""
    Write-Host "(Or skip hand-editing config.yaml entirely -- run tui.bat and use its"
    Write-Host "Settings screen for the machine-specific paths above instead.)"
    Write-Host ""
}

Write-Host "Once config.yaml is ready, either:"
Write-Host "  tui.bat                              (terminal UI -- menus/forms, recommended)"
Write-Host "or the underlying CLI directly:"
Write-Host "  python -m src.cli sync"
Write-Host "  python -m src.cli generate-es-systems"
Write-Host ""
Write-Host "Then paste the generated entries into this machine's es_systems.xml,"
Write-Host "apply the ParseGamelistOnly / LegacyGamelistFileLocation / MediaDirectory"
Write-Host "settings from TESTING.md, and restart ES-DE."
