#Requires -RunAsAdministrator
# Allow EPIC on the current LAN when WSL uses mirrored networking.
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$ruleName = "EPIC-WSL-LAN-TCP-$Port"

if ($Remove) {
    Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Write-Host "Removed EPIC LAN rules for TCP $Port."
    return
}

# Only use connected LAN interfaces with a default gateway.
$addresses = @(Get-NetIPConfiguration | Where-Object IPv4DefaultGateway |
    ForEach-Object { $_.IPv4Address } |
    Where-Object { $_.IPAddress -notlike "127.*" })
if ($addresses.Count -eq 0) {
    throw "No LAN IPv4 address found. Connect Wi-Fi or Ethernet and try again."
}
$localAddresses = @($addresses.IPAddress | Sort-Object -Unique)
$subnets = @($addresses | ForEach-Object {
    $bytes = [System.Net.IPAddress]::Parse($_.IPAddress).GetAddressBytes()
    for ($index = 0; $index -lt 4; $index++) {
        $bits = [Math]::Min(8, [Math]::Max(0, $_.PrefixLength - 8 * $index))
        $mask = (255 -shl (8 - $bits)) -band 255
        $bytes[$index] = $bytes[$index] -band $mask
    }
    "$(($bytes -join '.'))/$($_.PrefixLength)"
} | Sort-Object -Unique)

$hostRule = @{
    Name = $ruleName; Direction = "Inbound"; Action = "Allow"
    Enabled = "True"; Profile = "Any"; Protocol = "TCP"; LocalPort = $Port
    LocalAddress = $localAddresses; RemoteAddress = $subnets
}
# WSL mirrored networking inherits this host rule (HostFirewallLocal).
if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule @hostRule | Out-Null
} else {
    New-NetFirewallRule @hostRule -DisplayName "EPIC WSL LAN TCP $Port" | Out-Null
}
Write-Host "Allowed TCP $Port from $($subnets -join ', ')."
foreach ($address in $localAddresses) {
    Write-Host "EPIC LAN: http://${address}:$Port"
}
Write-Host "Keep EPIC running in WSL. Run this script again after changing networks."
