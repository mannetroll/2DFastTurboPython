param([Parameter(Mandatory)][string]$Destination)
$ErrorActionPreference = 'Stop'
$base = 'https://developer.download.nvidia.com/compute/cuda/redist/'
$manifest = Invoke-RestMethod ($base + 'redistrib_13.4.2.json')
New-Item -ItemType Directory -Force $Destination | Out-Null
$downloads = Join-Path $Destination 'downloads'
New-Item -ItemType Directory -Force $downloads | Out-Null
foreach ($component in @('cccl', 'cuda_crt', 'cuda_cudart', 'cuda_nvcc', 'cuda_nvrtc', 'libnvptxcompiler', 'libnvvm')) {
    $entry = $manifest.$component.'windows-x86_64'
    $archive = Join-Path $downloads ($component + '.zip')
    Invoke-WebRequest ($base + $entry.relative_path) -OutFile $archive
    if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) {
        throw "CUDA component checksum mismatch: $component"
    }
    $expanded = Join-Path $downloads $component
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded -Force
    $root = Get-ChildItem $expanded -Directory | Select-Object -First 1
    Copy-Item (Join-Path $root.FullName '*') -Destination $Destination -Recurse -Force
}
Invoke-WebRequest ($base + $manifest.cuda_cudart.license_path) -OutFile (Join-Path $Destination 'EULA.txt')
& (Join-Path $Destination 'bin/nvcc.exe') --version
if ($LASTEXITCODE) { throw 'CUDA compiler installation failed.' }
