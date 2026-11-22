<#
.SYNOPSIS
    Collect a read-only Perfect World / CS-Scout diagnostic bundle.

.DESCRIPTION
    Gathered for the 2.5 investigation into "the platform client updated and the
    log-based detection stopped working". Everything here is read-only: no file
    is modified, nothing is uploaded.

    Secrets are redacted before they are written:
      * token / jt / cookie / authorization lines are replaced with <redacted>
      * long hex blobs outside the known log-payload position are masked
    Review the output before sending it to anyone.

.PARAMETER OutputPath
    Where to write the report. Defaults to a timestamped file in the current
    directory.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\windows\Collect-Diagnostics.ps1
#>
[CmdletBinding()]
param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not $OutputPath) {
    $OutputPath = Join-Path (Get-Location) "cs-scout-diagnostics-$stamp.txt"
}

$lines = New-Object System.Collections.Generic.List[string]
function Add-Section([string]$title) {
    $lines.Add("")
    $lines.Add("=" * 70)
    $lines.Add("== $title")
    $lines.Add("=" * 70)
}
function Add-Line([string]$text) { $lines.Add($text) }

function Hide-Secrets([string]$text) {
    if ([string]::IsNullOrEmpty($text)) { return $text }
    $clean = [regex]::Replace(
        $text,
        '(?i)(token|jt|cookie|authorization|access[_-]?token|pwa-jt)\s*[:=]\s*\S+',
        '$1=<redacted>'
    )
    # A bare 32+ hex run is a credential, not a log payload; payloads always
    # appear as ^hex$ and are kept verbatim for decoding.
    $clean = [regex]::Replace($clean, '(?<![\^0-9a-fA-F])[0-9a-fA-F]{32,}(?!\$)', '<redacted-hex>')
    return $clean
}

Add-Section "CS-Scout diagnostics"
Add-Line "collected : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
Add-Line "computer  : $env:COMPUTERNAME"
Add-Line "user      : $env:USERNAME"
Add-Line "os        : $((Get-CimInstance Win32_OperatingSystem).Caption) $((Get-CimInstance Win32_OperatingSystem).Version)"
Add-Line "ps        : $($PSVersionTable.PSVersion)"

Add-Section "Repository"
$repoRoot = Split-Path -Parent $PSScriptRoot
Add-Line "root: $repoRoot"
foreach ($relative in @(
    "server\web_server.py",
    "perfectworld_experiment\current_match.py",
    "perfectworld_experiment\native_signer.py",
    "fivee_monitor.py",
    "server\fivee_monitor.py"
)) {
    $path = Join-Path $repoRoot $relative
    Add-Line ("{0,-52} {1}" -f $relative, $(if (Test-Path $path) { "present" } else { "missing" }))
}

Add-Section "Perfect World signing component"
$pythonCandidates = @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    "python"
)
$signerDone = $false
foreach ($python in $pythonCandidates) {
    if ($signerDone) { break }
    if ($python -ne "python" -and -not (Test-Path $python)) { continue }
    # PowerShell 5.1 mangles quotes when passing -c payloads to a native
    # executable, so the probe is written to a file and executed from there.
    $probeFile = Join-Path $env:TEMP "cs-scout-signer-probe-$stamp.py"
    $probeSource = @"
import json
import sys
sys.path.insert(0, r"$repoRoot")
try:
    from perfectworld_experiment import native_signer
except Exception as exc:
    print(json.dumps({"error": "import failed: " + str(exc)}))
    raise SystemExit(0)
try:
    status = native_signer.get_dll_status(force=True)
except Exception as exc:
    print(json.dumps({"error": "status failed: " + str(exc)}))
    raise SystemExit(0)
safe = {k: v for k, v in status.items() if k in {"ready", "code", "message", "path", "source"}}
# ASCII-escaped JSON survives the console code page intact.
print(json.dumps(safe))
"@
    try {
        Set-Content -LiteralPath $probeFile -Value $probeSource -Encoding UTF8
        $result = & $python $probeFile 2>&1 | Out-String
        Add-Line "interpreter: $python"
        Add-Line (Hide-Secrets $result.Trim())
        $signerDone = $true
    }
    catch {
        Add-Line "interpreter $python failed: $($_.Exception.Message)"
    }
    finally {
        Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
    }
}
if (-not $signerDone) { Add-Line "no usable Python interpreter found" }

Add-Section "Candidate client directories"
$roots = @(
    $env:APPDATA,
    $env:LOCALAPPDATA,
    $env:ProgramFiles,
    ${env:ProgramFiles(x86)}
) | Where-Object { $_ -and (Test-Path $_) }

$patterns = @("Wmpvp", "wmpvp", "wmpvp*", "Perfect*", "完美*", "PvpAlive*", "5E*")
$seen = New-Object System.Collections.Generic.HashSet[string]
foreach ($root in $roots) {
    foreach ($pattern in $patterns) {
        Get-ChildItem -LiteralPath $root -Directory -Filter $pattern -ErrorAction SilentlyContinue |
            ForEach-Object {
                if ($seen.Add($_.FullName)) {
                    Add-Line ("{0}  (modified {1:yyyy-MM-dd HH:mm})" -f $_.FullName, $_.LastWriteTime)
                }
            }
    }
}
if ($seen.Count -eq 0) { Add-Line "no candidate directory matched the known patterns" }

Add-Section "Log directory inventory"
$logDirs = @()
foreach ($candidate in $seen) {
    $logDir = Join-Path $candidate "Log"
    if (Test-Path $logDir) { $logDirs += $logDir }
}
# Also probe the historically known path even when the parent was not matched.
$legacy = Join-Path $env:APPDATA "Wmpvp\Log"
if ((Test-Path $legacy) -and ($logDirs -notcontains $legacy)) { $logDirs += $legacy }

if ($logDirs.Count -eq 0) {
    Add-Line "NO log directory found. Include this whole report: it tells us the path moved."
}
foreach ($logDir in $logDirs) {
    Add-Line ""
    Add-Line "log dir: $logDir"
    $files = Get-ChildItem -LiteralPath $logDir -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    foreach ($file in $files | Select-Object -First 15) {
        Add-Line ("  {0,-42} {1,12:N0} B  {2:yyyy-MM-dd HH:mm:ss}" -f $file.Name, $file.Length, $file.LastWriteTime)
    }
    if (-not $files) { Add-Line "  (empty directory)" }
}

Add-Section "Newest log sample (head 120 / tail 40 lines, redacted)"
$newest = $null
foreach ($logDir in $logDirs) {
    $candidate = Get-ChildItem -LiteralPath $logDir -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($candidate -and (-not $newest -or $candidate.LastWriteTime -gt $newest.LastWriteTime)) {
        $newest = $candidate
    }
}
if (-not $newest) {
    Add-Line "no log file available"
}
else {
    Add-Line "file: $($newest.FullName)"
    Add-Line "size: $($newest.Length) bytes"
    $head = Get-Content -LiteralPath $newest.FullName -TotalCount 120 -ErrorAction SilentlyContinue
    Add-Line "--- head ---"
    foreach ($line in $head) { Add-Line (Hide-Secrets $line) }
    Add-Line "--- tail ---"
    $tail = Get-Content -LiteralPath $newest.FullName -Tail 40 -ErrorAction SilentlyContinue
    foreach ($line in $tail) { Add-Line (Hide-Secrets $line) }
}

Add-Section "CS-Scout runtime data"
$dataDir = Join-Path $env:LOCALAPPDATA "CS-Scout"
if (Test-Path $dataDir) {
    Get-ChildItem -LiteralPath $dataDir -ErrorAction SilentlyContinue | ForEach-Object {
        Add-Line ("{0}  ({1})" -f $_.Name, $(if ($_.PSIsContainer) { "dir" } else { "$($_.Length) B" }))
    }
}
else {
    Add-Line "no CS-Scout data directory yet"
}

$lines.Add("")
$lines.Add("End of report. Review before sharing; secrets are redacted but you own the final check.")
Set-Content -LiteralPath $OutputPath -Value $lines -Encoding UTF8
Write-Host "Diagnostic report written to:"
Write-Host "  $OutputPath"
Write-Host "Review it, then send the file back. Nothing was uploaded or modified."
