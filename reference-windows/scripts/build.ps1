param([ValidateSet('Release','Debug')][string]$Configuration = 'Release', [switch]$CoreOnly, [switch]$Distribution)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio C++ x64 tools are required.' }
if (-not $env:CUDA_PATH) { throw 'Set CUDA_PATH to your installed CUDA toolkit.' }
$cuda = $env:CUDA_PATH
if (-not (Test-Path -LiteralPath (Join-Path $cuda 'bin/nvcc.exe'))) {
    throw 'CUDA Toolkit not found. Set CUDA_PATH to your installed CUDA 13.x toolkit.'
}
$gui = if ($CoreOnly) { 'OFF' } else { 'ON' }
$destination = Join-Path $root $(if ($Distribution) { 'build-dist' } else { 'build' })
$static = if ($Distribution) { 'ON' } else { 'OFF' }
# Keep vcvars and CMake in one environment, including inherited x86 shells.
& "$PSScriptRoot/build.cmd" $vs $cuda $root $Configuration $gui $destination $static
if ($LASTEXITCODE) { throw 'Build failed.' }
