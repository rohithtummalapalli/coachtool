param(
  [string]$Source = ".\\vendor\\chainlit-frontend\\frontend\\dist",
  [string]$Destination = ".\\public\\build"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Source)) {
  Write-Error "Frontend build source not found: $Source`nRun build first: pnpm run buildUi (from vendor/chainlit-frontend)"
  exit 1
}

if (Test-Path $Destination) {
  Remove-Item $Destination -Recurse -Force
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Copy-Item (Join-Path $Source '*') $Destination -Recurse -Force

Write-Host "Frontend build prepared at $Destination" -ForegroundColor Green
