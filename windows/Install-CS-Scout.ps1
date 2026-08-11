[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ManagedVenvMarkerName = ".cs-scout-managed-venv"
$ManagedVenvMarkerContents = "CS-Scout managed virtual environment v1"
$ManagedPythonVersion = "3.12.10"
$ManagedPythonInstallerUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
$ManagedPythonInstallerSha256 = "67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB"

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
        "api_client.py", "combat.py", "config.py", "fivee_monitor.py", "maps.py",
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
    $perfectWorldRuntime = @(
        "perfectworld_experiment\__init__.py",
        "perfectworld_experiment\auto_scout.py",
        "perfectworld_experiment\current_match.py",
        "perfectworld_experiment\demo_io.py",
        "perfectworld_experiment\native_signer.py",
        "perfectworld_experiment\pipeline.py",
        "perfectworld_experiment\pwa_client.py",
        "perfectworld_experiment\pwa_protocol.py",
        "perfectworld_experiment\requirements.txt",
        "perfectworld_experiment\web_server.py",
        "perfectworld_experiment\native\PwaSwapBridge.cs"
    )
    foreach ($relativePath in $perfectWorldRuntime) {
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

function Get-SupportedPythonCandidate([string]$Command, [string[]]$Prefix) {
    if ([System.IO.Path]::IsPathRooted($Command)) {
        if (-not (Test-Path -LiteralPath $Command -PathType Leaf)) {
            return $null
        }
    }
    elseif (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        return $null
    }
    $info = Get-PythonInfo $Command @($Prefix)
    if (-not (Test-SupportedPythonInfo $info)) {
        return $null
    }
    return [pscustomobject]@{
        Command = $Command
        Prefix = @($Prefix)
        Version = $info.Version
        Bits = $info.Bits
        Executable = $info.Executable
    }
}

function Install-ManagedPython([string]$LocalState) {
    $runtimeRoot = Join-Path $LocalState "runtime"
    $pythonRoot = Join-Path $runtimeRoot "python-$ManagedPythonVersion"
    $pythonExe = Join-Path $pythonRoot "python.exe"
    $downloadRoot = Join-Path $LocalState "downloads"
    $installerPath = Join-Path $downloadRoot "python-$ManagedPythonVersion-amd64.exe"

    foreach ($directory in @($runtimeRoot, $downloadRoot)) {
        if (Test-Path -LiteralPath $directory) {
            $item = Get-Item -LiteralPath $directory -Force
            if (-not $item.PSIsContainer -or
                ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "The managed Python path is not an ordinary directory: $directory"
            }
        }
        else {
            [void](New-Item -ItemType Directory -Path $directory)
        }
    }

    $existing = Get-SupportedPythonCandidate $pythonExe @()
    if ($null -ne $existing) {
        return $existing
    }

    Write-Host "No compatible Python was found. Downloading the official Python $ManagedPythonVersion runtime..." -ForegroundColor Yellow
    $temporaryPath = Join-Path $downloadRoot ("python-download-" + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $downloaded = $false
        for ($attempt = 1; $attempt -le 3 -and -not $downloaded; $attempt++) {
            try {
                Invoke-WebRequest `
                    -Uri $ManagedPythonInstallerUrl `
                    -OutFile $temporaryPath `
                    -TimeoutSec 180 `
                    -UseBasicParsing
                $downloaded = $true
            }
            catch {
                if (Test-Path -LiteralPath $temporaryPath) {
                    Remove-Item -LiteralPath $temporaryPath -Force
                }
                if ($attempt -ge 3) {
                    throw "Could not download the official Python runtime from python.org. Check the network or proxy and run the installer again."
                }
                Start-Sleep -Seconds 2
            }
        }

        $actualHash = (Get-FileHash -LiteralPath $temporaryPath -Algorithm SHA256).Hash
        if (-not [string]::Equals(
            $actualHash,
            $ManagedPythonInstallerSha256,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "The downloaded Python runtime failed its SHA-256 integrity check."
        }
        Move-Item -LiteralPath $temporaryPath -Destination $installerPath -Force

        $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
        if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
            $null -eq $signature.SignerCertificate -or
            $signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
            throw "The downloaded Python runtime is not signed by the Python Software Foundation."
        }

        Write-Host "Installing the private Python $ManagedPythonVersion runtime for CS-Scout..."
        $installArguments = @(
            "/quiet",
            "InstallAllUsers=0",
            "TargetDir=`"$pythonRoot`"",
            "Include_pip=1",
            "Include_launcher=0",
            "AssociateFiles=0",
            "Shortcuts=0",
            "Include_doc=0",
            "Include_test=0",
            "Include_tcltk=0",
            "PrependPath=0",
            "AppendPath=0"
        )
        $installer = Start-Process `
            -FilePath $installerPath `
            -ArgumentList $installArguments `
            -Wait `
            -PassThru
        if ($installer.ExitCode -ne 0) {
            throw "The official Python runtime installer failed (exit code $($installer.ExitCode))."
        }
        $installed = Get-SupportedPythonCandidate $pythonExe @()
        if ($null -eq $installed -or $installed.Version -ne "3.12") {
            throw "The managed Python runtime did not pass its 64-bit Python 3.12 check."
        }
        Remove-Item -LiteralPath $installerPath -Force
        return $installed
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function Find-SupportedPython([string]$LocalState) {
    $managedPython = Join-Path $LocalState "runtime\python-$ManagedPythonVersion\python.exe"
    $candidates = @(
        [pscustomobject]@{ Command = $managedPython; Prefix = @() },
        [pscustomobject]@{ Command = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"); Prefix = @() },
        [pscustomobject]@{ Command = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"); Prefix = @() },
        [pscustomobject]@{ Command = "py.exe"; Prefix = @("-3.12") },
        [pscustomobject]@{ Command = "py.exe"; Prefix = @("-3.11") },
        [pscustomobject]@{ Command = "python.exe"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $python = Get-SupportedPythonCandidate $candidate.Command @($candidate.Prefix)
        if ($null -ne $python) {
            return $python
        }
    }
    return Install-ManagedPython $LocalState
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

    Write-Step "Preparing Python"
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
        $python = Find-SupportedPython $localState
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
    & $venvPython -m pip install --requirement (Join-Path $projectRoot "perfectworld_experiment\requirements.txt")
    Assert-LastExitCode "Installing Perfect World dependencies"

    Write-Step "Checking the installation"
    & $venvPython -m pip check
    Assert-LastExitCode "Checking installed dependencies"
    & $venvPython -c "import sys; sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2]); import cryptography, flask, requests, pandas, numpy, demoparser2, websocket, fivee_monitor, perfectworld_experiment.web_server; print('Runtime imports: OK')" $projectRoot (Join-Path $projectRoot "server")
    Assert-LastExitCode "Importing runtime packages"

    Write-Host "`nInstallation is ready." -ForegroundColor Green
    Write-Host "Double-click windows\Start-CS-Scout.cmd to start CS-Scout."
    exit 0
}
catch {
    Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
