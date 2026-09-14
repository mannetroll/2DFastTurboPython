param([switch]$AllResolutions)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
Push-Location $project
try {
    & "$PSScriptRoot/build-windows.ps1"
    & .venv/Scripts/python.exe -m pytest -q
    if ($LASTEXITCODE) { throw 'Python/backend tests failed.' }
    # CTest inherits the CUDA DLL location from the built package.
    $savedPath = $env:PATH
    try {
        $env:PATH = (Join-Path $project 'src/fastturbo/_native') + ';' + $env:PATH
        ctest --test-dir build/windows --output-on-failure
        if ($LASTEXITCODE) { throw 'Native lifecycle tests failed.' }
    } finally { $env:PATH = $savedPath }
    if ($AllResolutions) {
        & .venv/Scripts/python.exe scripts/check_gui.py --all-resolutions --output results/gui-all-resolutions.json
        if ($LASTEXITCODE) { throw 'GUI resolution tests failed.' }
    }
} finally { Pop-Location }
