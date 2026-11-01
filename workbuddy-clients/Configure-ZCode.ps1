$ErrorActionPreference='Stop'
$taskClient=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime\client.json') -Raw | ConvertFrom-Json
$taskHeaders=@{Authorization=('Bearer '+$taskClient.clientKey)}
$taskBase='http://127.0.0.1:'+ $taskClient.port +'/v1'
$taskModels=Invoke-RestMethod ($taskBase+'/models') -Headers $taskHeaders -TimeoutSec 15
$taskIds=@($taskModels.data | ForEach-Object {$_.id})
if(!$taskIds.Count){throw 'WorkBuddy model catalog is empty.'}
$taskFile=Join-Path $env:USERPROFILE '.zcode\v2\provider_config.json'
$taskRaw=[IO.File]::ReadAllText($taskFile)
$taskConfig=$taskRaw | ConvertFrom-Json
if($taskConfig.schemaVersion -ne 1 -or !$taskConfig.config.providerConfigRules){throw 'Unsupported ZCode provider configuration schema.'}
$taskProviderId='workbuddy-local'
$taskOriginalRules=@($taskConfig.config.providerConfigRules.providerRules)
$taskOwnRule=[pscustomobject]@{providerId=$taskProviderId;providerName='WorkBuddy 本机账号';enabled=$true;config=[pscustomobject]@{group='standard-personal';access=[pscustomobject]@{type='api-key';apiKey=$taskClient.clientKey};api=[pscustomobject]@{type='openai-chat-completions';baseUrl=$taskBase};personalModelIds=$taskIds;modelOrder=$taskIds;visibility='visible'}}
$taskConfig.config.providerConfigRules.providerRules=@($taskOriginalRules | Where-Object {$_.providerId -ne $taskProviderId})+@($taskOwnRule)
$taskOrder=@($taskConfig.config.providerOrder)
if($taskProviderId -notin $taskOrder){$taskConfig.config.providerOrder=$taskOrder+@($taskProviderId)}
$taskResult=$taskConfig | ConvertTo-Json -Depth 100
$taskReadback=$taskResult|ConvertFrom-Json
foreach($taskRule in $taskOriginalRules | Where-Object {$_.providerId -ne $taskProviderId}){
    $taskNew=@($taskReadback.config.providerConfigRules.providerRules | Where-Object {$_.providerId -eq $taskRule.providerId})
    if($taskNew.Count -ne 1 -or ($taskNew[0]|ConvertTo-Json -Depth 100 -Compress) -ne ($taskRule|ConvertTo-Json -Depth 100 -Compress)){throw 'Existing provider changed unexpectedly; file not written.'}
}
$taskBackups=Join-Path (Split-Path $taskFile -Parent) 'local-backups'
New-Item -ItemType Directory -Path $taskBackups -Force | Out-Null
Copy-Item -LiteralPath $taskFile -Destination (Join-Path $taskBackups ('provider_config-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.json'))
$taskTemporary=$taskFile+'.workbuddy-'+[guid]::NewGuid().ToString('N')+'.tmp'
[IO.File]::WriteAllText($taskTemporary,$taskResult,(New-Object Text.UTF8Encoding($false)))
Move-Item -LiteralPath $taskTemporary -Destination $taskFile -Force
Write-Output ('Added WorkBuddy provider with '+$taskIds.Count+' models; existing providers preserved. Verify it in ZCode model settings.')
$taskClient=$null;$taskHeaders=$null;$taskConfig=$null;$taskReadback=$null;$taskResult=$null;$taskRaw=$null
