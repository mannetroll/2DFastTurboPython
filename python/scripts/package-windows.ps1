$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & .venv/Scripts/python.exe scripts/package_windows.py
    if ($LASTEXITCODE) { throw 'Windows packaging failed.' }
} finally { Pop-Location }
