$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
Push-Location $project
try {
    if (-not (Test-Path .venv/Scripts/python.exe)) { & "$PSScriptRoot/build-windows.ps1" }
    & .venv/Scripts/python.exe -m fastturbo @args
    if ($LASTEXITCODE) { throw 'FastTurbo failed.' }
} finally { Pop-Location }
