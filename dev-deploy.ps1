# ============================================================================
#  Dev Deploy - Build, install, and reload in one shot
#
#  Run from the repo root:
#    .\dev-deploy.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

$REPO_ROOT  = $PSScriptRoot
$EXT_DIR    = Join-Path $REPO_ROOT "extension"
$CLONE_DIR  = Join-Path $env:USERPROFILE "CanvasQuartoSync"

# --- Helpers ---
function Write-Step  { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$msg) Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Err   { param([string]$msg) Write-Host "   [ERROR] $msg" -ForegroundColor Red }

# ---- Step 1: Sync repo files to ~/CanvasQuartoSync ----
Write-Step "Syncing repo to $CLONE_DIR..."

if ($REPO_ROOT -ne $CLONE_DIR) {
    if (-not (Test-Path $CLONE_DIR)) {
        New-Item -ItemType Directory -Path $CLONE_DIR -Force | Out-Null
    }
    # Copy Python files and config (exclude .git, extension, node_modules, .venv)
    $exclude = @('.git', 'extension', 'node_modules', '.venv', '__pycache__')
    Get-ChildItem -Path $REPO_ROOT -Exclude $exclude | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $CLONE_DIR -Recurse -Force
    }
    Write-Ok "Files synced."
} else {
    Write-Ok "Already running from install directory."
}

# ---- Step 2: Build VSIX ----
Write-Step "Building VSIX..."

Push-Location $EXT_DIR

# Read version from package.json
$pkgJson = Get-Content (Join-Path $EXT_DIR "package.json") -Raw | ConvertFrom-Json
$version = $pkgJson.version
$vsixName = "canvasquartosync-$version.vsix"

# Clean dist to avoid Dropbox lock issues
if (Test-Path (Join-Path $EXT_DIR "dist\webview\assets")) {
    Remove-Item (Join-Path $EXT_DIR "dist\webview\assets") -Recurse -Force -ErrorAction SilentlyContinue
}

npx @vscode/vsce package --no-dependencies -o $vsixName 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "VSIX build failed."
    Pop-Location
    exit 1
}
Write-Ok "Built $vsixName"
Pop-Location

# ---- Step 3: Install VSIX ----
Write-Step "Installing extension..."

$codeCmd = $null
foreach ($c in @("code.cmd", "code")) {
    try { & $c --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $codeCmd = $c; break } } catch {}
}

if (-not $codeCmd) {
    Write-Err "VS Code not found in PATH."
    exit 1
}

$vsixPath = Join-Path $EXT_DIR $vsixName
& $codeCmd --install-extension $vsixPath --force 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "Extension install failed."
    exit 1
}
Write-Ok "Extension v$version installed."

# ---- Step 4: Done ----
Write-Ok "Restart VS Code to activate the new extension."

Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "   Deploy complete! v$version"                 -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
