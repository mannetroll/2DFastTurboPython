param([switch]$HeadlessOnly,[switch]$RestorePrevious,[string]$PreviousInstall)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$repo=Split-Path $root -Parent
$python=(Get-Command python.exe).Source
$vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs=& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
$dumpbin=Get-ChildItem "$vs/VC/Tools/MSVC/*/bin/Hostx64/x64/dumpbin.exe" | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
$output=Join-Path $root 'artifacts/packaging/clean-machine'
New-Item -ItemType Directory -Force $output | Out-Null
if ($RestorePrevious) {
    $previous=Get-Content -Raw "$output/location.json" | ConvertFrom-Json
    $previousPath=[IO.Path]::GetFullPath($(if ($PreviousInstall) { $PreviousInstall } else { $previous.install }))
    $tempPrefix=Join-Path ([IO.Path]::GetTempPath()) '2DFastTurbo-package-test-'
    if (-not $previousPath.StartsWith($tempPrefix,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $previousPath -Leaf) -ne 'application') { throw 'Refusing to modify an unrelated directory.' }
    & "$env:SystemRoot/System32/icacls.exe" $previousPath /reset
    if ($LASTEXITCODE) { throw 'Could not restore the previous temporary directory ACL.' }
    Write-Output "Restored access to previous temporary test folder: $previousPath"
    exit 0
}
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('2DFastTurbo-package-test-'+[Guid]::NewGuid().ToString('N'))
$install=Join-Path $testRoot 'application'
$working=Join-Path $testRoot 'unrelated-working-directory'
New-Item -ItemType Directory -Path $install,$working -Force | Out-Null
if ([IO.Path]::GetFullPath($testRoot).StartsWith([IO.Path]::GetFullPath($repo),[StringComparison]::OrdinalIgnoreCase)) { throw 'Clean test must be outside the repository.' }
$exe=Join-Path $install '2DFastTurbo.exe'
Copy-Item -LiteralPath (Join-Path $root 'dist/2DFastTurbo.exe') -Destination $exe
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().User
$readOnly=New-Object Security.AccessControl.DirectorySecurity
$readOnly.SetAccessRuleProtection($true,$false)
$rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')
$readOnly.AddAccessRule($rule)
try {
    Set-Acl -LiteralPath $install -AclObject $readOnly
    $denied=$false
    try { [IO.File]::WriteAllText((Join-Path $install 'write-probe.txt'),'probe') }
    catch [UnauthorizedAccessException] { $denied=$true }
    if (-not $denied) { throw 'Install-directory write restriction was not enforced.' }
    [pscustomobject]@{install=$install;cwd=$working;write_probe_denied=$denied;installation_contains_source_tree=$false;physical_clean_OS=$false} | ConvertTo-Json | Set-Content (Join-Path $output 'location.json')
    & $python "$PSScriptRoot/audit_runtime.py" --app $exe --output "$output/headless" --dumpbin $dumpbin --cwd $working --clean
    if ($LASTEXITCODE) { throw 'Clean headless dependency audit failed.' }
    if (-not $HeadlessOnly) {
        & $python "$PSScriptRoot/audit_runtime.py" --app $exe --output "$output/gui" --dumpbin $dumpbin --cwd $working --clean --gui
        if ($LASTEXITCODE) { throw 'Clean GUI dependency audit failed.' }
        $env:PATH="$env:SystemRoot/System32;$env:SystemRoot"
        Get-ChildItem Env: | Where-Object { $_.Name -match '^(CUDA|NVRTC|VSCMD|VSINSTALL|VCINSTALL|VCTOOLS|WINDOWSSDK)' -or $_.Name -in @('INCLUDE','LIB','LIBPATH') } | ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name,$null,'Process') }
        $env:CUDA_CACHE_DISABLE='1'
        Push-Location $working
        try {
            & $exe --live-check --resolution 128 --output "$output/gui-check.json" --export "$output/field.png" --capture "$output/window.png" | Out-File -Encoding utf8 "$output/gui-check.log"
            if ($LASTEXITCODE) { throw 'Clean native GUI controls failed.' }
            New-Item -ItemType Directory -Force "$output/colors" | Out-Null
            & $exe --gui-benchmark --resolution 32 --reynolds 50000 --until 0 --gui-warmup-seconds 0 --fields "$output/colors/fields.f32" --render-check "$output/colors" --output "$output/colors/run.json" | Out-File -Encoding utf8 "$output/colors/run.log"
            if ($LASTEXITCODE) { throw 'Embedded shader render run failed.' }
            & $python "$root/tests/render_reference.py" --directory "$output/colors"
            if ($LASTEXITCODE) { throw 'Embedded shader color comparison failed.' }
            & $exe --licenses | Out-File -Encoding utf8 "$output/embedded-licenses.txt"
            if ($LASTEXITCODE) { throw 'Cannot read embedded notices.' }
        } finally { Pop-Location }
    }
    $files=@(Get-ChildItem -LiteralPath $install -File)
    if ($files.Count -ne 1 -or $files[0].Name -ne '2DFastTurbo.exe') { throw 'Unexpected installation-directory files.' }
} finally {
    # Only the unique test directory's permissions were changed; restore them.
    # /reset restores inherited permissions on this newly created directory.
    # Unlike Set-Acl on PowerShell 5.1, it does not request SACL privileges.
    & "$env:SystemRoot/System32/icacls.exe" $install /reset
    if ($LASTEXITCODE) { throw 'Could not restore temporary directory permissions.' }
}
[pscustomobject]@{passed=$true;single_exe=$true;read_only_install_verified=$true;permissions_restored=$true;physical_clean_OS=$false;gui_checked=(-not $HeadlessOnly);sha256=(Get-FileHash $exe).Hash.ToLowerInvariant()} | ConvertTo-Json | Set-Content "$output/result.json"
Write-Output "PASS clean environment, outside repository, read-only installation: $install"
