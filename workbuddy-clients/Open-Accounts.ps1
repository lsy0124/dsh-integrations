$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'Start-Bridge.ps1')
if($LASTEXITCODE -and $LASTEXITCODE -ne 0){throw 'Bridge startup failed.'}
Start-Process 'http://127.0.0.1:18181/accounts'
