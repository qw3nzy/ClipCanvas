param(
    [switch]$NoBrowser,
    [string]$BindAddress = '127.0.0.1',
    [ValidateRange(1024,65535)][int]$Port = 8787
)
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$bindIp = $null
if (-not [Net.IPAddress]::TryParse($BindAddress, [ref]$bindIp)) { throw 'BindAddress must be a specific IP address.' }
if ($bindIp.Equals([Net.IPAddress]::Any) -or $bindIp.Equals([Net.IPAddress]::IPv6Any)) { throw 'Wildcard binding is disabled. Use the specific VPN IP of the PC.' }
$BindAddress = $bindIp.ToString()
$isRemote = -not [Net.IPAddress]::IsLoopback($bindIp)
if ($isRemote) {
    $ownedAddresses = @(Get-NetIPAddress -ErrorAction Stop | ForEach-Object IPAddress)
    if ($ownedAddresses -notcontains $BindAddress) { throw 'BindAddress is not assigned to this PC. Connect the VPN first and use its PC address.' }
}
$urlHost = if ($bindIp.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6) { '[' + $BindAddress + ']' } else { $BindAddress }
$baseUrl = 'http://' + $urlHost + ':' + $Port
$runtimePath = Join-Path $appRoot '.runtime'
$tempPath = Join-Path $runtimePath 'tmp'
New-Item -ItemType Directory -Force -Path $runtimePath,$tempPath | Out-Null

function Protect-PrivatePath([string]$Path, [bool]$Directory) {
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl = if ($Directory) { New-Object Security.AccessControl.DirectorySecurity } else { New-Object Security.AccessControl.FileSecurity }
    $acl.SetOwner($owner)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($owner,$system)) {
        if ($Directory) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        } else { $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','Allow') }
        $acl.AddAccessRule($rule)
    }
    $item = if ($Directory) { New-Object IO.DirectoryInfo($Path) } else { New-Object IO.FileInfo($Path) }
    if ($PSVersionTable.PSEdition -eq 'Desktop') { $item.SetAccessControl($acl) }
    else { [IO.FileSystemAclExtensions]::SetAccessControl($item,$acl) }
}
$startupLockName = 'Local\ClipCanvas-start-' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$startupLock = New-Object Threading.Mutex($false,$startupLockName)
try { $ownsStartupLock = $startupLock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsStartupLock = $true }
if (-not $ownsStartupLock) { $startupLock.Dispose(); throw 'Another ClipCanvas startup is in progress. Wait for it to finish.' }
try {
$privatePath = Join-Path $env:LOCALAPPDATA 'ClipCanvas'
New-Item -ItemType Directory -Force -Path $privatePath | Out-Null
Protect-PrivatePath $privatePath $true
$tokenPath = Join-Path $privatePath 'access-token.txt'
if (-not (Test-Path -LiteralPath $tokenPath)) {
    $tokenBytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($tokenBytes) } finally { $random.Dispose() }
    $newToken = [Convert]::ToBase64String($tokenBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    [IO.File]::WriteAllText($tokenPath, $newToken, [Text.Encoding]::ASCII)
}
Protect-PrivatePath $tokenPath $false
$accessToken = [IO.File]::ReadAllText($tokenPath).Trim()
if ($accessToken -notmatch '^[A-Za-z0-9_-]{43,128}$') { throw ('Invalid access token file: ' + $tokenPath) }

Add-Type -AssemblyName System.Net.Http
$httpHandler = New-Object System.Net.Http.HttpClientHandler
$httpHandler.UseProxy = $false
$client = New-Object System.Net.Http.HttpClient -ArgumentList $httpHandler
$client.Timeout = [TimeSpan]::FromSeconds(2)
$client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer',$accessToken)
function Get-EngineHealth { return ($client.GetStringAsync($baseUrl + '/api/health').GetAwaiter().GetResult() | ConvertFrom-Json) }
function Get-Sha256([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
function Test-Identity($Health, [string]$Instance) {
    if ($Health.app -ne 'ClipCanvas' -or $Health.version -ne '2.3.1' -or -not $Health.root -or -not $Health.instance_id) { return $false }
    if (-not [string]::Equals([IO.Path]::GetFullPath($Health.root).TrimEnd('\'),$appRoot,[StringComparison]::OrdinalIgnoreCase)) { return $false }
    if ($Instance -and $Health.instance_id -ne $Instance) { return $false }
    return $true
}
try {
    $pidPath = Join-Path $runtimePath 'engine.pid'
    $existingRecord = $null
    if (Test-Path -LiteralPath $pidPath) {
        $savedText = [IO.File]::ReadAllText($pidPath).Trim()
        if ($savedText -match '^\d+$') {
            $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$savedText" -ErrorAction SilentlyContinue
            if ($oldProcess -and $oldProcess.CommandLine -and $oldProcess.CommandLine.Contains((Join-Path $appRoot 'engine\launch.py'))) { throw 'An older ClipCanvas instance is running. Run Stop.ps1 before starting version 2.' }
        } else {
            try { $existingRecord = $savedText | ConvertFrom-Json } catch {}
            if ($existingRecord) {
                $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($existingRecord.pid)" -ErrorAction SilentlyContinue
                if ($oldProcess -and $oldProcess.CommandLine -and $oldProcess.CommandLine.Contains((Join-Path $appRoot 'engine\launch.py')) -and ($existingRecord.host -ne $BindAddress -or $existingRecord.port -ne $Port)) { throw 'This folder already has a running instance on another address/port. Stop it before changing the bind.' }
            }
        }
    }
    $existingHealth = $null
    try { $existingHealth = Get-EngineHealth } catch {}
    if ($existingHealth) {
        $knownInstance = if ($existingRecord) { $existingRecord.instance_id } else { '' }
        if (-not (Test-Identity $existingHealth $knownInstance)) { throw ('The address is occupied by another app or ClipCanvas version. Stop that instance or select another port: ' + $baseUrl) }
        if (-not $NoBrowser) { Start-Process $baseUrl }
        Write-Host ('ClipCanvas is already running: ' + $baseUrl)
        exit 0
    }
    Set-Location -LiteralPath $appRoot
    $pythonPath = $null
    $candidates = @(
        (Join-Path $runtimePath 'python\python.exe'),
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe')
    )
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $candidates += $pythonCommand.Source }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate) {
            & $candidate -c 'import sys,struct; sys.exit(0 if (3,12) <= sys.version_info[:2] <= (3,13) and struct.calcsize(chr(80))==8 else 1)'
            if ($LASTEXITCODE -eq 0) { $pythonPath = $candidate; break }
        }
    }
    $pipWheel = $null
    if (-not $pythonPath) {
        Write-Host 'Preparing verified portable Python (first launch only)...'
        $pythonDir = Join-Path $runtimePath 'python'
        $archivePath = Join-Path $runtimePath 'python.zip'
        Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $archivePath -UseBasicParsing
        $expectedPythonHash = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
        if ((Get-Sha256 $archivePath) -ne $expectedPythonHash) { throw 'Portable Python checksum mismatch; archive was not executed.' }
        Expand-Archive -LiteralPath $archivePath -DestinationPath $pythonDir -Force
        @('python312.zip','.','Lib\site-packages','import site') | Set-Content -LiteralPath (Join-Path $pythonDir 'python312._pth') -Encoding ASCII
        $pythonPath = Join-Path $pythonDir 'python.exe'
    }
    $env:TEMP = $tempPath
    $env:TMP = $tempPath
    $env:PYTHONPATH = Join-Path $runtimePath 'deps'
    $installPath = Join-Path $appRoot 'engine\install.py'
    & $pythonPath $installPath --check
    if ($LASTEXITCODE -ne 0) {
        & $pythonPath $installPath --record
        if ($LASTEXITCODE -ne 0) {
            & $pythonPath -c 'import pip' 2>$null
            if ($LASTEXITCODE -ne 0) {
                $pipWheel = Join-Path $runtimePath 'pip-25.3-py3-none-any.whl'
                Invoke-WebRequest -Uri 'https://files.pythonhosted.org/packages/44/3c/d717024885424591d5376220b5e836c2d5293ce2011523c9de23ff7bf068/pip-25.3-py3-none-any.whl' -OutFile $pipWheel -UseBasicParsing
                if ((Get-Sha256 $pipWheel) -ne '9655943313a94722b7774661c21049070f6bbb0a1516bf02f7c8d5d9201514cd') { throw 'pip checksum mismatch; package was not executed.' }
            }
            Write-Host 'Installing app-local video and speech dependencies...'
            $installArguments = @($installPath)
            if ($pipWheel) { $installArguments += @('--pip-wheel',$pipWheel) }
            & $pythonPath @installArguments
            if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check engine requirements and internet access.' }
        }
    }
    $env:CLIPCANVAS_HOST = $BindAddress
    $env:CLIPCANVAS_PORT = [string]$Port
    $env:CLIPCANVAS_TOKEN = $accessToken
    $env:CLIPCANVAS_APP_ROOT = $appRoot
    $env:CLIPCANVAS_INSTANCE_ID = [guid]::NewGuid().ToString()
    $instanceId = $env:CLIPCANVAS_INSTANCE_ID
    $launchPath = Join-Path $appRoot 'engine\launch.py'
    $process = Start-Process -FilePath $pythonPath -ArgumentList @(('"' + $launchPath + '"')) -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'engine.log') -RedirectStandardError (Join-Path $runtimePath 'engine-error.log')
    $online = $false
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Get-EngineHealth
            if ((Test-Identity $health $instanceId) -and $health.pid -eq $process.Id) { $online = $true; break }
        } catch {}
        $process.Refresh()
        if ($process.HasExited) { break }
    }
    if (-not $online) {
        if (-not $process.HasExited) { & (Join-Path $appRoot 'Stop.ps1') }
        throw ('The owned engine did not start. See ' + (Join-Path $runtimePath 'engine-error.log'))
    }
    if (-not $NoBrowser) { Start-Process $baseUrl }
    Write-Host ('ClipCanvas is ready: ' + $baseUrl)
    if ($isRemote) { Write-Host ('Remote access token is stored privately at: ' + $tokenPath) }
} finally {
    $client.Dispose()
    $httpHandler.Dispose()
    Remove-Item Env:\CLIPCANVAS_TOKEN -ErrorAction SilentlyContinue
}
} finally {
    $startupLock.ReleaseMutex()
    $startupLock.Dispose()
}
