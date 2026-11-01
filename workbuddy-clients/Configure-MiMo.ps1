$ErrorActionPreference='Stop'
$taskFile=Join-Path $env:USERPROFILE '.config\mimocode\mimocode.jsonc'
$taskRaw=[IO.File]::ReadAllText($taskFile)
$taskConfig=$taskRaw|ConvertFrom-Json
$taskProvider=$taskConfig.provider.'workbuddy-local'
if(!$taskProvider -or !$taskProvider.options -or $taskProvider.options.baseURL -ne 'http://127.0.0.1:18181/v1'){throw 'Save the WorkBuddy provider in MiMo first; no matching provider found.'}
$taskModels=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime\models.json') -Raw|ConvertFrom-Json
$taskOriginalProviders=$taskConfig.provider|ConvertTo-Json -Depth 100
if(!$taskProvider.models){$taskProvider|Add-Member -NotePropertyName models -NotePropertyValue ([pscustomobject]@{}) -Force}
foreach($taskModel in $taskModels){
    if(!$taskModel.id -or $taskModel.contextWindow -le 0 -or $taskModel.maxTokens -le 0){throw 'Invalid upstream model metadata; no file was written.'}
    $taskEntry=[pscustomobject]@{name=$taskModel.name;limit=[pscustomobject]@{context=[int]$taskModel.contextWindow;output=[int]$taskModel.maxTokens};modalities=[pscustomobject]@{input=@('text');output=@('text')}}
    $taskProvider.models|Add-Member -NotePropertyName $taskModel.id -NotePropertyValue $taskEntry -Force
}
$taskResult=$taskConfig|ConvertTo-Json -Depth 100
$taskCheck=$taskResult|ConvertFrom-Json
$taskBefore=$taskRaw|ConvertFrom-Json
foreach($taskOther in $taskBefore.provider.PSObject.Properties|Where-Object {$_.Name -ne 'workbuddy-local'}){
    if(($taskOther.Value|ConvertTo-Json -Depth 100 -Compress) -ne ($taskCheck.provider.($taskOther.Name)|ConvertTo-Json -Depth 100 -Compress)){throw 'Other provider changed; no file was written.'}
}
$taskBackupDir=Join-Path (Split-Path $taskFile -Parent) 'local-backups'
New-Item -ItemType Directory -Path $taskBackupDir -Force|Out-Null
Copy-Item -LiteralPath $taskFile -Destination (Join-Path $taskBackupDir ('mimocode-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.jsonc'))
$taskTemp=$taskFile+'.workbuddy-'+[guid]::NewGuid().ToString('N')+'.tmp'
[IO.File]::WriteAllText($taskTemp,$taskResult,(New-Object Text.UTF8Encoding($false)))
Move-Item -LiteralPath $taskTemp -Destination $taskFile -Force
Write-Output ('MiMo WorkBuddy models saved: '+@($taskModels).Count+'. Refresh model settings before using.')
