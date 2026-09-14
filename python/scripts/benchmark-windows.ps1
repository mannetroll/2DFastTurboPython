$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & .venv/Scripts/python.exe scripts/benchmark_windows.py @args
    if ($LASTEXITCODE) { throw 'Performance comparison failed.' }
} finally { Pop-Location }
