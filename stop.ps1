<#
.SYNOPSIS
  Stops the EVOLVE backend and frontend started by start.ps1.
#>
$ErrorActionPreference = 'SilentlyContinue'
foreach ($port in 8000, 5173, 4173) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            Stop-Process -Id $_ -Force
            Write-Host "Stopped process $_ (port $port)"
        }
}
Remove-Item (Join-Path $PSScriptRoot 'logs\pids.json') -Force
Write-Host 'EVOLVE stopped.'
