param([switch]$SkipBuild)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
if (-not $SkipBuild) { & "$PSScriptRoot/build.ps1" -Distribution }
$cache=Get-Content -Raw (Join-Path $root 'build-dist/CMakeCache.txt')
if ($cache -notmatch '(?m)^CMAKE_BUILD_TYPE:[^=]+=Release\r?$' -or $cache -notmatch '(?m)^TURBO_DISTRIBUTION:[^=]+=ON\r?$' -or $cache -notmatch '(?m)^TURBO_BUILD_GUI:[^=]+=ON\r?$') { throw 'Packaging requires the Release GUI distribution configuration.' }
$exe=Join-Path $root 'build-dist/2DFastTurbo.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Build the distribution configuration first.' }
$dist=Join-Path $root 'dist'
New-Item -ItemType Directory -Force $dist | Out-Null
Copy-Item -LiteralPath $exe -Destination (Join-Path $dist '2DFastTurbo.exe')
Copy-Item -LiteralPath (Join-Path $root 'packaging/README.txt') -Destination (Join-Path $dist 'README.txt')
$artifact=Get-Item (Join-Path $dist '2DFastTurbo.exe')
Write-Output "Distribution: $($artifact.FullName) ($($artifact.Length) bytes)"
Write-Output "SHA-256: $((Get-FileHash $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant())"
