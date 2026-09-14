param([switch]$Force)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
Push-Location $project
try {
    uv --quiet --cache-dir build/uv-cache sync --python 3.13
    if ($LASTEXITCODE) { throw 'Dependency or native build failed.' }
    if ($Force) {
        & .venv/Scripts/python.exe -m fastturbo.build --force
        if ($LASTEXITCODE) { throw 'Native build failed.' }
    }
} finally { Pop-Location }
