[CmdletBinding()]
param(
    [string]$ProjectRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Assert-NonEmptyFile([string]$Path, [string]$RelativePath) {
    Assert-True (Test-Path -LiteralPath $Path -PathType Leaf) "Release package is missing: $RelativePath"
    Assert-True ((Get-Item -LiteralPath $Path).Length -gt 0) "Release package contains an empty file: $RelativePath"
}

function Assert-PngFile([string]$Path, [string]$RelativePath) {
    Assert-NonEmptyFile $Path $RelativePath
    $expected = [byte[]](0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    $actual = New-Object byte[] 8
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        Assert-True ($stream.Read($actual, 0, 8) -eq 8) "Truncated PNG: $RelativePath"
    }
    finally {
        $stream.Dispose()
    }
    for ($index = 0; $index -lt 8; $index++) {
        Assert-True ($actual[$index] -eq $expected[$index]) "Invalid PNG signature: $RelativePath"
    }
}

function Assert-MapMetadata([string]$Path, [string]$RelativePath) {
    Assert-NonEmptyFile $Path $RelativePath
    try {
        $metadata = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        throw "Invalid map metadata JSON: $RelativePath"
    }
    Assert-True ($null -ne $metadata) "Empty map metadata JSON: $RelativePath"
    Assert-True ($null -ne $metadata.PSObject.Properties["transform"]) "Map transform is missing: $RelativePath"
    foreach ($name in @("pos_x", "pos_y", "scale")) {
        Assert-True ($null -ne $metadata.transform.PSObject.Properties[$name]) "Map transform $name is missing: $RelativePath"
        Assert-True ($null -ne $metadata.transform.$name) "Map transform $name is null: $RelativePath"
        try {
            $number = [double]$metadata.transform.$name
        }
        catch {
            throw "Map transform $name is not numeric: $RelativePath"
        }
        Assert-True (-not [double]::IsNaN($number)) "Map transform $name is NaN: $RelativePath"
        Assert-True (-not [double]::IsInfinity($number)) "Map transform $name is infinite: $RelativePath"
        if ($name -eq "scale") {
            Assert-True ($number -gt 0) "Map scale is not positive: $RelativePath"
        }
    }
}

function Assert-WebpFile([string]$Path, [string]$RelativePath) {
    Assert-NonEmptyFile $Path $RelativePath
    $bytes = New-Object byte[] 12
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        Assert-True ($stream.Read($bytes, 0, 12) -eq 12) "Truncated WebP: $RelativePath"
    }
    finally {
        $stream.Dispose()
    }
    $ascii = [System.Text.Encoding]::ASCII
    Assert-True ($ascii.GetString($bytes, 0, 4) -eq "RIFF") "Invalid WebP RIFF signature: $RelativePath"
    Assert-True ($ascii.GetString($bytes, 8, 4) -eq "WEBP") "Invalid WebP signature: $RelativePath"
}

if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$root = [System.IO.Path]::GetFullPath($ProjectRoot)
$windowsRoot = Join-Path $root "windows"

Assert-True ($PSVersionTable.PSVersion.Major -ge 5) "Use Windows PowerShell 5.1 or newer to verify the package."

$requiredWindowsFiles = @(
    "Install-CS-Scout.ps1", "Start-CS-Scout.ps1",
    "Install-CS-Scout.cmd", "Start-CS-Scout.cmd",
    "README-PLAYER-ZH.md", "Verify-Windows-Package.ps1"
)
foreach ($fileName in $requiredWindowsFiles) {
    Assert-NonEmptyFile (Join-Path $windowsRoot $fileName) "windows\$fileName"
}

foreach ($fileName in @("Install-CS-Scout.cmd", "Start-CS-Scout.cmd")) {
    $wrapper = Get-Content -LiteralPath (Join-Path $windowsRoot $fileName) -Raw
    Assert-True ($wrapper -match '%~dp0') "$fileName must resolve files relative to itself."
    Assert-True ($wrapper -match '-NoProfile') "$fileName must not load the player's PowerShell profile."
    Assert-True ($wrapper -match '-ExecutionPolicy Bypass') "$fileName must launch its packaged script reliably."
}

foreach ($fileName in @("Install-CS-Scout.ps1", "Start-CS-Scout.ps1", "Verify-Windows-Package.ps1")) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $windowsRoot $fileName),
        [ref]$tokens,
        [ref]$errors
    )
    Assert-True ($errors.Count -eq 0) "PowerShell 5.1 syntax error in $fileName`: $($errors -join '; ')"
}

$runtimePython = @(
    "api_client.py", "combat.py", "config.py", "maps.py",
    "parse.py", "pipeline.py", "player_json.py", "web_server.py"
)
foreach ($fileName in $runtimePython) {
    $relativePath = "server\$fileName"
    Assert-NonEmptyFile (Join-Path $root $relativePath) $relativePath
}

foreach ($relativePath in @(
    "server\requirements-runtime.txt",
    "server\templates\index.html",
    "server\static\app.js",
    "server\static\replay.js"
)) {
    Assert-NonEmptyFile (Join-Path $root $relativePath) $relativePath
}
Assert-WebpFile (Join-Path $root "server\static\logo.webp") "server\static\logo.webp"

$replayScript = Get-Content -LiteralPath (Join-Path $root "server\static\replay.js") -Raw
$replayIcons = @(
    "smokegrenade.svg", "flashbang.svg", "hegrenade.svg",
    "incgrenade.svg", "molotov_bottle.svg", "map_smoke.svg", "inferno.svg"
)
foreach ($fileName in $replayIcons) {
    $relativePath = "radar\icons\$fileName"
    $path = Join-Path $root $relativePath
    Assert-NonEmptyFile $path $relativePath
    Assert-True ((Get-Content -LiteralPath $path -Raw) -match '<svg\b') "Replay icon is not SVG: $relativePath"
    Assert-True ($replayScript -match [regex]::Escape($fileName)) "Replay script does not reference expected icon: $fileName"
}

$maps = @(
    "de_ancient", "de_anubis", "de_dust2", "de_inferno",
    "de_mirage", "de_nuke", "de_overpass", "de_train"
)
foreach ($map in $maps) {
    $metaRelative = "server\data\maps\$map\meta.json"
    $pngRelative = "server\data\maps\$map\radar.png"
    Assert-MapMetadata (Join-Path $root $metaRelative) $metaRelative
    Assert-PngFile (Join-Path $root $pngRelative) $pngRelative
}

$installScript = Get-Content -LiteralPath (Join-Path $windowsRoot "Install-CS-Scout.ps1") -Raw
$startScript = Get-Content -LiteralPath (Join-Path $windowsRoot "Start-CS-Scout.ps1") -Raw
$configScript = Get-Content -LiteralPath (Join-Path $root "server\config.py") -Raw
$webServerScript = Get-Content -LiteralPath (Join-Path $root "server\web_server.py") -Raw

foreach ($fileName in $runtimePython) {
    Assert-True ($installScript -match [regex]::Escape($fileName)) "Installer does not validate runtime Python file: $fileName"
}
foreach ($relativePath in @(
    "server\requirements-runtime.txt",
    "server\templates\index.html",
    "server\static\app.js",
    "server\static\replay.js",
    "server\static\logo.webp"
)) {
    Assert-True ($installScript -match [regex]::Escape($relativePath)) "Installer does not validate runtime asset: $relativePath"
}
foreach ($fileName in $replayIcons) {
    Assert-True ($installScript -match [regex]::Escape($fileName)) "Installer does not validate replay icon: $fileName"
}
Assert-True ($installScript -match 'function Assert-PngFile') "Installer must validate PNG signatures."
Assert-True ($installScript -match 'function Assert-MapMetadata') "Installer must validate map transforms."
Assert-True ($installScript -match 'function Assert-WebpFile') "Installer must validate the logo WebP signature."

# Python and recoverable environment checks.
foreach ($script in @($installScript, $startScript)) {
    Assert-True ($script -match "struct\.calcsize\('P'\) \* 8") "Python checks must verify a 64-bit runtime."
    Assert-True ($script -match "base64\.b64encode\(sys\.executable\.encode\('utf-8'\)\)\.decode\('ascii'\)") "Python probes must emit executable paths as UTF-8 Base64 ASCII."
    Assert-True ($script -match 'Convert\]::FromBase64String') "PowerShell probes must decode the Base64 executable path."
    Assert-True ($script -match 'UTF8Encoding\]::new\(\$false, \$true\)') "PowerShell probes must use strict UTF-8 decoding."
    Assert-True ($script -match '3\.11') "Python checks must allow Python 3.11."
    Assert-True ($script -match '3\.12') "Python checks must allow Python 3.12."
    Assert-True (
        $script -match '(?s)function Get-PythonInfo.*?\$savedErrorActionPreference = \$ErrorActionPreference.*?try\s*\{.*?\$ErrorActionPreference = "SilentlyContinue".*?\$probeExitCode = \$LASTEXITCODE.*?\}\s*catch\s*\{.*?\$probeExitCode = -1.*?\}\s*finally\s*\{.*?\$ErrorActionPreference = \$savedErrorActionPreference.*?\}.*?if \(\$probeExitCode -ne 0'
    ) "Python probes must isolate native stderr/nonzero failures and restore ErrorActionPreference."
}
Assert-True ($installScript -match 'function Remove-SafeVenv') "Installer must have a bounded venv cleanup function."
Assert-True ($installScript -match 'OrdinalIgnoreCase') "Installer must verify the exact .venv cleanup target."
Assert-True ($installScript -match 'Remove-SafeVenv \$projectRoot \$venvDir') "Installer must rebuild invalid and partial environments safely."
Assert-True ($installScript -match 'if \(\$null -eq \$venvInfo\)') "Installer must handle a failed venv probe without calling Trim on null."
Assert-True ($installScript -match '\$env:PYTHONUTF8\s*=\s*"1"') "Installer must force UTF-8 Python output for non-ASCII extraction paths."
Assert-True ($installScript -match '\$env:PYTHONIOENCODING\s*=\s*"utf-8"') "Installer must force UTF-8 pip output for non-ASCII extraction paths."
Assert-True ($installScript -match '\.cs-scout-managed-venv') "Installer must use a dedicated managed-venv marker."
Assert-True ($installScript -match 'function Test-ManagedVenv') "Installer must validate its managed-venv marker."
Assert-True ($installScript -match 'if \(-not \(Test-ManagedVenv \$actual\)\)') "Venv cleanup must require the managed marker."
Assert-True ($installScript -match 'Refusing to remove an unmanaged, linked, or unmarked \.venv') "Venv cleanup must reject unknown directories."
Assert-True ($installScript -match '(?s)if \(\$isReparsePoint\).*?throw .*?will not be changed') "Installer must refuse a linked .venv without deleting it."
Assert-True ($installScript -match 'if \(-not \$venvItem\.PSIsContainer\)') "Installer must require .venv to be an ordinary directory."
Assert-True ($installScript -match 'has no valid CS-Scout managed marker and will not be deleted') "Installer must refuse an unmarked existing .venv."
$managedCheckIndex = $installScript.IndexOf('if (-not (Test-ManagedVenv $venvDir))')
$existingProbeIndex = $installScript.IndexOf('$venvInfo = Get-PythonInfo $venvPython @()')
Assert-True ($managedCheckIndex -ge 0 -and $existingProbeIndex -gt $managedCheckIndex) "Existing venv must have a valid marker before its Python is accepted."
$createDirIndex = $installScript.IndexOf('[void](New-Item -ItemType Directory -Path $venvDir)')
$createMarkerIndex = $installScript.IndexOf('$markerPath = Join-Path $venvDir $ManagedVenvMarkerName')
$createPythonIndex = $installScript.IndexOf('& $python.Command @venvArguments')
Assert-True ($createDirIndex -ge 0 -and $createMarkerIndex -gt $createDirIndex -and $createPythonIndex -gt $createMarkerIndex) "Installer must create the exact .venv and marker before invoking Python venv."
Assert-True ($installScript -match '(?s)catch\s*\{.*?if \(Test-ManagedVenv \$venvDir\)\s*\{.*?Remove-SafeVenv \$projectRoot \$venvDir') "Failed creation cleanup must be guarded by the managed marker."

# Single-instance and kernel-assigned local port controls.
Assert-True ($startScript -match 'System\.Threading\.Mutex') "Starter must use a named mutex."
Assert-True ($startScript -match 'Local\\CS-Scout-') "Mutex must be local to the current Windows session and user SID."
Assert-True ($startScript -match '\.WaitOne\(0, \$false\)') "Mutex acquisition must be non-blocking."
Assert-True ($startScript -match '\.ReleaseMutex\(\)') "Starter must release its mutex."
Assert-True ($startScript -match '\.Dispose\(\)') "Starter must dispose its mutex."
Assert-True ($startScript -notmatch 'Test-LocalPortOpen') "Starter must not race another process with a probe-then-bind port check."
Assert-True ($startScript -match '"CS_SCOUT_PORT" = "0"') "Starter must ask Windows to atomically assign a free port."
Assert-True ($startScript -match '"CS_SCOUT_STARTUP_INFO" = \$startupInfoPath') "Starter must request authenticated startup information."
Assert-True ($startScript -match '"CS_SCOUT_STARTUP_TOKEN" = \$startupToken') "Starter must pass a one-time startup token."
Assert-True ($startScript -match 'Read-ValidatedStartupInfo') "Starter must validate the server's selected port."
Assert-True ($startScript -match '\[string\]\$document\.token.*?-cne \$ExpectedToken') "Starter must compare the startup token case-sensitively."
Assert-True ($startScript -match '\$reportedProcessId -eq \$ExpectedProcessId -or') "Starter must accept a direct Python child PID."
Assert-True ($startScript -match '\$reportedParentProcessId -eq \$ExpectedProcessId') "Starter must accept a Python runtime whose launcher is its parent."
Assert-True ($startScript -match 'foreach \(\$name in @\("token", "pid", "parent_pid", "port"\)\)') "Starter must require all authenticated startup fields."
Assert-True ($startScript -match 'ProcessId = \$reportedProcessId') "Starter must retain the actual Python runtime PID for cleanup."
Assert-True ($startScript -match '\$reportedPort -lt 1 -or \$reportedPort -gt 65535') "Starter must reject an invalid reported port."
Assert-True ($startScript -match '\$baseUri = "http://127\.0\.0\.1:\$\(\$startupInfo\.Port\)"') "Starter must derive one loopback base URI from the reported port."
Assert-True ($startScript -notmatch 'http://127\.0\.0\.1:5000') "Starter must not retain a fixed local URL."
Assert-True ($startScript -match 'Remove-Item -LiteralPath \$startupInfoPath') "Starter must remove its one-time startup file."

# Child-only environment injection and restoration before browser launch.
Assert-True ($startScript -match '"CS_SCOUT_HOST" = "127\.0\.0\.1"') "Starter must force loopback binding."
Assert-True ($startScript -notmatch 'CS_SCOUT_HOST\s*=\s*"0\.0\.0\.0"') "Starter must never bind to all interfaces."
Assert-True ($startScript -match '"CS_SCOUT_LOCAL_MODE" = "1"') "Starter must enable loopback-only keyless analysis."
Assert-True ($startScript -notmatch 'CS_SCOUT_SECRET_KEY') "Starter must not create or inject a local analysis key."
Assert-True ($startScript -match '\$savedEnvironment') "Starter must save the original process environment."
Assert-True ($startScript -match 'EnvironmentVariableTarget\]::Process') "Environment changes must be process-local."
Assert-True ($startScript -match '(?s)try\s*\{.*?Start-Process.*?-FilePath \$venvPython.*?\}\s*finally\s*\{.*?\$savedEnvironment\[\$name\]') "Starter must restore the environment immediately after spawning Python."
$restoreIndex = $startScript.IndexOf('$savedEnvironment[$name],')
$browserIndex = $startScript.IndexOf('Start-Process "$baseUri/"')
Assert-True ($restoreIndex -ge 0 -and $browserIndex -gt $restoreIndex) "Browser must launch only after environment restoration."
Assert-True ($startScript -match '-Uri "\$baseUri/readyz"') "Readiness must use the reported base URI."
Assert-True ($startScript -match '-Uri "\$baseUri/api/status"') "Status probe must use the reported base URI."

# The Python entry point must bind port zero atomically and publish the actual port.
Assert-True ($configScript -match 'CS_SCOUT_PORT') "Server config must accept the starter's port override."
Assert-True ($webServerScript -match 'make_server') "Local entry point must use a server object that exposes the bound port."
Assert-True ($webServerScript -match '\.server_port') "Local entry point must report the kernel-assigned port."
Assert-True ($webServerScript -match 'CS_SCOUT_STARTUP_INFO') "Local entry point must support the startup-information file."
Assert-True ($webServerScript -match 'CS_SCOUT_STARTUP_TOKEN') "Local entry point must echo the one-time startup token."
Assert-True ($webServerScript -match 'os\.getppid\(\)') "Local entry point must report the Python launcher's parent PID."
Assert-True ($webServerScript -match 'os\.replace') "Startup information must be published atomically."
Assert-True ($webServerScript -match '\.serve_forever\(\)') "Local entry point must serve after publishing its port."

# Real deadline, JSON readiness, status, and process-liveness checks.
Assert-True ($startScript -match 'UtcNow\.AddSeconds\(30\)') "Starter must use a real 30-second deadline."
Assert-True ($startScript -match 'UtcNow -lt \$deadline') "Starter must stop polling at its deadline."
Assert-True ($startScript -match 'Invoke-RestMethod.*') "Starter must decode the readiness JSON."
Assert-True ($startScript -match '\$readyDocument\.status -eq "ready"') "Starter must require status=ready."
Assert-True ($startScript -match '\$statusResponse\.StatusCode -eq 200') "Starter must require a status HTTP 200."
Assert-True (($startScript | Select-String -Pattern '\$serverProcess\.HasExited' -AllMatches).Matches.Count -ge 5) "Starter must repeatedly check child-process liveness."

# Exit must remove only the process tree created by this starter.
Assert-True ($startScript -match '/PID \$serverProcess\.Id /T /F') "Starter must clean up its own parser process tree."
Assert-True ($startScript -match '(?s)Stop-Process\s+`\s*-InputObject \$serverRuntimeProcess') "Starter must directly stop the original runtime process object when taskkill is restricted."

Write-Host "Windows player package validation passed." -ForegroundColor Green
