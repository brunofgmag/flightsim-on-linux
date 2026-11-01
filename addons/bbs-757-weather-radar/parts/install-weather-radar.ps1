param(
    [Parameter(Mandatory)][string]$Bundle,
    [Parameter(Mandatory)][string]$Community,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$scripts = Join-Path (Resolve-Path -LiteralPath $Bundle) 'scripts'
$install = [IO.File]::ReadAllText((Join-Path $scripts 'Install.ps1'))
$dotSource = ". (Join-Path `$PSScriptRoot 'Support.ps1')"
if (-not $install.Contains($dotSource)) { throw 'Install.ps1 no longer dot-sources Support.ps1 the way this launcher expects.' }

$linux = @'
function Get-FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path.Replace('\','/')).TrimEnd([char[]]'/')
}
function Test-Within([string]$Root, [string]$Path) {
    return (Get-FullPath $Path).StartsWith((Get-FullPath $Root) + '/', [StringComparison]::Ordinal)
}
function Add-Type {
    param([string[]]$Path, [string[]]$ReferencedAssemblies)
    Microsoft.PowerShell.Utility\Add-Type -Path (@($Path) + $RadarSerializer)
}
'@

$RadarScripts = $scripts
$RadarSerializer = Join-Path $PSScriptRoot 'JavaScriptSerializer.cs'
$text = $install.Replace($dotSource, ". (Join-Path `$RadarScripts 'Support.ps1')`n$linux").Replace('$PSScriptRoot', '$RadarScripts')
$aircraft = Join-Path $Community 'bluebirdsim-aircraft-757-200'
$arguments = @{ AircraftRoot = $aircraft; Community = $Community; NonInteractive = $true; DryRun = $DryRun }
& ([scriptblock]::Create($text)) @arguments
