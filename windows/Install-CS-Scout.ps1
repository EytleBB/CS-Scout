[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ManagedVenvMarkerName = ".cs-scout-managed-venv"
$ManagedVenvMarkerContents = "CS-Scout managed virtual environment v1"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Assert-LastExitCode([string]$Action) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed (exit code $LASTEXITCODE)."
    }
}

function Assert-NonEmptyFile([string]$Path, [string]$RelativePath) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Release file is missing: $RelativePath. Extract the complete Windows release ZIP first."
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        throw "Release file is empty: $RelativePath. Download the release ZIP again."
    }
}

function Assert-PngFile([string]$Path, [string]$RelativePath) {
    Assert-NonEmptyFile $Path $RelativePath
    $expected = [byte[]](0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    $actual = New-Object byte[] 8
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($stream.Read($actual, 0, $actual.Length) -ne $actual.Length) {
            throw "Map image is truncated: $RelativePath"
        }
    }
    finally {
        $stream.Dispose()
    }
    for ($index = 0; $index -lt $expected.Length; $index++) {
        if ($actual[$index] -ne $expected[$index]) {
            throw "Map image is not a valid PNG: $RelativePath"
        }
    }
}

function Assert-MapMetadata([string]$Path, [string]$RelativePath) {
    Assert-NonEmptyFile $Path $RelativePath
    try {
        $metadata = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        throw "Map metadata is invalid JSON: $RelativePath"
    }
    if ($null -eq $metadata -or $null -eq $metadata.PSObject.Properties["transform"]) {
        throw "Map metadata has no transform: $RelativePath"
    }
    $transform = $metadata.transform
    foreach ($name in @("pos_x", "pos_y", "scale")) {
        if ($null -eq $transform -or $null -eq $transform.PSObject.Properties[$name]) {
            throw "Map metadata transform is missing $name`: $RelativePath"
        }
        if ($null -eq $transform.$name) {
            throw "Map metadata transform $name is null: $RelativePath"
        }
        try {
            $number = [double]$transform.$name
        }
        catch {
            throw "Map metadata transform $name is not numeric: $RelativePath"
        }
        if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
            throw "Map metadata transform $name is not finite: $RelativePath"
        }
        if ($name -eq "scale" -and $number -le 0) {
            throw "Map metadata scale must be positive: $RelativePath"
        }
    }
}

function Assert-WebpFile([string]$Path, [string]$RelativePath) {
    Assert-NonEmptyFile $Path $RelativePath
    $bytes = New-Object byte[] 12
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($stream.Read($bytes, 0, $bytes.Length) -ne $bytes.Length) {
            throw "WebP image is truncated: $RelativePath"
        }
    }
    finally {
        $stream.Dispose()
    }
    $ascii = [System.Text.Encoding]::ASCII
    if ($ascii.GetString($bytes, 0, 4) -ne "RIFF" -or $ascii.GetString($bytes, 8, 4) -ne "WEBP") {
        throw "WebP image signature is invalid: $RelativePath"
    }
}

function Assert-Package([string]$Root) {
    $runtimePython = @(
        "api_client.py", "combat.py", "config.py", "maps.py",
        "parse.py", "pipeline.py", "player_json.py", "web_server.py"
    )
    foreach ($fileName in $runtimePython) {
        $relativePath = "server\$fileName"
        Assert-NonEmptyFile (Join-Path $Root $relativePath) $relativePath
    }

    $runtimeAssets = @(
        "server\requirements-runtime.txt",
        "server\templates\index.html",
        "server\static\app.js",
        "server\static\replay.js"
    )
    foreach ($relativePath in $runtimeAssets) {
        Assert-NonEmptyFile (Join-Path $Root $relativePath) $relativePath
    }
    Assert-WebpFile `
        (Join-Path $Root "server\static\logo.webp") `
        "server\static\logo.webp"

    $replayIcons = @(
        "smokegrenade.svg", "flashbang.svg", "hegrenade.svg",
        "incgrenade.svg", "molotov_bottle.svg", "map_smoke.svg", "inferno.svg"
    )
    foreach ($fileName in $replayIcons) {
        $relativePath = "radar\icons\$fileName"
        $path = Join-Path $Root $relativePath
        Assert-NonEmptyFile $path $relativePath
        if ((Get-Content -LiteralPath $path -Raw) -notmatch "<svg\b") {
            throw "Replay icon is not an SVG: $relativePath"
        }
    }

    $maps = @(
        "de_ancient", "de_anubis", "de_dust2", "de_inferno",
        "de_mirage", "de_nuke", "de_overpass", "de_train"
    )
    foreach ($map in $maps) {
        $metaRelative = "server\data\maps\$map\meta.json"
        $pngRelative = "server\data\maps\$map\radar.png"
        Assert-MapMetadata (Join-Path $Root $metaRelative) $metaRelative
        Assert-PngFile (Join-Path $Root $pngRelative) $pngRelative
    }
}

function Get-PythonInfo([string]$Command, [string[]]$Prefix) {
    $probeArguments = @($Prefix) + @(
        "-c",
        "import base64,struct,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}'); print(struct.calcsize('P') * 8); print(base64.b64encode(sys.executable.encode('utf-8')).decode('ascii'))"
    )
    $probe = @()
    $probeExitCode = -1
    $savedErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $probe = @(& $Command @probeArguments 2>$null)
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

    $version = ([string]$probe[0]).Trim()
    $bitsText = ([string]$probe[1]).Trim()
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
    $bits = 0
    if (-not [int]::TryParse($bitsText, [ref]$bits)) {
        return $null
    }
    return [pscustomobject]@{
        Version = $version
        Bits = $bits
        Executable = $executable
    }
}

function Test-SupportedPythonInfo($Info) {
    return $null -ne $Info -and $Info.Version -in @("3.11", "3.12") -and $Info.Bits -eq 64
}

function Find-SupportedPython {
    $candidates = @(
        [pscustomobject]@{ Command = "py.exe"; Prefix = @("-3.12") },
        [pscustomobject]@{ Command = "py.exe"; Prefix = @("-3.11") },
        [pscustomobject]@{ Command = "python.exe"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
            continue
        }
        $info = Get-PythonInfo $candidate.Command @($candidate.Prefix)
        if (Test-SupportedPythonInfo $info) {
            return [pscustomobject]@{
                Command = $candidate.Command
                Prefix = @($candidate.Prefix)
                Version = $info.Version
                Bits = $info.Bits
                Executable = $info.Executable
            }
        }
    }

    throw @"
64-bit Python 3.11 or 3.12 was not found.
Install 64-bit Python from https://www.python.org/downloads/windows/ and run this installer again.
Keep the Python Launcher option enabled during Python installation.
"@
}

function Test-ManagedVenv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }
    $directory = Get-Item -LiteralPath $Path -Force
    if (($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $false
    }
    $markerPath = Join-Path $Path $ManagedVenvMarkerName
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
        return $false
    }
    $marker = Get-Item -LiteralPath $markerPath -Force
    if (($marker.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $false
    }
    $markerText = Get-Content -LiteralPath $markerPath -Raw
    return $null -ne $markerText -and ([string]$markerText).Trim() -eq $ManagedVenvMarkerContents
}

function Remove-SafeVenv([string]$Root, [string]$Path) {
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    $expected = [System.IO.Path]::GetFullPath((Join-Path $rootFull ".venv"))
    $actual = [System.IO.Path]::GetFullPath($Path)
    if (-not [string]::Equals($actual, $expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an unexpected environment path: $actual"
    }
    if (-not (Test-Path -LiteralPath $actual)) {
        return
    }

    if (-not (Test-ManagedVenv $actual)) {
        throw "Refusing to remove an unmanaged, linked, or unmarked .venv: $actual"
    }
    Remove-Item -LiteralPath $actual -Recurse -Force
}

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
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($existingRule in @($acl.Access)) {
            [void]$acl.RemoveAccessRuleAll($existingRule)
        }
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.SetAccessRule($rule)
        Set-Acl -LiteralPath $Path -AclObject $acl
    }
    catch {
        Write-Warning "Could not tighten the key file ACL. This does not prevent local use; keep the key private."
    }
}

function Read-ValidatedSecret([string]$Path) {
    $raw = Get-Content -LiteralPath $Path -Raw
    if ($null -eq $raw) {
        throw "The local access key file is empty: $Path"
    }
    $value = ([string]$raw).Trim()
    if ($value -notmatch "^[0-9a-f]{64}$") {
        throw "The existing access key is invalid: $Path. Rename it and run the installer again."
    }
    Protect-SecretFile $Path
    return $value
}

function Copy-SecretToClipboard([string]$Secret) {
    if (-not (Get-Command Set-Clipboard -ErrorAction SilentlyContinue)) {
        Write-Warning "Clipboard access is unavailable. Start-CS-Scout will try again."
        return
    }
    try {
        Set-Clipboard -Value $Secret
        Write-Host "The access key was copied to the clipboard." -ForegroundColor Green
    }
    catch {
        Write-Warning "Could not copy the access key. Use windows\Copy-Access-Key.cmd after installation."
    }
}

try {
    $principal = [System.Security.Principal.WindowsPrincipal]::new(
        [System.Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if ($principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Do not run this installer as Administrator. Close this window and double-click Install-CS-Scout.cmd normally."
    }

    $projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is not available for this Windows account."
    }
    $localState = Join-Path $env:LOCALAPPDATA "CS-Scout"
    $venvDir = [System.IO.Path]::GetFullPath((Join-Path $projectRoot ".venv"))
    $venvPython = Join-Path $venvDir "Scripts\python.exe"

    Write-Host "CS-Scout Windows installer" -ForegroundColor Green
    Write-Host "Project: $projectRoot"
    Write-Host "Local data: $localState"

    Write-Step "Validating the release package"
    Assert-Package $projectRoot

    Write-Step "Preparing local data directories"
    foreach ($directory in @(
        $localState,
        (Join-Path $localState "demos"),
        (Join-Path $localState "output")
    )) {
        [void](New-Item -ItemType Directory -Path $directory -Force)
    }

    $secretPath = Join-Path $localState "secret.key"
    if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
        $secret = Read-ValidatedSecret $secretPath
        Write-Host "Existing local access key preserved and protected."
    }
    else {
        $secret = New-RandomSecret
        [System.IO.File]::WriteAllText($secretPath, $secret, [System.Text.Encoding]::ASCII)
        Protect-SecretFile $secretPath
        Write-Host "A new local access key was generated."
    }

    Write-Step "Preparing 64-bit Python 3.11/3.12"
    $venvInfo = $null
    if (Test-Path -LiteralPath $venvDir) {
        $venvItem = Get-Item -LiteralPath $venvDir -Force
        $isReparsePoint = ($venvItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        if ($isReparsePoint) {
            throw "The existing .venv is a link or reparse point and will not be changed. Use a newly extracted release directory or handle it manually: $venvDir"
        }
        if (-not $venvItem.PSIsContainer) {
            throw "The existing .venv is not a directory and will not be changed. Use a newly extracted release directory or handle it manually: $venvDir"
        }
        if (-not (Test-ManagedVenv $venvDir)) {
            throw "The existing .venv has no valid CS-Scout managed marker and will not be deleted. Use a newly extracted release directory or handle it manually: $venvDir"
        }
        if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
            $venvInfo = Get-PythonInfo $venvPython @()
        }
        if (-not (Test-SupportedPythonInfo $venvInfo)) {
            Write-Warning "The managed .venv is incomplete or incompatible and will be rebuilt."
            Remove-SafeVenv $projectRoot $venvDir
            $venvInfo = $null
        }
    }

    if ($null -eq $venvInfo) {
        $python = Find-SupportedPython
        Write-Host "Using $($python.Bits)-bit Python $($python.Version): $($python.Executable)"
        $venvArguments = @($python.Prefix) + @("-m", "venv", $venvDir)
        try {
            [void](New-Item -ItemType Directory -Path $venvDir)
            $markerPath = Join-Path $venvDir $ManagedVenvMarkerName
            [System.IO.File]::WriteAllText(
                $markerPath,
                $ManagedVenvMarkerContents,
                [System.Text.Encoding]::ASCII
            )
            if (-not (Test-ManagedVenv $venvDir)) {
                throw "Could not create the CS-Scout managed .venv marker."
            }
            & $python.Command @venvArguments
            Assert-LastExitCode "Creating the Python environment"
            if (-not (Test-ManagedVenv $venvDir)) {
                throw "Python environment creation removed or damaged its CS-Scout marker."
            }
            $venvInfo = Get-PythonInfo $venvPython @()
            if (-not (Test-SupportedPythonInfo $venvInfo)) {
                throw "The new Python environment failed its 64-bit Python 3.11/3.12 check."
            }
        }
        catch {
            if (Test-ManagedVenv $venvDir) {
                Remove-SafeVenv $projectRoot $venvDir
            }
            else {
                Write-Warning "The failed .venv has no valid managed marker and was left untouched: $venvDir"
            }
            throw
        }
    }
    Write-Host "Virtual environment: $($venvInfo.Bits)-bit Python $($venvInfo.Version)"

    Write-Step "Installing pinned runtime dependencies"
    $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
    # Keep pip output valid even when the extraction path contains characters
    # outside the active Windows console code page.
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PIP_PROGRESS_BAR = "off"
    & $venvPython -m pip install --upgrade pip
    Assert-LastExitCode "Updating pip"
    & $venvPython -m pip install --requirement (Join-Path $projectRoot "server\requirements-runtime.txt")
    Assert-LastExitCode "Installing dependencies"

    Write-Step "Checking the installation"
    & $venvPython -m pip check
    Assert-LastExitCode "Checking installed dependencies"
    & $venvPython -c "import flask, requests, pandas, numpy, demoparser2; print('Runtime imports: OK')"
    Assert-LastExitCode "Importing runtime packages"

    Copy-SecretToClipboard $secret
    Write-Host "`nInstallation is ready." -ForegroundColor Green
    Write-Host "Double-click windows\Start-CS-Scout.cmd to start CS-Scout."
    exit 0
}
catch {
    Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
