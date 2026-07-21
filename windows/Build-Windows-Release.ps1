#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$Version = "2.0.2",
    [string]$ProjectRoot = "",
    [string]$OutputDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedReleaseVersion = "2.0.2"
$ExpectedMaps = @(
    "de_ancient", "de_anubis", "de_dust2", "de_inferno",
    "de_mirage", "de_nuke", "de_overpass", "de_train"
)
$ExpectedIconFiles = @(
    "flash.svg", "flashbang.svg", "he.svg", "hegrenade.svg",
    "incgrenade.svg", "inferno.svg", "map_smoke.svg", "molotov.svg",
    "molotov_bottle.svg", "smoke.svg", "smokegrenade.svg"
)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false, $true)
$PathComparison = [System.StringComparison]::OrdinalIgnoreCase

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Get-NormalizedFullPath([string]$Path, [string]$BasePath = "") {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "A required path was empty."
    }

    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        if ([string]::IsNullOrWhiteSpace($BasePath)) {
            $Path = Join-Path (Get-Location).Path $Path
        }
        else {
            $Path = Join-Path $BasePath $Path
        }
    }
    return [System.IO.Path]::GetFullPath($Path)
}

function Get-ContainedRelativePath([string]$BasePath, [string]$FullPath) {
    $base = [System.IO.Path]::GetFullPath($BasePath).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $candidate = [System.IO.Path]::GetFullPath($FullPath)
    $prefix = $base + [System.IO.Path]::DirectorySeparatorChar

    if (-not $candidate.StartsWith($prefix, $PathComparison)) {
        throw "Path escapes the allowed root: $candidate"
    }
    return $candidate.Substring($prefix.Length).Replace("\", "/")
}

function Assert-NoReparsePoint([string]$RootPath, [string]$TargetPath) {
    $root = [System.IO.Path]::GetFullPath($RootPath).TrimEnd("\", "/")
    $current = [System.IO.Path]::GetFullPath($TargetPath).TrimEnd("\", "/")

    while ($true) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release sources may not contain links or junctions: $current"
        }
        if ([string]::Equals($current, $root, $PathComparison)) {
            break
        }

        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ([string]::IsNullOrEmpty($parent) -or
            (-not ($current.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, $PathComparison)))) {
            throw "Release source is outside the project root: $TargetPath"
        }
        $current = $parent.TrimEnd("\", "/")
    }
}

function Read-Utf8Text([string]$Path) {
    try {
        return [System.IO.File]::ReadAllText($Path, $Utf8NoBom)
    }
    catch {
        throw "Text file is not valid UTF-8: $Path"
    }
}

function Assert-PngFile([string]$Path) {
    $expected = [byte[]](137, 80, 78, 71, 13, 10, 26, 10)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        Assert-True ($stream.Length -gt $expected.Length) "PNG file is empty or truncated: $Path"
        foreach ($byte in $expected) {
            if ($stream.ReadByte() -ne $byte) {
                throw "File does not have a valid PNG signature: $Path"
            }
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Assert-WebPFile([string]$Path) {
    $bytes = New-Object byte[] 12
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        Assert-True ($stream.Length -gt 12) "WebP file is empty or truncated: $Path"
        $read = $stream.Read($bytes, 0, $bytes.Length)
        Assert-True ($read -eq 12) "Could not read the WebP header: $Path"
        $riff = [System.Text.Encoding]::ASCII.GetString($bytes, 0, 4)
        $webp = [System.Text.Encoding]::ASCII.GetString($bytes, 8, 4)
        Assert-True ($riff -ceq "RIFF" -and $webp -ceq "WEBP") "File does not have a valid WebP signature: $Path"
    }
    finally {
        $stream.Dispose()
    }
}

function Convert-ToFiniteDouble([object]$Value, [string]$Description) {
    $number = 0.0
    $text = [System.Convert]::ToString($Value, [System.Globalization.CultureInfo]::InvariantCulture)
    $ok = [double]::TryParse(
        $text,
        [System.Globalization.NumberStyles]::Float,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$number
    )
    if (-not $ok -or [double]::IsNaN($number) -or [double]::IsInfinity($number)) {
        throw "$Description must be a finite number."
    }
    return $number
}

function Assert-MapMetadata([string]$Path, [string]$MapName) {
    try {
        $metadata = (Read-Utf8Text $Path) | ConvertFrom-Json
    }
    catch {
        throw "Invalid map metadata JSON for $MapName`: $($_.Exception.Message)"
    }

    Assert-True ($null -ne $metadata) "Map metadata is empty for $MapName."
    Assert-True ($null -ne $metadata.PSObject.Properties["transform"]) "Map metadata has no transform for $MapName."
    $transform = $metadata.transform
    foreach ($propertyName in @("pos_x", "pos_y", "scale")) {
        Assert-True ($null -ne $transform.PSObject.Properties[$propertyName]) "Map metadata has no $propertyName for $MapName."
    }
    [void](Convert-ToFiniteDouble $transform.pos_x "$MapName transform.pos_x")
    [void](Convert-ToFiniteDouble $transform.pos_y "$MapName transform.pos_y")
    $scale = Convert-ToFiniteDouble $transform.scale "$MapName transform.scale"
    Assert-True ($scale -gt 0) "Map scale must be positive for $MapName."
}

function Assert-RuntimeRequirements([string]$Path) {
    $requiredPackages = @(
        "flask", "requests", "urllib3", "pandas", "numpy", "demoparser2", "gunicorn"
    )
    $found = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)
    $lines = [System.IO.File]::ReadAllLines($Path, $Utf8NoBom)

    foreach ($rawLine in $lines) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $match = [regex]::Match(
            $line,
            '^([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)(?:\s*;\s*.+)?$'
        )
        if (-not $match.Success) {
            throw "Runtime dependency is not strictly pinned with ==: $line"
        }
        $packageName = $match.Groups[1].Value
        if ($packageName -notin $requiredPackages) {
            throw "Unexpected runtime dependency in the Windows release: $packageName"
        }
        if (-not $found.Add($packageName)) {
            throw "Duplicate runtime dependency: $packageName"
        }
    }

    foreach ($packageName in $requiredPackages) {
        Assert-True ($found.Contains($packageName)) "Missing pinned runtime dependency: $packageName"
    }
    Assert-True ($found.Count -eq $requiredPackages.Count) "Runtime dependency allowlist does not match."

    $gunicornLines = @($lines | Where-Object { $_ -match '^\s*gunicorn==' })
    Assert-True ($gunicornLines.Count -eq 1) "Expected exactly one pinned gunicorn dependency."
    Assert-True ($gunicornLines[0] -match 'platform_system\s*!=\s*["'']Windows["'']') `
        "gunicorn must remain excluded on Windows by an environment marker."
}

function Assert-AllowedRelativePath([string]$RelativePath) {
    $path = $RelativePath.Replace("\", "/")
    $forbiddenSegmentPattern = '(?i)(^|/)(?:\.git|\.github|\.venv|venv|__pycache__|tests?|deploy|tools|docs|demos_opponents|output|\.pytest[^/]*)(?:/|$)'
    if ($path -match $forbiddenSegmentPattern) {
        throw "Forbidden directory entered the Windows release: $path"
    }
    if ($path -match '(?i)(^|/)(?:\.env(?:\..*)?|secret\.key|credentials\.json|\.demo_index\.json|AGENTS\.md|CLAUDE\.md|replay_test\.html|server(?:_run)?\.log)$') {
        throw "Forbidden file entered the Windows release: $path"
    }
    if ($path -match '(?i)\.(?:zip|7z|rar|tar|gz|bz2|xz)$') {
        throw "Nested archive entered the Windows release: $path"
    }
}

function Copy-ReleaseFile(
    [string]$SourceRoot,
    [string]$DestinationRoot,
    [string]$RelativePath,
    [System.Collections.Generic.HashSet[string]]$ExpectedRelativePaths
) {
    if ([System.IO.Path]::IsPathRooted($RelativePath) -or $RelativePath -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "Unsafe release allowlist path: $RelativePath"
    }

    $normalized = $RelativePath.Replace("\", "/").TrimStart("/")
    Assert-AllowedRelativePath $normalized
    if (-not $ExpectedRelativePaths.Add($normalized)) {
        throw "Duplicate release allowlist path: $normalized"
    }

    $sourcePath = Get-NormalizedFullPath $RelativePath $SourceRoot
    [void](Get-ContainedRelativePath $SourceRoot $sourcePath)
    Assert-True (Test-Path -LiteralPath $sourcePath -PathType Leaf) "Required release file is missing: $RelativePath"
    Assert-NoReparsePoint $SourceRoot $sourcePath
    $sourceItem = Get-Item -LiteralPath $sourcePath -Force
    Assert-True ($sourceItem.Length -gt 0) "Required release file is empty: $RelativePath"

    $destinationPath = Get-NormalizedFullPath $RelativePath $DestinationRoot
    [void](Get-ContainedRelativePath $DestinationRoot $destinationPath)
    $destinationParent = [System.IO.Path]::GetDirectoryName($destinationPath)
    [void][System.IO.Directory]::CreateDirectory($destinationParent)
    [System.IO.File]::Copy($sourcePath, $destinationPath, $false)
}

function Test-IsTextReleaseFile([string]$Path) {
    $extension = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
    if ($extension -in @(".py", ".js", ".html", ".ps1", ".cmd", ".md", ".txt", ".json", ".svg")) {
        return $true
    }
    $name = [System.IO.Path]::GetFileName($Path)
    return $name -in @("LICENSE", "THIRD_PARTY_NOTICES")
}

function Assert-NoEmbeddedSecrets([string]$PackageRoot) {
    $patterns = @(
        [pscustomobject]@{ Name = "private key"; Pattern = '-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----' },
        [pscustomobject]@{ Name = "GitHub token"; Pattern = '(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})' },
        [pscustomobject]@{ Name = "OpenAI-style API key"; Pattern = '(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{20,}' },
        [pscustomobject]@{ Name = "AWS access key"; Pattern = '(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])' },
        [pscustomobject]@{ Name = "Google API key"; Pattern = '(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])' },
        [pscustomobject]@{ Name = "Slack token"; Pattern = '(?<![A-Za-z0-9-])xox(?:b|p|a|r|s)-[A-Za-z0-9-]{20,}' },
        [pscustomobject]@{ Name = "JWT"; Pattern = '(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])' },
        [pscustomobject]@{ Name = "credential-bearing URL"; Pattern = 'https?://[^/\s:@]+:[^/\s@]+@' },
        [pscustomobject]@{ Name = "literal CS-Scout secret"; Pattern = '(?im)^\s*(?:\$env:)?CS_SCOUT_SECRET_KEY\s*[:=]\s*["''][0-9a-f]{64}["'']\s*$' },
        [pscustomobject]@{ Name = "cloud account key"; Pattern = '(?i)AccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}' }
    )

    foreach ($file in Get-ChildItem -LiteralPath $PackageRoot -Recurse -File -Force) {
        if (-not (Test-IsTextReleaseFile $file.FullName)) {
            continue
        }
        $content = Read-Utf8Text $file.FullName
        foreach ($secretPattern in $patterns) {
            if ([regex]::IsMatch($content, $secretPattern.Pattern)) {
                $relativePath = Get-ContainedRelativePath $PackageRoot $file.FullName
                throw "Possible $($secretPattern.Name) found in release file: $relativePath"
            }
        }
    }
}

function Assert-StagedPackage(
    [string]$PackageRoot,
    [System.Collections.Generic.HashSet[string]]$ExpectedRelativePaths
) {
    foreach ($directory in Get-ChildItem -LiteralPath $PackageRoot -Recurse -Directory -Force) {
        if (($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Link or junction found in staged release: $($directory.FullName)"
        }
    }

    $actual = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($file in Get-ChildItem -LiteralPath $PackageRoot -Recurse -File -Force) {
        if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Linked file found in staged release: $($file.FullName)"
        }
        $relativePath = Get-ContainedRelativePath $PackageRoot $file.FullName
        Assert-AllowedRelativePath $relativePath
        if (-not $ExpectedRelativePaths.Contains($relativePath)) {
            throw "Unexpected file in staged release: $relativePath"
        }
        if (-not $actual.Add($relativePath)) {
            throw "Duplicate path in staged release: $relativePath"
        }
    }

    foreach ($expectedPath in $ExpectedRelativePaths) {
        if (-not $actual.Contains($expectedPath)) {
            throw "Allowlisted file was not staged: $expectedPath"
        }
    }
    Assert-True ($actual.Count -eq $ExpectedRelativePaths.Count) "Staged release file count does not match the allowlist."
    Assert-NoEmbeddedSecrets $PackageRoot
}

function Assert-StagedRuntimeIntegrity([string]$PackageRoot, [string]$ArchiveName) {
    $readmePath = Join-Path $PackageRoot "README.md"
    $playerReadmePath = Join-Path $PackageRoot "windows\README-PLAYER-ZH.md"
    $indexPath = Join-Path $PackageRoot "server\templates\index.html"
    Assert-True ((Read-Utf8Text $readmePath) -match '(?m)^# CS-Scout 2\.0\r?$') `
        "Staged README.md does not identify the 2.0 release line."
    Assert-True ((Read-Utf8Text $playerReadmePath).Contains($ArchiveName)) `
        "Staged Windows player guide does not name the expected release archive."
    Assert-True ((Read-Utf8Text $indexPath).Contains("<title>CS-Scout 2.0</title>")) `
        "Staged Web UI title does not identify CS-Scout 2.0."

    Assert-RuntimeRequirements (Join-Path $PackageRoot "server\requirements-runtime.txt")
    Assert-WebPFile (Join-Path $PackageRoot "server\static\logo.webp")
    foreach ($mapName in $ExpectedMaps) {
        Assert-MapMetadata `
            (Join-Path $PackageRoot "server\data\maps\$mapName\meta.json") `
            $mapName
        Assert-PngFile (Join-Path $PackageRoot "server\data\maps\$mapName\radar.png")
    }
    foreach ($iconFile in $ExpectedIconFiles) {
        $iconPath = Join-Path $PackageRoot "radar\icons\$iconFile"
        Assert-True ((Read-Utf8Text $iconPath) -match '(?i)<svg\b') `
            "Staged icon is not recognizable SVG: $iconFile"
    }

    $powerShellFiles = @(Get-ChildItem -LiteralPath (Join-Path $PackageRoot "windows") -Filter "*.ps1" -File)
    Assert-True ($powerShellFiles.Count -eq 3) "The staged Windows workflow must contain exactly three player-facing PowerShell scripts."
    foreach ($scriptFile in $powerShellFiles) {
        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile(
            $scriptFile.FullName,
            [ref]$tokens,
            [ref]$parseErrors
        )
        if ($parseErrors.Count -ne 0) {
            throw "PowerShell syntax error in staged file $($scriptFile.Name): $($parseErrors -join '; ')"
        }
    }
}

function Assert-ZipArchive(
    [string]$ZipPath,
    [string]$PackageDirectoryName,
    [string]$PackageRoot,
    [System.Collections.Generic.HashSet[string]]$ExpectedRelativePaths
) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $expectedEntries = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)
    $expectedLengths = @{}
    foreach ($relativePath in $ExpectedRelativePaths) {
        $entryName = "$PackageDirectoryName/$relativePath"
        [void]$expectedEntries.Add($entryName)
        $localPath = Join-Path $PackageRoot $relativePath.Replace("/", "\")
        $expectedLengths[$entryName] = (Get-Item -LiteralPath $localPath -Force).Length
    }

    $seen = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName.Replace("\", "/")
            if (-not $name -or $name.EndsWith("/")) {
                throw "Directory or empty ZIP entry is not allowed: $name"
            }
            if ($name.StartsWith("/") -or $name.StartsWith("\") -or $name -match '^[A-Za-z]:') {
                throw "Rooted ZIP entry is not allowed: $name"
            }
            $parts = $name.Split('/')
            if ($parts | Where-Object { $_ -eq "" -or $_ -eq "." -or $_ -eq ".." }) {
                throw "ZIP traversal or ambiguous entry detected: $name"
            }
            if ($parts[0] -cne $PackageDirectoryName) {
                throw "ZIP entry is outside the release root directory: $name"
            }
            if (-not $expectedEntries.Contains($name)) {
                throw "Unexpected ZIP entry: $name"
            }
            if (-not $seen.Add($name)) {
                throw "Duplicate ZIP entry: $name"
            }
            if ($entry.Length -ne $expectedLengths[$name]) {
                throw "ZIP entry size mismatch: $name"
            }
        }
    }
    finally {
        $archive.Dispose()
    }

    foreach ($expectedEntry in $expectedEntries) {
        if (-not $seen.Contains($expectedEntry)) {
            throw "ZIP entry is missing: $expectedEntry"
        }
    }
    Assert-True ($seen.Count -eq $expectedEntries.Count) "ZIP entry count does not match the allowlist."
}

function Remove-OwnedTemporaryFile([string]$Path, [string]$ExpectedDirectory, [string]$BuildId) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return
    }
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $parent = [System.IO.Path]::GetDirectoryName($fullPath)
    $leaf = [System.IO.Path]::GetFileName($fullPath)
    if (-not [string]::Equals($parent, [System.IO.Path]::GetFullPath($ExpectedDirectory), $PathComparison) -or
        $leaf.IndexOf($BuildId, [System.StringComparison]::Ordinal) -lt 0) {
        throw "Refusing to remove an unowned temporary file: $fullPath"
    }
    [System.IO.File]::Delete($fullPath)
}

function Remove-OwnedStagingDirectory([string]$Path, [string]$ExpectedParent, [string]$BuildId) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return
    }
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd("\", "/")
    $parent = [System.IO.Path]::GetDirectoryName($fullPath)
    $leaf = [System.IO.Path]::GetFileName($fullPath)
    $expectedLeaf = "cs-scout-windows-release-$BuildId"
    if (-not [string]::Equals($parent, [System.IO.Path]::GetFullPath($ExpectedParent).TrimEnd("\", "/"), $PathComparison) -or
        -not [string]::Equals($leaf, $expectedLeaf, [System.StringComparison]::Ordinal)) {
        throw "Refusing to remove an unowned staging directory: $fullPath"
    }
    Remove-Item -LiteralPath $fullPath -Recurse -Force
}

function Publish-ArtifactSet([array]$Artifacts, [string]$BuildId) {
    Assert-True ($Artifacts.Count -gt 0) "No release artifacts were supplied for publication."
    $states = @()
    $finalPaths = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)

    foreach ($artifact in $Artifacts) {
        $temporaryPath = [System.IO.Path]::GetFullPath([string]$artifact.Temporary)
        $finalPath = [System.IO.Path]::GetFullPath([string]$artifact.Final)
        Assert-True (Test-Path -LiteralPath $temporaryPath -PathType Leaf) `
            "Temporary release artifact is missing: $temporaryPath"
        Assert-True (-not (Test-Path -LiteralPath $finalPath -PathType Container)) `
            "Release artifact destination is a directory: $finalPath"
        Assert-True ($finalPaths.Add($finalPath)) "Duplicate final release artifact path: $finalPath"

        $temporaryParent = [System.IO.Path]::GetDirectoryName($temporaryPath)
        $finalParent = [System.IO.Path]::GetDirectoryName($finalPath)
        Assert-True ([string]::Equals($temporaryParent, $finalParent, $PathComparison)) `
            "Temporary and final artifacts must share a directory for atomic publication."

        $backupPath = "$finalPath.$BuildId.backup"
        Assert-True (-not (Test-Path -LiteralPath $backupPath)) "Unexpected backup path already exists: $backupPath"
        $states += [pscustomobject]@{
            Temporary = $temporaryPath
            Final = $finalPath
            Backup = $backupPath
            BackedUp = $false
            Published = $false
        }
    }

    try {
        foreach ($state in $states) {
            if (Test-Path -LiteralPath $state.Final -PathType Leaf) {
                [System.IO.File]::Move($state.Final, $state.Backup)
                $state.BackedUp = $true
            }
        }
        foreach ($state in $states) {
            # Each move is an atomic rename because temporary and final files share a directory.
            [System.IO.File]::Move($state.Temporary, $state.Final)
            $state.Published = $true
        }
    }
    catch {
        for ($index = $states.Count - 1; $index -ge 0; $index--) {
            $state = $states[$index]
            if ($state.Published -and (Test-Path -LiteralPath $state.Final -PathType Leaf)) {
                [System.IO.File]::Delete($state.Final)
            }
        }
        for ($index = $states.Count - 1; $index -ge 0; $index--) {
            $state = $states[$index]
            if ($state.BackedUp -and (Test-Path -LiteralPath $state.Backup -PathType Leaf)) {
                [System.IO.File]::Move($state.Backup, $state.Final)
            }
        }
        throw
    }

    foreach ($state in $states) {
        if ($state.BackedUp) {
            try {
                [System.IO.File]::Delete($state.Backup)
            }
            catch {
                # The new artifact set is already complete. Keep the old backup rather
                # than report a failed/partial release after a successful transaction.
                Write-Warning "Could not remove obsolete artifact backup: $($state.Backup)"
            }
        }
    }
}

$buildId = [guid]::NewGuid().ToString("N")
$temporaryRoot = $null
$temporaryZip = $null
$temporaryPerFileChecksum = $null
$temporaryChecksumSums = $null
$outputRoot = $null
$published = $false

try {
    if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
        throw "Version must be a canonical three-part semantic version (for example, 2.0.0)."
    }
    if (-not [string]::Equals($Version, $ExpectedReleaseVersion, [System.StringComparison]::Ordinal)) {
        throw "This release definition is locked to v$ExpectedReleaseVersion; requested v$Version."
    }

    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $ProjectRoot = Split-Path -Parent $PSScriptRoot
    }
    $sourceRoot = Get-NormalizedFullPath $ProjectRoot
    Assert-True (Test-Path -LiteralPath $sourceRoot -PathType Container) "Project root does not exist: $sourceRoot"
    Assert-NoReparsePoint $sourceRoot $sourceRoot

    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
        $OutputDirectory = Join-Path $sourceRoot "dist"
    }
    $outputRoot = Get-NormalizedFullPath $OutputDirectory $sourceRoot
    if (Test-Path -LiteralPath $outputRoot -PathType Leaf) {
        throw "Output directory path is an existing file: $outputRoot"
    }
    [void][System.IO.Directory]::CreateDirectory($outputRoot)
    $outputItem = Get-Item -LiteralPath $outputRoot -Force
    Assert-True (($outputItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) `
        "Output directory may not be a link or junction: $outputRoot"

    $packageDirectoryName = "CS-Scout-v$Version"
    $archiveName = "CS-Scout-Windows-x64-v$Version.zip"
    $perFileChecksumName = "$archiveName.sha256"
    $checksumSumsName = "SHA256SUMS.txt"
    $finalZip = Join-Path $outputRoot $archiveName
    $finalPerFileChecksum = Join-Path $outputRoot $perFileChecksumName
    $finalChecksumSums = Join-Path $outputRoot $checksumSumsName
    $temporaryZip = Join-Path $outputRoot ".$archiveName.$buildId.tmp"
    $temporaryPerFileChecksum = Join-Path $outputRoot ".$perFileChecksumName.$buildId.tmp"
    $temporaryChecksumSums = Join-Path $outputRoot ".$checksumSumsName.$buildId.tmp"

    foreach ($path in @($temporaryZip, $temporaryPerFileChecksum, $temporaryChecksumSums)) {
        Assert-True (-not (Test-Path -LiteralPath $path)) "Temporary output path already exists: $path"
    }

    $systemTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd("\", "/")
    $temporaryRoot = Join-Path $systemTemp "cs-scout-windows-release-$buildId"
    Assert-True (-not (Test-Path -LiteralPath $temporaryRoot)) "Unique staging directory already exists: $temporaryRoot"
    [void][System.IO.Directory]::CreateDirectory($temporaryRoot)
    $packageRoot = Join-Path $temporaryRoot $packageDirectoryName
    [void][System.IO.Directory]::CreateDirectory($packageRoot)

    Write-Host "Building $archiveName from a strict runtime allowlist..." -ForegroundColor Cyan

    $rootReadme = Join-Path $sourceRoot "README.md"
    $playerReadme = Join-Path $sourceRoot "windows\README-PLAYER-ZH.md"
    $indexTemplate = Join-Path $sourceRoot "server\templates\index.html"
    foreach ($path in @($rootReadme, $playerReadme, $indexTemplate)) {
        Assert-True (Test-Path -LiteralPath $path -PathType Leaf) "Version validation file is missing: $path"
    }
    Assert-True ((Read-Utf8Text $rootReadme) -match '(?m)^# CS-Scout 2\.0\r?$') `
        "README.md does not identify the 2.0 release line."
    Assert-True ((Read-Utf8Text $playerReadme).Contains($archiveName)) `
        "Windows player guide does not name the expected release archive: $archiveName"
    Assert-True ((Read-Utf8Text $indexTemplate).Contains("<title>CS-Scout 2.0</title>")) `
        "Web UI title does not identify CS-Scout 2.0."

    $requirementsPath = Join-Path $sourceRoot "server\requirements-runtime.txt"
    Assert-True (Test-Path -LiteralPath $requirementsPath -PathType Leaf) `
        "Pinned runtime requirements are missing: $requirementsPath"
    Assert-RuntimeRequirements $requirementsPath

    foreach ($mapName in $ExpectedMaps) {
        $metaPath = Join-Path $sourceRoot "server\data\maps\$mapName\meta.json"
        $radarPath = Join-Path $sourceRoot "server\data\maps\$mapName\radar.png"
        Assert-True (Test-Path -LiteralPath $metaPath -PathType Leaf) "Missing map metadata: $mapName"
        Assert-True (Test-Path -LiteralPath $radarPath -PathType Leaf) "Missing map radar: $mapName"
        Assert-MapMetadata $metaPath $mapName
        Assert-PngFile $radarPath
    }
    Assert-WebPFile (Join-Path $sourceRoot "server\static\logo.webp")

    $expectedRelativePaths = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)
    $requiredFiles = @(
        "LICENSE",
        "README.md",
        "RELEASE_NOTES_v2.0.2.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "windows\Install-CS-Scout.cmd",
        "windows\Install-CS-Scout.ps1",
        "windows\README-PLAYER-ZH.md",
        "windows\Start-CS-Scout.cmd",
        "windows\Start-CS-Scout.ps1",
        "windows\Verify-Windows-Package.ps1",
        "server\api_client.py",
        "server\combat.py",
        "server\config.py",
        "server\maps.py",
        "server\parse.py",
        "server\pipeline.py",
        "server\player_json.py",
        "server\requirements-runtime.txt",
        "server\web_server.py",
        "server\static\app.js",
        "server\static\logo.webp",
        "server\static\replay.js",
        "server\templates\index.html"
    )
    foreach ($relativePath in $requiredFiles) {
        Copy-ReleaseFile $sourceRoot $packageRoot $relativePath $expectedRelativePaths
    }

    foreach ($mapName in $ExpectedMaps) {
        foreach ($fileName in @("meta.json", "radar.png")) {
            Copy-ReleaseFile `
                $sourceRoot `
                $packageRoot `
                "server\data\maps\$mapName\$fileName" `
                $expectedRelativePaths
        }
    }
    foreach ($iconFile in $ExpectedIconFiles) {
        Copy-ReleaseFile $sourceRoot $packageRoot "radar\icons\$iconFile" $expectedRelativePaths
    }

    Assert-StagedPackage $packageRoot $expectedRelativePaths
    Assert-StagedRuntimeIntegrity $packageRoot $archiveName

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $temporaryRoot,
        $temporaryZip,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    Assert-True ((Get-Item -LiteralPath $temporaryZip -Force).Length -gt 0) "Generated ZIP is empty."
    Assert-ZipArchive $temporaryZip $packageDirectoryName $packageRoot $expectedRelativePaths

    $hash = (Get-FileHash -LiteralPath $temporaryZip -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-True ($hash -match '^[0-9a-f]{64}$') "Generated SHA256 value is invalid."
    $checksumLine = "$hash  $archiveName`r`n"
    [System.IO.File]::WriteAllText(
        $temporaryPerFileChecksum,
        $checksumLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    [System.IO.File]::WriteAllText(
        $temporaryChecksumSums,
        $checksumLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    foreach ($checksumPath in @($temporaryPerFileChecksum, $temporaryChecksumSums)) {
        $checksumText = [System.IO.File]::ReadAllText($checksumPath, $Utf8NoBom)
        Assert-True ($checksumText -ceq $checksumLine) "SHA256 sidecar verification failed: $checksumPath"
    }

    $artifacts = @(
        [pscustomobject]@{ Temporary = $temporaryZip; Final = $finalZip },
        [pscustomobject]@{ Temporary = $temporaryPerFileChecksum; Final = $finalPerFileChecksum },
        [pscustomobject]@{ Temporary = $temporaryChecksumSums; Final = $finalChecksumSums }
    )
    Publish-ArtifactSet $artifacts $buildId
    $published = $true

    $publishedHash = (Get-FileHash -LiteralPath $finalZip -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-True ($publishedHash -ceq $hash) "Published ZIP hash changed after the atomic move."

    Write-Host "Windows release built successfully." -ForegroundColor Green
    Write-Host "ZIP:        $finalZip"
    Write-Host "SHA256:     $finalPerFileChecksum"
    Write-Host "Checksums:  $finalChecksumSums"
    Write-Host "Hash:       $hash"
}
finally {
    try {
        if ($null -ne $temporaryZip -and $null -ne $outputRoot) {
            Remove-OwnedTemporaryFile $temporaryZip $outputRoot $buildId
        }
        if ($null -ne $temporaryPerFileChecksum -and $null -ne $outputRoot) {
            Remove-OwnedTemporaryFile $temporaryPerFileChecksum $outputRoot $buildId
        }
        if ($null -ne $temporaryChecksumSums -and $null -ne $outputRoot) {
            Remove-OwnedTemporaryFile $temporaryChecksumSums $outputRoot $buildId
        }
    }
    catch {
        Write-Warning "Could not remove a release temporary file: $($_.Exception.Message)"
    }

    try {
        if ($null -ne $temporaryRoot) {
            $systemTempForCleanup = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd("\", "/")
            Remove-OwnedStagingDirectory $temporaryRoot $systemTempForCleanup $buildId
        }
    }
    catch {
        Write-Warning "Could not remove the release staging directory: $($_.Exception.Message)"
    }

    if (-not $published) {
        Write-Verbose "Release build did not publish new artifacts. Existing final artifacts, if any, were preserved."
    }
}
