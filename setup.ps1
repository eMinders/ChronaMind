# Quick setup and start script for Windows (PowerShell)
# Usage: .\setup.ps1 [-NoStart] [-SkipDocker]

param(
    [switch]$NoStart = $false,
    [switch]$SkipDocker = $false
)

$ErrorActionPreference = "Stop"

# 1. Ensure uv is available
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $uvLocal = "$HOME\.local\bin\uv.exe"
    $uvCargo = "$HOME\.cargo\bin\uv.exe"

    if (Test-Path $uvLocal) {
        $env:PATH = "$HOME\.local\bin;$env:PATH"
    } elseif (Test-Path $uvCargo) {
        $env:PATH = "$HOME\.cargo\bin;$env:PATH"
    } else {
        Write-Host "Installing uv package manager (astral.sh)..." -ForegroundColor Cyan
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        $env:PATH = "$HOME\.local\bin;$env:PATH"
    }
}

$argsList = @()
if ($NoStart) {
    $argsList += "--no-start"
} else {
    $argsList += "--start"
}

if ($SkipDocker) {
    $argsList += "--skip-docker"
}

uv run python scripts/setup.py @argsList
