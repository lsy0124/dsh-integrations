$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Security
    $taskDirectory=if($env:WORKBUDDY_HELPER_DATA_DIR){$env:WORKBUDDY_HELPER_DATA_DIR}else{Join-Path $env:LOCALAPPDATA 'WorkBuddyHelper'}
    $taskBytes=[IO.File]::ReadAllBytes((Join-Path $taskDirectory 'state.bin'))
    if($taskBytes.Length -le 4 -or [Text.Encoding]::ASCII.GetString($taskBytes,0,4) -ne 'WBH1'){throw 'Unexpected state format'}
    $taskPlain=[Security.Cryptography.ProtectedData]::Unprotect($taskBytes[4..($taskBytes.Length-1)],$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskState=[Text.Encoding]::UTF8.GetString($taskPlain)|ConvertFrom-Json
    # Private pipe to the bridge process only. Never redirect this output to a file.
    [Console]::Write((ConvertTo-Json -InputObject @($taskState.accounts) -Depth 12 -Compress))
} catch {
    [Console]::Error.WriteLine('Cannot read Helper account store as the current Windows user.')
    exit 1
} finally {$taskPlain=$null;$taskBytes=$null;$taskState=$null}
