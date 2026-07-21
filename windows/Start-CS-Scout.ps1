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

function Read-ValidatedStartupInfo(
    [string]$Path,
    [string]$ExpectedToken,
    [int]$ExpectedProcessId
) {
    $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "The local server returned empty startup information."
    }
    try {
        $document = $raw | ConvertFrom-Json
    }
    catch {
        throw "The local server returned invalid startup information."
    }
    foreach ($name in @("token", "pid", "parent_pid", "port")) {
        if ($null -eq $document.PSObject.Properties[$name]) {
            throw "The local server startup information is missing $name."
        }
    }
    if (([string]$document.token) -cne $ExpectedToken) {
        throw "The local server startup token did not match."
    }

    $reportedProcessId = 0
    $reportedParentProcessId = 0
    $hasValidProcessId = [int]::TryParse(
        [string]$document.pid,
        [ref]$reportedProcessId
    ) -and $reportedProcessId -gt 0
    $hasValidParentProcessId = [int]::TryParse(
        [string]$document.parent_pid,
        [ref]$reportedParentProcessId
    ) -and $reportedParentProcessId -gt 0
    $isExpectedProcess = $reportedProcessId -eq $ExpectedProcessId -or
        $reportedParentProcessId -eq $ExpectedProcessId
    if (-not $hasValidProcessId -or
        -not $hasValidParentProcessId -or
        -not $isExpectedProcess) {
        throw "The local server startup process ID did not match."
    }
    $reportedPort = 0
    if (-not [int]::TryParse([string]$document.port, [ref]$reportedPort) -or
        $reportedPort -lt 1 -or $reportedPort -gt 65535) {
        throw "The local server reported an invalid TCP port."
    }
    return [pscustomobject]@{
        Port = $reportedPort
        ProcessId = $reportedProcessId
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
$serverRuntimeProcess = $null
$instanceMutex = $null
$mutexAcquired = $false
$startupInfoPath = $null
$startupToken = $null
$baseUri = $null
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
        Write-Warning "Only $freeGB GB is free. Start with Normal mode and 1-2 Demos, or free more disk space before a larger analysis."
    }
    $startupInfoPath = Join-Path $localState (
        "startup-" + [System.Guid]::NewGuid().ToString("N") + ".json"
    )
    $startupToken = New-RandomSecret
    if (Test-Path -LiteralPath $startupInfoPath) {
        throw "Refusing to reuse an existing local startup-information file."
    }

    Write-Host "CS-Scout is starting on this computer only..." -ForegroundColor Cyan
    Write-Host "Data: $localState"

    $childEnvironment = [ordered]@{
        "CS_SCOUT_HOST" = "127.0.0.1"
        "CS_SCOUT_PORT" = "0"
        "CS_SCOUT_LOCAL_MODE" = "1"
        "CS_SCOUT_STARTUP_INFO" = $startupInfoPath
        "CS_SCOUT_STARTUP_TOKEN" = $startupToken
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
    $startupReceived = $false
    $deadline = [System.DateTime]::UtcNow.AddSeconds(30)
    while ([System.DateTime]::UtcNow -lt $deadline) {
        if ($serverProcess.HasExited) {
            break
        }

        if (-not $startupReceived) {
            if (-not (Test-Path -LiteralPath $startupInfoPath -PathType Leaf)) {
                Start-Sleep -Milliseconds 100
                continue
            }
            $startupInfo = Read-ValidatedStartupInfo `
                -Path $startupInfoPath `
                -ExpectedToken $startupToken `
                -ExpectedProcessId $serverProcess.Id
            $baseUri = "http://127.0.0.1:$($startupInfo.Port)"
            try {
                $serverRuntimeProcess = Get-Process `
                    -Id $startupInfo.ProcessId `
                    -ErrorAction Stop
            }
            catch {
                throw "The reported local server process is not running."
            }
            $startupReceived = $true
            Write-Host "Address: $baseUri"
            Remove-Item -LiteralPath $startupInfoPath -Force -ErrorAction SilentlyContinue
        }

        if ($serverProcess.HasExited) {
            break
        }
        try {
            $readyDocument = Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "$baseUri/readyz" `
                -TimeoutSec 1
            if ($serverProcess.HasExited) {
                break
            }
            if ($null -ne $readyDocument -and $readyDocument.status -eq "ready") {
                $statusResponse = Invoke-WebRequest `
                    -UseBasicParsing `
                    -Uri "$baseUri/api/status" `
                    -TimeoutSec 1
                if ($serverProcess.HasExited) {
                    break
                }
                if ($statusResponse.StatusCode -eq 200) {
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
            $serverProcess.WaitForExit()
            throw "CS-Scout exited during startup (exit code $($serverProcess.ExitCode))."
        }
        if (-not $startupReceived) {
            throw "CS-Scout did not report its local address within 30 seconds."
        }
        throw "CS-Scout did not pass readiness checks within 30 seconds."
    }
    Start-Sleep -Milliseconds 100
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) {
        throw "CS-Scout exited immediately after its startup checks."
    }

    try {
        Start-Process "$baseUri/"
    }
    catch {
        Write-Warning "Could not open the browser automatically. Open $baseUri manually."
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
            $savedErrorActionPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "SilentlyContinue"
                & taskkill.exe /PID $serverProcess.Id /T /F 2>$null | Out-Null
            }
            finally {
                $ErrorActionPreference = $savedErrorActionPreference
            }
        }
    }
    if ($null -ne $serverRuntimeProcess -and -not $serverRuntimeProcess.HasExited) {
        Stop-Process `
            -InputObject $serverRuntimeProcess `
            -Force `
            -ErrorAction SilentlyContinue
    }
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($null -ne $startupInfoPath -and
        (Test-Path -LiteralPath $startupInfoPath -PathType Leaf)) {
        Remove-Item -LiteralPath $startupInfoPath -Force -ErrorAction SilentlyContinue
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
