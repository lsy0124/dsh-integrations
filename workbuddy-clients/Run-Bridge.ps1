$ErrorActionPreference='Continue'
$taskRuntime=Join-Path $PSScriptRoot 'runtime'
New-Item -ItemType Directory -Path $taskRuntime -Force|Out-Null
Set-Location -LiteralPath $PSScriptRoot
$taskNode=(Get-Command node -ErrorAction Stop).Source
& $taskNode (Join-Path $PSScriptRoot 'server.mjs') 1> (Join-Path $taskRuntime 'stdout.log') 2> (Join-Path $taskRuntime 'stderr.log')
exit $LASTEXITCODE
