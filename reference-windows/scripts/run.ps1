$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$env:PATH = "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.0/bin;$env:PATH"
# A pipeline makes Windows PowerShell wait for the native GUI executable and
# preserves redirected benchmark output and the exit code.
& "$root/build/2DFastTurbo.exe" @args | Write-Output
exit $LASTEXITCODE
