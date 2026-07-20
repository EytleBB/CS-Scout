[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return ([System.BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

function Protect-SecretFile([string]$Path) {
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = New-Object System.Security.AccessControl.FileSecurity
        $acl.SetOwner($identity)
        $acl.SetAccessRuleProtection($true, $false)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $Path -AclObject $acl
    }
    catch {
        Write-Warning "Could not tighten the key file ACL. The key is still stored inside your LocalAppData profile."
    }
}

function Get-OrCreateSecret([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $newSecret = New-RandomSecret
        [System.IO.File]::WriteAllText($Path, $newSecret, [System.Text.Encoding]::ASCII)
    }
    $raw = Get-Content -LiteralPath $Path -Raw
    if ($null -eq $raw) {
        throw "The local access key file is empty: $Path"
    }
    $value = ([string]$raw).Trim()
    if ($value -notmatch "^[0-9a-f]{64}$") {
        throw "The local access key is invalid: $Path. Rename it and run the installer again."
    }
    Protect-SecretFile $Path
    return $value
}

function Copy-SecretToClipboard([string]$Secret) {
    if (-not (Get-Command Set-Clipboard -ErrorAction SilentlyContinue)) {
        Write-Warning "Clipboard access is unavailable. Run Copy-Access-Key.cmd to copy the key."
        return
    }
    try {
        Set-Clipboard -Value $Secret
        Write-Host "Access key copied. Paste it into the page with Ctrl+V." -ForegroundColor Green
    }
    catch {
        Write-Warning "Could not copy the access key. Run Copy-Access-Key.cmd and try again."
    }
}

function Get-PythonInfo([string]$Command) {
    $probe = @()
    $probeExitCode = -1
    $savedErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $probe = @(& $Command -c "import base64,struct,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}'); print(struct.calcsize('P') * 8); print(base64.b64encode(sys.executable.encode('utf-8')).decode('ascii'))" 2>$null)
        $probeExitCode = $LASTEXITCODE
    }
    catch {
        $probe = @()
        $probeExitCode = -1
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    if ($probeExitCode -ne 0 -or $probe.Count -lt 3) {
        return $null
    }
    $bits = 0
    if (-not [int]::TryParse(([string]$probe[1]).Trim(), [ref]$bits)) {
        return $null
    }
    try {
        $executableBytes = [System.Convert]::FromBase64String(([string]$probe[2]).Trim())
        $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
        $executable = $strictUtf8.GetString($executableBytes)
    }
    catch {
        return $null
    }
    if ([string]::IsNullOrWhiteSpace($executable)) {
        return $null
    }
    return [pscustomobject]@{
        Version = ([string]$probe[0]).Trim()
        Bits = $bits
        Executable = $executable
    }
}

function Test-LocalPortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $task.Wait(400)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Assert-PngFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-Item -LiteralPath $Path).Length -le 8) {
        throw "Map image is missing or empty: $Path"
    }
    $expected = [byte[]](0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    $actual = New-Object byte[] 8
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($stream.Read($actual, 0, $actual.Length) -ne $actual.Length) {
            throw "Map image is truncated: $Path"
        }
    }
    finally {
        $stream.Dispose()
    }
    for ($index = 0; $index -lt $expected.Length; $index++) {
        if ($actual[$index] -ne $expected[$index]) {
            throw "Map image is not a valid PNG: $Path"
        }
    }
}

function Assert-MapAssets([string]$MapsRoot) {
    $maps = @(
        "de_ancient", "de_anubis", "de_dust2", "de_inferno",
        "de_mirage", "de_nuke", "de_overpass", "de_train"
    )
    foreach ($map in $maps) {
        $mapRoot = Join-Path $MapsRoot $map
        $metaPath = Join-Path $mapRoot "meta.json"
        if (-not (Test-Path -LiteralPath $metaPath -PathType Leaf) -or (Get-Item -LiteralPath $metaPath).Length -le 0) {
            throw "Map metadata is missing or empty: $metaPath"
        }
        try {
            $metadata = Get-Content -LiteralPath $metaPath -Raw | ConvertFrom-Json
        }
        catch {
            throw "Map metadata is invalid JSON: $metaPath"
        }
        if ($null -eq $metadata -or $null -eq $metadata.PSObject.Properties["transform"]) {
            throw "Map metadata has no transform: $metaPath"
        }
        foreach ($name in @("pos_x", "pos_y", "scale")) {
            if ($null -eq $metadata.transform.PSObject.Properties[$name]) {
                throw "Map metadata transform is missing $name`: $metaPath"
            }
            if ($null -eq $metadata.transform.$name) {
                throw "Map metadata transform $name is null: $metaPath"
            }
            try {
                $number = [double]$metadata.transform.$name
            }
            catch {
                throw "Map metadata transform $name is not numeric: $metaPath"
            }
            if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
                throw "Map metadata transform $name is not finite: $metaPath"
            }
            if ($name -eq "scale" -and $number -le 0) {
                throw "Map metadata scale must be positive: $metaPath"
            }
        }
        Assert-PngFile (Join-Path $mapRoot "radar.png")
    }
}

$serverProcess = $null
$instanceMutex = $null
$mutexAcquired = $false
try {
    $windowsIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [System.Security.Principal.WindowsPrincipal]::new($windowsIdentity)
    if ($principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Do not run CS-Scout as Administrator. Start it by double-clicking Start-CS-Scout.cmd normally."
    }
    if ($null -eq $windowsIdentity.User) {
        throw "The current Windows account has no security identifier."
    }

    $mutexName = "Local\CS-Scout-$($windowsIdentity.User.Value)"
    $instanceMutex = [System.Threading.Mutex]::new($false, $mutexName)
    try {
        $mutexAcquired = $instanceMutex.WaitOne(0, $false)
    }
    catch [System.Threading.AbandonedMutexException] {
        $mutexAcquired = $true
    }
    if (-not $mutexAcquired) {
        throw "CS-Scout is already running for this Windows account. Use the existing browser and console window."
    }

    $projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is not available for this Windows account."
    }

    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $serverScript = Join-Path $projectRoot "server\web_server.py"
    $mapsRoot = Join-Path $projectRoot "server\data\maps"
    $localState = Join-Path $env:LOCALAPPDATA "CS-Scout"
    $demoDir = Join-Path $localState "demos"
    $outputDir = Join-Path $localState "output"
    $secretPath = Join-Path $localState "secret.key"

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "CS-Scout is not installed yet. Run windows\Install-CS-Scout.cmd first."
    }
    $venvInfo = Get-PythonInfo $venvPython
    if ($null -eq $venvInfo -or $venvInfo.Version -notin @("3.11", "3.12") -or $venvInfo.Bits -ne 64) {
        throw "The .venv is not a working 64-bit Python 3.11/3.12 environment. Run Install-CS-Scout.cmd to rebuild it."
    }
    if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) {
        throw "Server file is missing: $serverScript"
    }
    Assert-MapAssets $mapsRoot

    foreach ($directory in @($localState, $demoDir, $outputDir)) {
        [void](New-Item -ItemType Directory -Path $directory -Force)
    }
    $stateItem = Get-Item -LiteralPath $localState
    $stateDrive = Get-PSDrive -Name $stateItem.PSDrive.Name
    $freeGB = [math]::Round($stateDrive.Free / 1GB, 1)
    if ($freeGB -lt 9) {
        throw "Only $freeGB GB is free on the local data drive. Free at least 9 GB before starting CS-Scout."
    }
    if ($freeGB -lt 16) {
        Write-Warning "Only $freeGB GB is free. A larger Demo analysis may run out of disk space."
    }
    $secret = Get-OrCreateSecret $secretPath

    if (Test-LocalPortOpen 5000) {
        throw "Port 5000 is already in use. Close the other CS-Scout window or the program using that port, then try again."
    }

    Write-Host "CS-Scout is starting on this computer only..." -ForegroundColor Cyan
    Write-Host "Address: http://127.0.0.1:5000"
    Write-Host "Data: $localState"

    $childEnvironment = [ordered]@{
        "CS_SCOUT_HOST" = "127.0.0.1"
        "CS_SCOUT_SECRET_KEY" = $secret
        "CS_SCOUT_DEMO_DIR" = $demoDir
        "CS_SCOUT_OUTPUT_DIR" = $outputDir
        "CS_SCOUT_MAPS_DIR" = $mapsRoot
        "PYTHONUTF8" = "1"
        "PYTHONUNBUFFERED" = "1"
    }
    $savedEnvironment = @{}
    foreach ($name in $childEnvironment.Keys) {
        $savedEnvironment[$name] = [System.Environment]::GetEnvironmentVariable(
            $name,
            [System.EnvironmentVariableTarget]::Process
        )
    }
    try {
        foreach ($name in $childEnvironment.Keys) {
            [System.Environment]::SetEnvironmentVariable(
                $name,
                $childEnvironment[$name],
                [System.EnvironmentVariableTarget]::Process
            )
        }
        $serverProcess = Start-Process `
            -FilePath $venvPython `
            -ArgumentList "`"$serverScript`"" `
            -WorkingDirectory (Join-Path $projectRoot "server") `
            -NoNewWindow `
            -PassThru
    }
    finally {
        foreach ($name in $childEnvironment.Keys) {
            [System.Environment]::SetEnvironmentVariable(
                $name,
                $savedEnvironment[$name],
                [System.EnvironmentVariableTarget]::Process
            )
        }
    }

    if ($null -eq $serverProcess) {
        throw "The local Python server process was not created."
    }

    $ready = $false
    $deadline = [System.DateTime]::UtcNow.AddSeconds(30)
    while ([System.DateTime]::UtcNow -lt $deadline) {
        if ($serverProcess.HasExited) {
            break
        }
        try {
            $readyDocument = Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "http://127.0.0.1:5000/readyz" `
                -TimeoutSec 1
            if ($serverProcess.HasExited) {
                break
            }
            if ($null -ne $readyDocument -and $readyDocument.status -eq "ready") {
                $authResponse = Invoke-WebRequest `
                    -UseBasicParsing `
                    -Uri "http://127.0.0.1:5000/api/status" `
                    -Headers @{ Authorization = "Bearer $secret" } `
                    -TimeoutSec 1
                if ($serverProcess.HasExited) {
                    break
                }
                if ($authResponse.StatusCode -eq 200) {
                    $ready = $true
                    break
                }
            }
        }
        catch {
            # The server normally refuses connections during its first moments.
        }
        if (-not $serverProcess.HasExited -and [System.DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 250
        }
    }

    if (-not $ready) {
        if ($serverProcess.HasExited) {
            throw "CS-Scout exited during startup (exit code $($serverProcess.ExitCode))."
        }
        throw "CS-Scout did not pass readiness and access-key checks within 30 seconds."
    }
    if ($serverProcess.HasExited) {
        throw "CS-Scout exited immediately after its startup checks."
    }

    Copy-SecretToClipboard $secret
    try {
        Start-Process "http://127.0.0.1:5000/"
    }
    catch {
        Write-Warning "Could not open the browser automatically. Open http://127.0.0.1:5000 manually."
    }

    Write-Host "`nCS-Scout is running." -ForegroundColor Green
    Write-Host "Keep this window open. Press Ctrl+C only when no analysis is running."
    while (-not $serverProcess.HasExited) {
        Start-Sleep -Seconds 1
    }
    if ($serverProcess.ExitCode -ne 0) {
        throw "CS-Scout exited with code $($serverProcess.ExitCode)."
    }
    exit 0
}
catch {
    Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Write-Host "Stopping the local CS-Scout process..."
        if (Get-Command taskkill.exe -ErrorAction SilentlyContinue) {
            & taskkill.exe /PID $serverProcess.Id /T /F 2>$null | Out-Null
        }
        if (-not $serverProcess.HasExited) {
            Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if ($mutexAcquired -and $null -ne $instanceMutex) {
        try {
            $instanceMutex.ReleaseMutex()
        }
        catch {
            Write-Warning "Could not release the local instance lock cleanly."
        }
    }
    if ($null -ne $instanceMutex) {
        $instanceMutex.Dispose()
    }
}
