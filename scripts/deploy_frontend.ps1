$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $root "vendor\chainlit-frontend"

if (-not (Test-Path $frontendRoot)) {
  Write-Error "Chainlit frontend folder not found: $frontendRoot"
  exit 1
}

Push-Location $frontendRoot
try {
  Push-Location "libs\react-client"
  try {
    pnpm run build
  } finally {
    Pop-Location
  }

  Push-Location "frontend"
  try {
    pnpm run build
  } finally {
    Pop-Location
  }
} finally {
  Pop-Location
}

powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "prepare_frontend.ps1")

Write-Host "Frontend build + deploy artifact prepared successfully." -ForegroundColor Green
