$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $env:CUDA_PATH) { throw 'Set CUDA_PATH to your installed CUDA toolkit.' }
$cuda = $env:CUDA_PATH
if (-not (Test-Path -LiteralPath $cuda -PathType Container)) { throw "CUDA toolkit directory not found: $cuda" }
$env:PATH = "$cuda/bin;$cuda/bin/x64;$env:PATH"
# A pipeline makes Windows PowerShell wait for the native GUI executable and
# preserves redirected benchmark output and the exit code.
& "$root/build/2DFastTurbo.exe" @args | Write-Output
exit $LASTEXITCODE
