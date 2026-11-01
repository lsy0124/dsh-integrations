$ErrorActionPreference='Stop'
$taskConfig=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime\client.json') -Raw|ConvertFrom-Json
$taskStatus=Invoke-RestMethod ('http://127.0.0.1:'+$taskConfig.port+'/v1/accounts') -Headers @{Authorization=('Bearer '+$taskConfig.clientKey)} -TimeoutSec 5
$taskConfig=$null
$taskStatus.accounts | Select-Object name,enabled,state,@{Name='balance';Expression={if($null -ne $_.balance){[Math]::Round($_.balance,2)}}},uses | Format-Table -AutoSize
Write-Output ('Source: '+$taskStatus.source+'; mode: '+$taskStatus.mode+'; sync: '+$taskStatus.syncSeconds+'s; state: '+$taskStatus.sourceState)
