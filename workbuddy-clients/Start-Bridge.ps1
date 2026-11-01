$ErrorActionPreference='Stop'
$taskRuntime=Join-Path $PSScriptRoot 'runtime'
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null
try {$taskHealth=Invoke-RestMethod 'http://127.0.0.1:18181/healthz' -TimeoutSec 2;if($taskHealth.app -eq 'workbuddy-clients'){Write-Output 'WorkBuddy bridge is already running.';exit 0}}catch{}
$taskHash=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot))).Replace('-','').Substring(0,8)
$taskName='WorkBuddy-Clients-Bridge-'+$taskHash
$taskExisting=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if(!$taskExisting){
    $taskAction=New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $PSScriptRoot 'Run-Bridge.ps1')+'"') -WorkingDirectory $PSScriptRoot
    $taskPrincipal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $taskOptions=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $taskPrincipal -Settings $taskOptions -Description 'On-demand local WorkBuddy bridge for ZCode and MiMo; no automatic logon trigger.'|Out-Null
}
Start-ScheduledTask -TaskName $taskName
for($taskAttempt=0;$taskAttempt -lt 60;$taskAttempt++){
    Start-Sleep -Seconds 1
    try {$taskHealth=Invoke-RestMethod 'http://127.0.0.1:18181/healthz' -TimeoutSec 1;if($taskHealth.app -eq 'workbuddy-clients'){Write-Output 'WorkBuddy bridge is ready.';exit 0}}catch{}
}
throw 'WorkBuddy bridge is still starting. Check runtime logs.'
