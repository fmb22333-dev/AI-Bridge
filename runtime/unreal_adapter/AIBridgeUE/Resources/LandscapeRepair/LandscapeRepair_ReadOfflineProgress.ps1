param([string]$ProjectFile='')
# Read-only diagnostic probe. Does not tick, save, restart or terminate Unreal.
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($ProjectFile)){throw 'PROJECT_FILE_REQUIRED'}
$root=[IO.Path]::GetFullPath((Split-Path ([IO.Path]::GetFullPath($ProjectFile)) -Parent))
$base=Join-Path $root 'Saved/AIBridgeLandscapeRepair'
function Read-BoundedJson([string]$path){if(!(Test-Path -LiteralPath $path)){return $null};if((Get-Item -LiteralPath $path).Length -gt 5000000){throw 'REPORT_TOO_LARGE'};return ([IO.File]::ReadAllText($path)|ConvertFrom-Json)}
function Shallow($value){$result=[ordered]@{};if($null -eq $value){return $result};foreach($p in $value.PSObject.Properties){$v=$p.Value;if($null -eq $v -or $v -is [ValueType]){$result[$p.Name]=$v}elseif($v -is [string]){$result[$p.Name]=$v.Substring(0,[Math]::Min(400,$v.Length))}elseif($v -is [array]){$result[$p.Name+'_count']=$v.Count}else{$result[$p.Name+'_fields']=@($v.PSObject.Properties.Name).Count}};return $result}
$job=Read-BoundedJson (Join-Path $base 'Offline/last_job.json')
if(!$job -or $job.job_id -notmatch '^[A-Fa-f0-9]{32}$'){throw 'JOB_POINTER_INVALID'}
$reportPath=Join-Path $base ('Offline/Jobs/'+$job.job_id+'/report.json')
$report=Read-BoundedJson $reportPath
$run=$report.run_id;if(!$run){$run=$report.apply.run_id}
if(!$run){foreach($f in @(Get-ChildItem (Join-Path $base 'Offline/Runs') -Filter '*.json' -File -ErrorAction SilentlyContinue)){$origin=Read-BoundedJson $f.FullName;if($origin.job_id -eq $job.job_id){$run=$origin.run_id;break}}}
$journal=$null;if($run -match '^[A-Fa-f0-9]{32}$'){$journal=Read-BoundedJson (Join-Path $base ('Runs/'+$run+'/journal.json'))}
$metrics=@(Get-Process -Name UnrealEditor,UnrealEditor-Cmd,ShaderCompileWorker -ErrorAction SilentlyContinue|ForEach-Object{[ordered]@{pid=$_.Id;name=$_.ProcessName;cpu_seconds=[Math]::Round($_.CPU,2);private_bytes=$_.PrivateMemorySize64;working_set=$_.WorkingSet64}})
$out=[ordered]@{probe='offline_progress_readonly_v1';utc=[DateTime]::UtcNow.ToString('o');job_id=$job.job_id;report=Shallow $report;apply=Shallow $report.apply;finalize=Shallow $report.finalize;verify=Shallow $report.verify;run_id=$run;journal=Shallow $journal;processes=$metrics}
$out.backup_sample=@($journal.backups|Select-Object -First 1)
$crashReports=@();foreach($f in @(Get-ChildItem (Join-Path $root 'Saved/Crashes') -Filter 'CrashContext.runtime-xml' -Recurse -File -ErrorAction SilentlyContinue|Sort-Object LastWriteTimeUtc -Descending|Select-Object -First 8)){try{$t=[IO.File]::ReadAllText($f.FullName);$match=[regex]::Match($t,'<ProcessId>([0-9]+)</ProcessId>');$err=[regex]::Match($t,'<ErrorMessage>([\s\S]*?)</ErrorMessage>');$msg=[Net.WebUtility]::HtmlDecode($err.Groups[1].Value);$crashReports+=@([ordered]@{path=$f.FullName.Substring($root.Length+1);modified_utc=$f.LastWriteTimeUtc.ToString('o');pid=$match.Groups[1].Value;error=$msg.Substring(0,[Math]::Min(1200,$msg.Length))})}catch{$crashReports+=@([ordered]@{path=$f.Name;read_error=$_.Exception.Message})}}
$out.crash_reports=$crashReports
if($metrics.Count -eq 0 -and $journal){
 $disk=[ordered]@{checked=0;unchanged=0;changed=@();errors=@();algorithm='SHA256'}
 foreach($b in @($journal.backups)){try{$src=[IO.Path]::GetFullPath($b.source);$bak=[IO.Path]::GetFullPath($b.backup);if(!$src.StartsWith($root+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or !$bak.StartsWith($base+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'BACKUP_PATH_OUTSIDE_PROJECT'};if(!(Test-Path -LiteralPath $src) -or !(Test-Path -LiteralPath $bak)){throw 'SOURCE_OR_BACKUP_MISSING'};$sh=(Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash;$bh=(Get-FileHash -LiteralPath $bak -Algorithm SHA256).Hash;$disk.checked++;if($sh -eq $bh){$disk.unchanged++}else{$disk.changed+=@($b.package)}}catch{$disk.errors+=@([ordered]@{package=$b.package;error=$_.Exception.Message})}}
 $out.disk_comparison=$disk
}
# Keep frequently polled output operational and bounded; old crash evidence is retained separately.
$out.Remove('crash_reports');$out.Remove('backup_sample')
$out.compilation_progress=Read-BoundedJson (Join-Path $base ('Offline/Jobs/'+$job.job_id+'/progress.json'))
$active=Read-BoundedJson (Join-Path $base 'Lifecycle/active.json')
if($active -and $active.ticket -match '^[a-f0-9]{32}$'){$out.lifecycle=Read-BoundedJson (Join-Path $base ('Lifecycle/Jobs/'+$active.ticket+'/state.json'))}
$resources=Read-BoundedJson (Join-Path $base ('Offline/Jobs/'+$job.job_id+'/resources.json'))
$out.resource_readiness=Shallow $resources
if($resources){
 $out.resource_rebuilds=@($resources.textures|Where-Object {$_.rebuild_started}|Select-Object -First 8)
 $out.previous_warning_texture=@($resources.textures|Where-Object {$_.texture -like '*_508_4_2_0.Weightmap_4'}|Select-Object -First 1)
}
$comparisonPath=Join-Path $base ('Runs/'+$run+'/comparison.json')
if($run -and (Test-Path $comparisonPath)){
 $comparison=Read-BoundedJson $comparisonPath
 $changed=@($comparison.components|Where-Object {!$_.pixel_equivalent})
 $compact=@($changed|ForEach-Object {[ordered]@{component=($_.component -replace '^.*?_508_','');height_pixels=$_.height_changed_pixels;height_max=$_.height_maximum_delta;paint_planes=$_.paint_changed_planes;added_zero=$_.added_zero_planes;height_detail=$_.height;paint_detail=$_.paint}})
 $report=[ordered]@{summary=$comparison.summary;active_source_verified=$comparison.all_active_hashes_match_captured_source;changed=$compact}
 [IO.File]::WriteAllText((Join-Path $base 'Offline/pixel_difference_summary.json'),($report|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
 $out.pixel_comparison_summary=$comparison.summary
 $out.pixel_comparison_samples=@($compact|Select-Object -First 3)
}
$out|ConvertTo-Json -Depth 12 -Compress
