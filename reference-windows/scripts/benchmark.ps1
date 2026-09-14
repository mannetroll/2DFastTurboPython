param([int]$Runs=3,[double]$Seconds=5)
$ErrorActionPreference='Stop'
if ($Runs -lt 1 -or $Seconds -le 0) { throw 'Runs and Seconds must be positive.' }
$root=Split-Path $PSScriptRoot -Parent
$destination=Join-Path $root 'artifacts/benchmark'
New-Item -ItemType Directory -Force $destination | Out-Null
$records=@()
$binaryHash=(Get-FileHash (Join-Path $root 'build/2DFastTurbo.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
for ($run=1; $run -le $Runs; $run++) {
    foreach ($mode in @('headless','gui')) {
        $output=Join-Path $destination "$mode-$run.json"
        $log=Join-Path $destination "$mode-$run.log"
        $arguments=@('--resolution','1024','--reynolds','50000','--seed','2','--k0','4','--integrator','etdrk4','--operator','stress','--cfl','3','--wall-seconds',"$Seconds",'--output',$output)
        if ($mode -eq 'headless') { $arguments=@('--benchmark','--warmup','8','--batch','8')+$arguments }
        else {
            $arguments=@('--gui-benchmark','--gui-warmup-seconds','2','--display','vorticity','--palette','ember','--canvas-size','640')+$arguments
            if ($run -eq $Runs) { $arguments+=@('--capture',(Join-Path $destination 'windows-gui.png'),'--export',(Join-Path $destination 'windows-field.png')) }
        }
        & "$PSScriptRoot/run.ps1" @arguments *> $log
        if ($LASTEXITCODE) { throw "Benchmark failed: $mode $run; see $log" }
        $result=Get-Content -Raw $output | ConvertFrom-Json
        $records+= [pscustomobject]@{mode=$mode;run=$run;seconds=$result.seconds;simulation_time=$result.stats.simulation_time;R_turbo=$result.simtime_per_s;steps_per_s=$result.steps_per_s;presented_fps=$result.presented_fps;executable_sha256=$binaryHash}
        Write-Output ("{0} run {1}: R_turbo={2:N6}; {3:N2} steps/s; {4:N2} presents/s" -f $mode,$run,$result.simtime_per_s,$result.steps_per_s,$result.presented_fps)
    }
}
$records | Export-Csv -NoTypeInformation (Join-Path $destination 'runs.csv')
$records | ConvertTo-Json | Set-Content (Join-Path $destination 'runs.json')
