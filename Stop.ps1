$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$pidPath = Join-Path $appRoot '.runtime\engine.pid'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host 'ClipCanvas is not running from this folder.'; exit 0 }
$rawRecord = [IO.File]::ReadAllText($pidPath).Trim()
$record = $null
if ($rawRecord -match '^\d+$') { $engineId = [int]$rawRecord } else { $record = $rawRecord | ConvertFrom-Json; $engineId = [int]$record.pid }
$launchPath = Join-Path $appRoot 'engine\launch.py'
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$engineId" -ErrorAction SilentlyContinue
$launchPattern = '(?i)(?:^|[\s"''])' + [regex]::Escape($launchPath) + '(?:$|[\s"''])'
if (-not $processInfo) { Remove-Item -LiteralPath $pidPath; Write-Host 'The saved engine has already stopped.'; exit 0 }
if (-not $processInfo.CommandLine -or $processInfo.CommandLine -notmatch $launchPattern) { throw 'Saved PID belongs to a different process. Nothing was stopped.' }
if ($record) {
    if (-not [string]::Equals([IO.Path]::GetFullPath($record.root).TrimEnd('\'),$appRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Engine identity points to another folder. Nothing was stopped.' }
    if (-not [string]::Equals($processInfo.ExecutablePath,$record.python_path,[StringComparison]::OrdinalIgnoreCase)) { throw 'Engine executable differs from its saved identity. Nothing was stopped.' }
    $recordStarted = if ($record.started_at -is [DateTime]) { $record.started_at.ToUniversalTime() }
        elseif ($record.started_at -is [DateTimeOffset]) { $record.started_at.UtcDateTime }
        else { [DateTimeOffset]::Parse([string]$record.started_at,[Globalization.CultureInfo]::InvariantCulture).UtcDateTime }
    if ([Math]::Abs(($processInfo.CreationDate.ToUniversalTime() - $recordStarted).TotalSeconds) -gt 5) { throw 'Saved PID has been reused. Nothing was stopped.' }
}
$owned = New-Object 'System.Collections.Generic.List[object]'
$owned.Add([pscustomobject]@{Id=$engineId; Created=$processInfo.CreationDate; Depth=0})
$allProcesses = @(Get-CimInstance Win32_Process)
for ($index=0; $index -lt $owned.Count; $index++) {
    $parent = $owned[$index]
    foreach ($child in ($allProcesses | Where-Object { $_.ParentProcessId -eq $parent.Id -and $_.CreationDate -ge $parent.Created })) {
        if (-not ($owned | Where-Object Id -eq $child.ProcessId)) { $owned.Add([pscustomobject]@{Id=[int]$child.ProcessId; Created=$child.CreationDate; Depth=$parent.Depth+1}) }
    }
}
if ($record) {
    $tokenPath = Join-Path $env:LOCALAPPDATA 'ClipCanvas\access-token.txt'
    if (Test-Path -LiteralPath $tokenPath) {
        $client = $null
        $handler = $null
        try {
            Add-Type -AssemblyName System.Net.Http
            $handler = New-Object System.Net.Http.HttpClientHandler
            $handler.UseProxy = $false
            $client = New-Object System.Net.Http.HttpClient -ArgumentList $handler
            $client.Timeout = [TimeSpan]::FromSeconds(3)
            $token = [IO.File]::ReadAllText($tokenPath).Trim()
            $client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer',$token)
            $hostText = if ($record.host -like '*:*') { '[' + $record.host + ']' } else { $record.host }
            $baseUrl = 'http://' + $hostText + ':' + $record.port
            $health = $client.GetStringAsync($baseUrl + '/api/health').GetAwaiter().GetResult() | ConvertFrom-Json
            if ($health.app -eq 'ClipCanvas' -and $health.instance_id -eq $record.instance_id -and $health.pid -eq $engineId) {
                $body = @{instance_id=$record.instance_id} | ConvertTo-Json -Compress
                $content = New-Object System.Net.Http.StringContent($body,[Text.Encoding]::UTF8,'application/json')
                try { $response = $client.PostAsync($baseUrl + '/api/shutdown',$content).GetAwaiter().GetResult(); $null = $response.EnsureSuccessStatusCode(); $response.Dispose() } finally { $content.Dispose() }
                $deadline = [DateTime]::UtcNow.AddSeconds(10)
                do {
                    $remaining = Get-Process -Id $engineId -ErrorAction SilentlyContinue
                    if (-not $remaining) { break }
                    Start-Sleep -Milliseconds 250
                } while ([DateTime]::UtcNow -lt $deadline)
            }
        } catch { Write-Host 'Graceful shutdown unavailable; stopping verified owned processes.' } finally { if ($client) { $client.Dispose() }; if ($handler) { $handler.Dispose() } }
    }
}
foreach ($item in ($owned | Sort-Object Depth -Descending)) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($item.Id)" -ErrorAction SilentlyContinue
    if ($current -and $current.CreationDate -eq $item.Created) { Stop-Process -Id $item.Id -ErrorAction SilentlyContinue }
}
if (Test-Path -LiteralPath $pidPath) {
    if ([IO.File]::ReadAllText($pidPath).Trim() -eq $rawRecord) { Remove-Item -LiteralPath $pidPath }
}
Write-Host 'ClipCanvas and its owned processing subprocesses have stopped.'
