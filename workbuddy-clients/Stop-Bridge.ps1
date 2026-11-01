$ErrorActionPreference='Stop'
$taskHash=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot))).Replace('-','').Substring(0,8)
$taskName='WorkBuddy-Clients-Bridge-'+$taskHash
$taskScheduled=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if($taskScheduled){Stop-ScheduledTask -TaskName $taskName}
$taskStatus=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime\status.json') -Raw|ConvertFrom-Json
$taskProcess=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$taskStatus.pid)
if(!$taskProcess){Write-Output 'WorkBuddy bridge is already stopped.';exit 0}
$taskEntry=Join-Path $PSScriptRoot 'server.mjs'
if($taskProcess.Name -ne 'node.exe' -or !$taskProcess.CommandLine.Contains($taskEntry)){throw 'Process identity mismatch; nothing stopped.'}
Stop-Process -Id $taskStatus.pid -Force
Write-Output 'WorkBuddy bridge stopped.'
