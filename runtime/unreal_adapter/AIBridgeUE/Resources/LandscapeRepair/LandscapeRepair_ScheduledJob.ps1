param([ValidateSet('launch','worker','status','cleanup')][string]$Mode='launch',[ValidateSet('probe','scan','repair','normalize','verify')][string]$Action='probe',[string]$Ticket='',[int]$RequesterPid=0,[switch]$WaitForRequesterExit,[string]$ProjectFile='',[string]$Map='',[int]$ExpectedProxies=0,[string]$EditorExecutable='',[switch]$Reopen)
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($ProjectFile)){throw 'PROJECT_FILE_REQUIRED'}
$projectPath=[IO.Path]::GetFullPath($ProjectFile)
$root=[IO.Path]::GetFullPath((Split-Path $projectPath -Parent))
$base=Join-Path $root 'Saved/AIBridgeLandscapeRepair/Lifecycle'
[IO.Directory]::CreateDirectory((Join-Path $base 'Jobs'))|Out-Null
. (Join-Path $PSScriptRoot 'LandscapeRepair_ProcessAncestry.ps1')
$hasher=[Security.Cryptography.SHA256]::Create();try{$rootKey=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-','').Substring(0,12)}finally{$hasher.Dispose()}
$prefix='AIBridgeLandscape_'+$rootKey+'_'
function Write-State([string]$path,$value){$tmp=$path+'.'+[Guid]::NewGuid().ToString('N')+'.tmp';[IO.File]::WriteAllText($tmp,($value|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false));Move-Item -LiteralPath $tmp -Destination $path -Force}
function Read-State([string]$path){if(Test-Path -LiteralPath $path){return ([IO.File]::ReadAllText($path)|ConvertFrom-Json)};return $null}
function Open-Exclusive([string]$name){try{return [IO.File]::Open((Join-Path $base $name),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)}catch{throw ('LIFECYCLE_BUSY: '+$name)}}
function Assert-NoUE {
 $needle=$projectPath.Replace('/','\\')
 try{$p=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {$_.Name -in @('UnrealEditor.exe','UnrealEditor-Cmd.exe') -and $_.CommandLine -and $_.CommandLine.Replace('/','\\').Contains($needle)})}
 catch{$p=@(Get-Process -Name UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue)}
 if($p.Count){throw 'TARGET_UE_PROCESS_RUNNING'}
}
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
if($Mode -eq 'status'){
 $active=Read-State (Join-Path $base 'active.json');$state=$null;$taskState=$null
 if($active -and $active.ticket -match '^[a-f0-9]{32}$'){$state=Read-State (Join-Path $base ('Jobs/'+$active.ticket+'/state.json'));try{$taskState=$folder.GetTask($prefix+$active.ticket).State}catch{}}
 @{active=$active;state=$state;task_state=$taskState}|ConvertTo-Json -Depth 12 -Compress;return
}
if($Mode -eq 'cleanup'){
 if($Ticket -notmatch '^[a-f0-9]{32}$'){throw 'INVALID_LIFECYCLE_TICKET'}
 $state=Read-State (Join-Path $base ('Jobs/'+$Ticket+'/state.json'));if(!$state -or !$state.terminal){throw 'NONTERMINAL_TASK_CANNOT_BE_REMOVED'}
 $task=$null;try{$task=$folder.GetTask($prefix+$Ticket)}catch{}
 if($task){$until=[DateTime]::UtcNow.AddSeconds(5);while($task.State -eq 4 -and [DateTime]::UtcNow -lt $until){Start-Sleep -Milliseconds 200};if($task.State -eq 4){throw 'TASK_STILL_EXITING'};$folder.DeleteTask($prefix+$Ticket,0)}
 @{ok=$true;removed_task=$prefix+$Ticket}|ConvertTo-Json -Compress;return
}
if($Mode -eq 'launch'){
 $dispatch=Open-Exclusive 'dispatch.lock'
 try{
  if(!$WaitForRequesterExit){Assert-NoUE}
  $check=Open-Exclusive 'worker.lock';$check.Dispose()
  foreach($task in @($folder.GetTasks(1))){if($task.Name.StartsWith($prefix)){
   if($task.State -in @(2,4)){throw 'LIFECYCLE_BUSY: scheduled task is queued or running'}
   $prior=Read-State (Join-Path $base ('Jobs/'+$task.Name.Substring($prefix.Length)+'/state.json'))
   if(!$prior -or !$prior.terminal){throw 'LIFECYCLE_BUSY: previous task requires reconciliation'}
  }}
  $Ticket=[Guid]::NewGuid().ToString('N');$dir=Join-Path $base ('Jobs/'+$Ticket);[IO.Directory]::CreateDirectory($dir)|Out-Null
  $effectiveRequesterPid=if($RequesterPid -gt 0){$RequesterPid}else{$PID}
  $request=[ordered]@{schema='landscape_scheduled_worker/2';ticket=$Ticket;action=$Action;task=$prefix+$Ticket;project_root=$root;project_file=$projectPath;map=$Map;expected_proxies=$ExpectedProxies;editor_executable=$EditorExecutable;reopen=[bool]$Reopen;wait_for_requester_exit=[bool]$WaitForRequesterExit;requester_pid=$effectiveRequesterPid;requester_ancestry=@(Get-AIBridgeProcessAncestry $effectiveRequesterPid);created_utc=[DateTime]::UtcNow.ToString('o');launcher_sha256=(Get-FileHash $PSCommandPath -Algorithm SHA256).Hash;offline_sha256=(Get-FileHash (Join-Path $PSScriptRoot 'LandscapeRepair_Offline.ps1') -Algorithm SHA256).Hash}
  Write-State (Join-Path $dir 'request.json') $request
  Write-State (Join-Path $dir 'state.json') @{ticket=$Ticket;action=$Action;phase='launching';terminal=$false}
  Write-State (Join-Path $base 'active.json') @{ticket=$Ticket;action=$Action;task=$request.task;created_utc=$request.created_utc}
  $definition=$scheduler.NewTask(0)
  $definition.RegistrationInfo.Description='Explicit one-shot UE Landscape maintenance; no triggers. Native results remain in the project Saved directory.'
  $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  $definition.Principal.UserId=$user;$definition.Principal.LogonType=3;$definition.Principal.RunLevel=0
  $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.Hidden=$false
  $definition.Settings.DisallowStartIfOnBatteries=$false;$definition.Settings.StopIfGoingOnBatteries=$false
  $definition.Settings.ExecutionTimeLimit='PT0S';$definition.Settings.MultipleInstances=2
  # Native phases enforce cooperative deadlines. Never kill a save for a task timeout.
  $workerHost=@((Join-Path $PSHOME 'pwsh.exe'),(Join-Path $PSHOME 'powershell.exe'),'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')|Where-Object {Test-Path -LiteralPath $_}|Select-Object -First 1
  if(!$workerHost){throw 'POWERSHELL_WORKER_HOST_NOT_FOUND'}
  $exec=$definition.Actions.Create(0);$exec.Path=$workerHost
  $exec.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File '+[char]34+$PSCommandPath+[char]34+' -Mode worker -Action '+$Action+' -Ticket '+$Ticket+' -ProjectFile '+[char]34+$projectPath+[char]34
  $exec.WorkingDirectory=$root
  try{$registered=$folder.RegisterTaskDefinition($request.task,$definition,2,$user,$null,3,$null);[void]$registered.Run($null)}catch{Write-State (Join-Path $dir 'state.json') @{ticket=$Ticket;action=$Action;phase='launch_failed';terminal=$true;error=$_.Exception.Message};throw}
  @{accepted=$true;ticket=$Ticket;task=$request.task;action=$Action;mode='independent_demand_task';state_file=(Join-Path $dir 'state.json')}|ConvertTo-Json -Compress
 }finally{$dispatch.Dispose()}
 return
}
if($Ticket -notmatch '^[a-f0-9]{32}$'){throw 'INVALID_LIFECYCLE_TICKET'}
$dir=Join-Path $base ('Jobs/'+$Ticket);$request=Read-State (Join-Path $dir 'request.json')
if(!$request -or $request.action -ne $Action -or $request.project_root -ne $root){throw 'LIFECYCLE_REQUEST_MISMATCH'}
$state=[ordered]@{schema='landscape_scheduled_worker/1';ticket=$Ticket;action=$Action;phase='worker_starting';terminal=$false;pid=$PID;started_utc=[DateTime]::UtcNow.ToString('o');ancestry=@(Get-AIBridgeProcessAncestry $PID);ancestry_independent=$false;run_level=$null;trigger_count=$null}
$lease=$null
$workerTranscript=Join-Path $dir 'worker_transcript.txt'
Start-Transcript -Path $workerTranscript -Force | Out-Null
Write-Output ('Worker entry: '+$Ticket+' action='+$Action)
try{
 if($request.wait_for_requester_exit -and [int]$request.requester_pid -gt 0){
  while(Get-Process -Id ([int]$request.requester_pid) -ErrorAction SilentlyContinue){Start-Sleep -Seconds 1}
  Start-Sleep -Seconds 2
 }
 $projectPath=[IO.Path]::GetFullPath([string]$request.project_file)
 $lease=Open-Exclusive 'worker.lock';Assert-NoUE
 $own=$folder.GetTask($request.task);$state.run_level=$own.Definition.Principal.RunLevel;$state.trigger_count=$own.Definition.Triggers.Count
 if($state.run_level -ne 0 -or $state.trigger_count -ne 0){throw 'SCHEDULED_SECURITY_CONTRACT_MISMATCH'}
 $overlap=@($state.ancestry|Where-Object {$request.requester_ancestry -contains $_})
 if($overlap.Count){throw 'WORKER_IS_STILL_IN_REQUESTER_PROCESS_TREE'}
 $state.ancestry_independent=$true
 if((Get-FileHash $PSCommandPath -Algorithm SHA256).Hash -ne $request.launcher_sha256 -or (Get-FileHash (Join-Path $PSScriptRoot 'LandscapeRepair_Offline.ps1') -Algorithm SHA256).Hash -ne $request.offline_sha256){throw 'WORKER_SCRIPTS_CHANGED_AFTER_REQUEST'}
 $state.phase='running';Write-State (Join-Path $dir 'state.json') $state
 if($Action -eq 'probe'){Start-Sleep -Seconds 8;$state.phase='probe_complete';$state.ok=$true}
 else{
  $before=Read-State (Join-Path $root 'Saved/AIBridgeLandscapeRepair/Offline/last_job.json')
  $offlineArgs=@{Action=$Action;ScheduledWorker=$true;ProjectFile=[string]$request.project_file;Map=[string]$request.map;ExpectedProxies=[int]$request.expected_proxies;EditorExecutable=[string]$request.editor_executable}
  & (Join-Path $PSScriptRoot 'LandscapeRepair_Offline.ps1') @offlineArgs 2>&1 | Out-File (Join-Path $dir 'launcher.log') -Encoding utf8
  $state.engine_exit_code=$LASTEXITCODE
  $job=Read-State (Join-Path $root 'Saved/AIBridgeLandscapeRepair/Offline/last_job.json')
  if(!$job -or $job.action -ne $Action -or ($before -and $job.job_id -eq $before.job_id)){throw 'NATIVE_JOB_DID_NOT_START'}
  $state.job_id=$job.job_id
  $report=Read-State (Join-Path $root ('Saved/AIBridgeLandscapeRepair/Offline/Jobs/'+$job.job_id+'/report.json'))
  $state.native_ok=($report -and $report.ok -eq $true);$state.map_saved=($report -and $report.map_saved -eq $true);$state.run_id=$report.run_id
  $state.ok=$state.native_ok -and ($Action -notin @('repair','normalize') -or $state.map_saved)
  $state.phase=if($state.ok){'completed'}else{'native_failed_or_interrupted'}
  if(!$state.ok){$state.error=$report.error}
 }
}catch{$state.phase='failed';$state.ok=$false;$state.error=$_.Exception.Message}
finally{
 $state.terminal=$true;$state.finished_utc=[DateTime]::UtcNow.ToString('o');Write-State (Join-Path $dir 'state.json') $state
 if($lease){$lease.Dispose()}
 if($request -and $request.reopen -and $request.editor_executable -and (Test-Path -LiteralPath $request.editor_executable)){
  try{Start-Process -FilePath $request.editor_executable -ArgumentList @([string]$request.project_file) -WorkingDirectory $request.project_root}catch{}
 }
}
if(!$state.ok){exit 1}
