# ============================================================================
#  Canvas Quarto Sync - Update Script
#
#  Usage (one-liner):
#    irm https://raw.githubusercontent.com/cenmir/CanvasQuartoSync/main/update.ps1 | iex
#
#  Updates the repo, installs latest VSIX, and updates Python packages.
# ============================================================================

$CLONE_DIR  = Join-Path $env:USERPROFILE "CanvasQuartoSync"
$VENV_DIR   = Join-Path $env:USERPROFILE ".venvs\canvas_quarto_env"
$REPO_URL   = "https://github.com/cenmir/CanvasQuartoSync.git"

# --- Enforce TLS 1.2 ---
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Helpers ---
function Write-Step  { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$msg) Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param([string]$msg) Write-Host "   [!] $msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$msg) Write-Host "   [ERROR] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "   Canvas Quarto Sync - Updater"              -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# ---- Step 1: Update repo ----
Write-Step "Updating CanvasQuartoSync..."

if (Test-Path (Join-Path $CLONE_DIR ".git")) {
    Push-Location $CLONE_DIR
    git pull
    Pop-Location
    Write-Ok "Updated to latest version."
} else {
    Write-Host "   Cloning repository..." -ForegroundColor White
    git clone $REPO_URL $CLONE_DIR
    if ($LASTEXITCODE -ne 0) { Write-Err "Failed to clone repository."; exit 1 }
    Write-Ok "Repository cloned to $CLONE_DIR"
}

# ---- Step 2: Update Python packages ----
Write-Step "Updating Python packages..."

$venvActivate = Join-Path $VENV_DIR "Scripts\Activate.ps1"
$requirementsFile = Join-Path $CLONE_DIR "requirements.txt"

if (Test-Path $venvActivate) {
    try { & $venvActivate } catch {
        $env:Path = (Join-Path $VENV_DIR "Scripts") + ";" + $env:Path
        $env:VIRTUAL_ENV = $VENV_DIR
    }

    if (Test-Path $requirementsFile) {
        uv pip install --upgrade -r $requirementsFile
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Python packages updated."
        } else {
            Write-Warn "Package update failed. Run install.ps1 to reinstall."
        }
    }
} else {
    Write-Warn "Virtual environment not found. Run install.ps1 first."
}

# ---- Step 3: Install latest VS Code extension ----
Write-Step "Updating VS Code extension..."

$codeCmd = $null
foreach ($c in @("code.cmd", "code")) {
    try { & $c --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $codeCmd = $c; break } } catch {}
}

if ($codeCmd) {
    $vsixPath = Join-Path $env:TEMP "canvasquartosync.vsix"
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/cenmir/CanvasQuartoSync/releases/latest" -Headers @{ Accept = "application/vnd.github.v3+json" }
        $asset = $release.assets | Where-Object { $_.name -like "*.vsix" } | Select-Object -First 1
        if ($asset) {
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $vsixPath -UseBasicParsing
            $ProgressPreference = 'Continue'
            $installOutput = & $codeCmd --install-extension $vsixPath --force 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Extension updated to $($release.tag_name). Restart VS Code to activate."
            } else {
                Write-Warn "Extension install returned exit code $LASTEXITCODE"
            }
            Remove-Item $vsixPath -ErrorAction SilentlyContinue
        } else {
            Write-Warn "No .vsix found in latest release."
        }
    } catch {
        Write-Warn "Could not download extension: $_"
    }
} else {
    Write-Warn "VS Code not found in PATH."
}

# ---- Done ----
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "   Update complete! Restart VS Code."         -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
