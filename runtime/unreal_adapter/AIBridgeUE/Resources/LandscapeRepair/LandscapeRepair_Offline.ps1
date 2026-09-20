param([ValidateSet('inventory','scan','repair','normalize','verify','status')][string]$Action='status',[switch]$ScheduledWorker,[string]$ProjectFile='',[string]$Map='',[int]$ExpectedProxies=0,[string]$EditorExecutable='')
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($ProjectFile)){throw 'PROJECT_FILE_REQUIRED'}
$project=[IO.Path]::GetFullPath($ProjectFile)
$root=[IO.Path]::GetFullPath((Split-Path $project -Parent))
if([string]::IsNullOrWhiteSpace($Map)){throw 'MAP_REQUIRED'}
$map=$Map
$expectedProxyCount=if($ExpectedProxies -ge 0){$ExpectedProxies}else{0}
if([string]::IsNullOrWhiteSpace($EditorExecutable)){throw 'EDITOR_EXECUTABLE_REQUIRED'}
$editorExe=[IO.Path]::GetFullPath($EditorExecutable)
$engine=Split-Path (Split-Path (Split-Path $editorExe -Parent) -Parent) -Parent
if($Action -in @('scan','repair','normalize','verify') -and !$ScheduledWorker){
 $launch=@{Mode='launch';Action=$Action;ProjectFile=$project;Map=$map;ExpectedProxies=$expectedProxyCount;EditorExecutable=$editorExe}
 & (Join-Path $PSScriptRoot 'LandscapeRepair_ScheduledJob.ps1') @launch
 exit 0
}
$out=Join-Path $root 'Saved/AIBridgeLandscapeRepair/Offline'
[IO.Directory]::CreateDirectory($out)|Out-Null
function Write-Json($p,$v){[IO.Directory]::CreateDirectory((Split-Path $p -Parent))|Out-Null;[IO.File]::WriteAllText(($p+'.tmp'),($v|ConvertTo-Json -Depth 14),[Text.UTF8Encoding]::new($false));Move-Item ($p+'.tmp') $p -Force}
function Assert-Closed {
 $p=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -in @('UnrealEditor.exe','UnrealEditor-Cmd.exe') -and $_.CommandLine -and $_.CommandLine.Replace('/','\').Contains($project.Replace('/','\'))})
 if($p.Count){throw 'TARGET_UE_PROCESS_RUNNING: do not overlap offline jobs or the GUI editor'}
}
if($Action -eq 'status'){
 $diagnostic=[ordered]@{utc=[DateTime]::UtcNow.ToString('o')}
 # Keep status independent of WMI/CIM, which timed out in the preceding resume.
 $diagnostic.processes=@(Get-Process -Name UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue | ForEach-Object {[ordered]@{pid=$_.Id;name=$_.ProcessName;private_bytes=$_.PrivateMemorySize64;working_set=$_.WorkingSet64;executable=$_.Path}})
 Write-Json (Join-Path $out 'status_progress.json') @{phase='processes_read';diagnostic=$diagnostic}
 $jp=Join-Path $out 'last_job.json';if(Test-Path $jp){$jobState=Get-Content $jp -Raw|ConvertFrom-Json;$log=Join-Path $out ('Jobs/'+$jobState.job_id+'/worker.log');if(!(Test-Path $log)){$log=Join-Path $out ('Jobs/'+$jobState.job_id+'/stdout.log')};if(Test-Path $log){$diagnostic.log_bytes=(Get-Item $log).Length;$stream=[IO.File]::Open($log,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite);try{$count=[int][Math]::Min(65536L,$stream.Length);$buffer=New-Object byte[] $count;[void]$stream.Seek(-$count,[IO.SeekOrigin]::End);$read=$stream.Read($buffer,0,$count);$encoding=[Text.Encoding]::UTF8;if($read -ge 4 -and $buffer[1] -eq 0 -and $buffer[3] -eq 0){$encoding=[Text.Encoding]::Unicode};$tail=@(($encoding.GetString($buffer,0,$read) -split '
?
')|Select-Object -Last 128)}finally{$stream.Dispose()};$diagnostic.log_tail=@($tail|Select-Object -Last 16);$diagnostic.recent_log_errors=@($tail|Select-String -Pattern ': Error:|Fatal error:|Commandlet->Main|Failure -|Success -' | Select-Object -Last 24 | ForEach-Object {$_.Line})}}
 Write-Json (Join-Path $out 'status_progress.json') @{phase='log_tail_read';diagnostic=$diagnostic}
 $ip=Join-Path $out 'inventory_pointer.json';$dp=Join-Path $out 'disk_delta.json';if((Test-Path $ip) -and (Test-Path $dp)){
  $inv=Get-Content ((Get-Content $ip -Raw|ConvertFrom-Json).report) -Raw|ConvertFrom-Json;$delta=Get-Content $dp -Raw|ConvertFrom-Json
  $set=@{};foreach($pkg in $inv.landscape_packages){$set[('Content/'+$pkg.Substring(6)+'.uasset')]=$true}
  $terrain=@($delta.changed|Where-Object {$set.ContainsKey($_.relative.Replace('\','/'))});$diagnostic.changed_landscape_count=$terrain.Count;$diagnostic.changed_other_count=$delta.changed.Count-$terrain.Count;$diagnostic.changed_parent=@($terrain|Where-Object {$_.relative -like '*BD3WB5VDY56KJ3Z4LR7FD9.uasset'})
  $diagnostic.descriptor_parents=$inv.descriptor_parent_count;$diagnostic.descriptor_proxies=$inv.descriptor_proxy_count
 }
 Write-Json (Join-Path $out 'diagnostic.json') $diagnostic
 # Keep transport output bounded; raw log diagnostics remain in diagnostic.json.
 [pscustomobject]$diagnostic|Select-Object utc,processes,log_bytes,descriptor_parents,descriptor_proxies|ConvertTo-Json -Depth 5 -Compress
 $p=Join-Path $out 'last_job.json';if(Test-Path $p){$j=Get-Content $p -Raw|ConvertFrom-Json;$r=Join-Path $out ('Jobs/'+$j.job_id+'/report.json');if(Test-Path $r){$v=Get-Content $r -Raw|ConvertFrom-Json;[ordered]@{job_id=$j.job_id;action=$j.action;ok=$v.ok;error=$v.error;run_id=$v.run_id;last_step=$v.last_step;private_bytes=$v.private_bytes;peak_private_bytes=$v.peak_private_bytes;map_saved=$v.map_saved;scan=$v.scan|Select-Object ready_to_apply,missing_final_snapshots,recoverable_components,data_error,component_count,loaded_proxy_count,layer_policy_error}|ConvertTo-Json -Depth 5 -Compress}else{$j|ConvertTo-Json -Compress}}
 & (Join-Path $PSScriptRoot 'LandscapeRepair_ReadOfflineProgress.ps1') -ProjectFile $project
 exit 0
}
Assert-Closed
$job=[Guid]::NewGuid().ToString('N');$dir=Join-Path $out ('Jobs/'+$job);[IO.Directory]::CreateDirectory($dir)|Out-Null
$argv=@($project,$map,'-run=WorldPartitionBuilderCommandlet','-builder=AIBridgeUELandscapeOfflineBuilder','-AllowCommandletRendering','-RenderOffscreen','-unattended','-nosound','-NoSplash','-SCCProvider=None','-stdout','-FullStdOutLogOutput',('-RepairAction='+$Action),('-RepairJob='+$job),('-ExpectedProxies='+$expectedProxyCount),('-log='+$dir+'/worker.log'))
if($Action -in @('repair','normalize')){$argv+='-AuthorizeRepair'}
if($Action -eq 'verify'){$run=Get-Content (Join-Path $out 'last_saved_run.json') -Raw|ConvertFrom-Json;$argv+=('-RepairRunId='+$run.run_id)}
$state=@{job_id=$job;action=$Action;phase='launching';started_utc=[DateTime]::UtcNow.ToString('o')};Write-Json (Join-Path $out 'last_job.json') $state
$env:AIBRIDGE_UE_DISABLE_SESSION='1'
& (Join-Path $engine 'Binaries/Win64/UnrealEditor-Cmd.exe') @argv 2>&1 | Tee-Object (Join-Path $dir 'stdout.log')
$exit=$LASTEXITCODE;$report=Join-Path $dir 'report.json';$state.exit_code=$exit;$state.phase='exited';Write-Json (Join-Path $out 'last_job.json') $state
if(!(Test-Path $report)){throw ('OFFLINE_REPORT_MISSING: '+$report)}
$r=Get-Content $report -Raw|ConvertFrom-Json
if($r.ok -and $Action -eq 'inventory'){Write-Json (Join-Path $out 'inventory_pointer.json') @{report=$report;job_id=$job}}
[ordered]@{job_id=$job;action=$Action;exit_code=$exit;ok=$r.ok;error=$r.error;run_id=$r.run_id;map_saved=$r.map_saved;cold_reopen_verified=$r.cold_reopen_verified;private_bytes=$r.private_bytes;peak_private_bytes=$r.peak_private_bytes;report=$report}|ConvertTo-Json -Compress
if($exit -ne 0){exit $exit};if(!$r.ok){exit 1};exit 0
