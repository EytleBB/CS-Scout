Set-StrictMode -Version Latest

if (-not ("CSScout.WindowsTokenNativeMethods" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace CSScout {
    public static class WindowsTokenNativeMethods {
        [DllImport("advapi32.dll", SetLastError = true)]
        public static extern bool GetTokenInformation(
            IntPtr tokenHandle,
            int tokenInformationClass,
            out int tokenInformation,
            int tokenInformationLength,
            out int returnLength
        );
    }
}
"@
}

function Get-CSScoutPrivilegeState {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity.User) {
        throw "The current Windows account has no security identifier."
    }

    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    $isAdministrator = $principal.IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator
    )
    $sid = $identity.User.Value
    $elevationType = "Unknown"
    $tokenInformation = 0
    $returnLength = 0
    try {
        $read = [CSScout.WindowsTokenNativeMethods]::GetTokenInformation(
            $identity.Token,
            18,
            [ref]$tokenInformation,
            4,
            [ref]$returnLength
        )
        if ($read) {
            $elevationType = switch ($tokenInformation) {
                1 { "Default" }
                2 { "Full" }
                3 { "Limited" }
                default { "Unknown" }
            }
        }
    }
    catch {
        $elevationType = "Unknown"
    }

    return [pscustomobject]@{
        IsAdministrator = [bool]$isAdministrator
        IsBuiltInAdministrator = [bool]($sid -match "-500$")
        Sid = $sid
        ElevationType = $elevationType
    }
}

function Test-CSScoutPrivilegeRequiresBlock {
    param(
        [Parameter(Mandatory = $true)][bool]$IsAdministrator,
        [Parameter(Mandatory = $true)][string]$Sid,
        [Parameter(Mandatory = $true)][string]$ElevationType
    )

    if (-not $IsAdministrator -or $Sid -match "-500$") {
        return $false
    }

    # Default is used by systems where UAC/admin approval mode is disabled.
    # Full is the explicit elevated token used by "Run as administrator".
    return $ElevationType -in @("Full", "Unknown")
}

function Assert-CSScoutSupportedPrivilege {
    param(
        [Parameter(Mandatory = $true)][string]$ElevatedMessage
    )

    $state = Get-CSScoutPrivilegeState
    if (Test-CSScoutPrivilegeRequiresBlock `
        -IsAdministrator $state.IsAdministrator `
        -Sid $state.Sid `
        -ElevationType $state.ElevationType) {
        throw $ElevatedMessage
    }

    if ($state.IsAdministrator) {
        Write-Warning (
            "Windows is using an always-administrator session. " +
            "CS-Scout will continue for the current account and keep its data under that account's LOCALAPPDATA."
        )
    }
    return $state
}
