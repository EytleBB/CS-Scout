[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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
        Write-Warning "Could not tighten the local access key ACL."
    }
}

try {
    $principal = [System.Security.Principal.WindowsPrincipal]::new(
        [System.Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if ($principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this file normally, not as Administrator."
    }
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is not available for this Windows account."
    }
    $secretPath = Join-Path $env:LOCALAPPDATA "CS-Scout\secret.key"
    if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
        throw "No local access key exists yet. Run Install-CS-Scout.cmd first."
    }

    $raw = Get-Content -LiteralPath $secretPath -Raw
    if ($null -eq $raw) {
        throw "The local access key file is empty. Run Install-CS-Scout.cmd to repair the installation."
    }
    $secret = ([string]$raw).Trim()
    if ($secret -notmatch "^[0-9a-f]{64}$") {
        throw "The local access key is invalid. Run Install-CS-Scout.cmd to diagnose the installation."
    }
    Protect-SecretFile $secretPath
    if (-not (Get-Command Set-Clipboard -ErrorAction SilentlyContinue)) {
        throw "Windows clipboard support is unavailable in this PowerShell session."
    }

    Set-Clipboard -Value $secret
    Write-Host "The CS-Scout access key is now in the clipboard." -ForegroundColor Green
    Write-Host "Return to the page and press Ctrl+V."
    exit 0
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
