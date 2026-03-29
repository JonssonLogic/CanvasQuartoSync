# ============================================================================
#  Canvas Quarto Sync — One-Line Installer (Windows PowerShell)
#
#  Usage:
#    irm https://raw.githubusercontent.com/cenmir/CanvasQuartoSync/main/install.ps1 | iex
#
#  Installs everything automatically — no prompts, no questions.
#  Canvas credentials are configured later in the VS Code extension.
# ============================================================================

# --- Configuration ---
$REPO_URL   = "https://github.com/cenmir/CanvasQuartoSync.git"
$VENV_ROOT  = Join-Path $env:USERPROFILE "venvs"
$VENV_DIR   = Join-Path $VENV_ROOT "canvas_quarto_env"
$CLONE_DIR  = Join-Path $VENV_DIR "CanvasQuartoSync"

# --- Enforce TLS 1.2 ---
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Helpers ---
function Write-Step  { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$msg) Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param([string]$msg) Write-Host "   [!] $msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$msg) Write-Host "   [ERROR] $msg" -ForegroundColor Red }

# ============================================================================
Write-Host ""
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host "   Canvas Quarto Sync — Installer" -ForegroundColor Magenta
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "  This will install:" -ForegroundColor White
Write-Host "    - Python (if not found)" -ForegroundColor White
Write-Host "    - Git (if not found)" -ForegroundColor White
Write-Host "    - CanvasQuartoSync (cloned to ~/venvs/canvas_quarto_env/)" -ForegroundColor White
Write-Host "    - Python packages (virtual environment)" -ForegroundColor White
Write-Host "    - VS Code extension (if VS Code is installed)" -ForegroundColor White
Write-Host ""
$answer = Read-Host "  Continue? [Y/n]"
if ($answer -match "^[Nn]") {
    Write-Host "  Aborted." -ForegroundColor Yellow
    exit 0
}
Write-Host ""

# ============================================================================
#  Step 1 — Python
# ============================================================================
Write-Step "Checking for Python..."

$pythonCmd = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python \d") { $pythonCmd = $cmd; Write-Ok "Found: $ver"; break }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "   Installing Python via uv..." -ForegroundColor White
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
        & uv python install 3.13
        $pythonCmd = "python"
        Write-Ok "Python 3.13 installed via uv."
    } catch {
        Write-Err "Failed to install Python. Install manually from https://www.python.org/downloads/ and re-run."
        exit 1
    }
}

# ============================================================================
#  Step 2 — Quarto
# ============================================================================
Write-Step "Checking for Quarto CLI..."

$quartoFound = $false
try {
    $ver = & quarto --version 2>&1
    if ($LASTEXITCODE -eq 0) { $quartoFound = $true; Write-Ok "Found: Quarto $ver" }
} catch {}

if (-not $quartoFound) {
    Write-Warn "Quarto not found. Install from https://quarto.org/docs/get-started/"
    Write-Host "   (Quarto is needed to render .qmd files, but you can install it later)" -ForegroundColor Yellow
}

# ============================================================================
#  Step 3 — Git
# ============================================================================
Write-Step "Checking for Git..."

$gitFound = $false
try {
    $ver = & git --version 2>&1
    if ($LASTEXITCODE -eq 0) { $gitFound = $true; Write-Ok "Found: $ver" }
} catch {}

if (-not $gitFound) {
    Write-Host "   Installing Git via winget..." -ForegroundColor White
    try {
        & winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
        $gitFound = $true
        Write-Ok "Git installed."
    } catch {
        Write-Err "Failed to install Git. Install from https://git-scm.com/download/win and re-run."
        exit 1
    }
}

# ============================================================================
#  Step 4 — Clone Repository
# ============================================================================
Write-Step "Setting up CanvasQuartoSync..."

if (-not (Test-Path $VENV_ROOT)) { New-Item -ItemType Directory -Path $VENV_ROOT -Force | Out-Null }
if (-not (Test-Path $VENV_DIR))  { New-Item -ItemType Directory -Path $VENV_DIR -Force | Out-Null }

if (Test-Path (Join-Path $CLONE_DIR ".git")) {
    Write-Ok "Already installed at $CLONE_DIR"
    Push-Location $CLONE_DIR
    & git pull 2>&1 | Out-Null
    Pop-Location
    Write-Ok "Updated to latest version."
} else {
    & git clone $REPO_URL $CLONE_DIR 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Err "Failed to clone repository."; exit 1 }
    Write-Ok "Repository cloned."
}

# ============================================================================
#  Step 5 — Virtual Environment + Packages
# ============================================================================
Write-Step "Setting up Python environment..."

$venvActivate = Join-Path $VENV_DIR "Scripts\Activate.ps1"
$requirementsFile = Join-Path $CLONE_DIR "requirements.txt"

# Create venv if it doesn't exist
if (-not (Test-Path $venvActivate)) {
    $uvAvailable = $false
    try { & uv --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $uvAvailable = $true } } catch {}

    if ($uvAvailable) {
        & uv venv --python 3.13 $VENV_DIR 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { & $pythonCmd -m venv $VENV_DIR }
    } else {
        & $pythonCmd -m venv $VENV_DIR
    }
    if (-not (Test-Path $venvActivate)) { Write-Err "Failed to create virtual environment."; exit 1 }
    Write-Ok "Virtual environment created."
} else {
    Write-Ok "Virtual environment exists."
}

# Activate and install packages
try { & $venvActivate } catch {
    $env:Path = (Join-Path $VENV_DIR "Scripts") + ";" + $env:Path
    $env:VIRTUAL_ENV = $VENV_DIR
}

$uvAvailable = $false
try { & uv --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $uvAvailable = $true } } catch {}

if ($uvAvailable) {
    & uv pip install -r $requirementsFile 2>&1 | Out-Null
} else {
    & pip install -r $requirementsFile 2>&1 | Out-Null
}

if ($LASTEXITCODE -ne 0) { Write-Err "Package installation failed."; exit 1 }
Write-Ok "Python packages installed."

# ============================================================================
#  Step 6 — Patch run_sync_here.bat
# ============================================================================
$batFile = Join-Path $CLONE_DIR "run_sync_here.bat"
if (Test-Path $batFile) {
    $batContent = Get-Content $batFile -Raw
    $batContent = $batContent -replace '(?m)^set "PROJECT_DIR=.*"', "set `"PROJECT_DIR=$CLONE_DIR`""
    $batContent = $batContent -replace '(?m)^"%PROJECT_DIR%\\\.venv\\Scripts\\python\.exe".*', "`"$VENV_DIR\Scripts\python.exe`" `"%PROJECT_DIR%\sync_to_canvas.py`" `"%~dp0.`" %*"
    Set-Content -Path $batFile -Value $batContent -NoNewline
}

# ============================================================================
#  Step 7 — VS Code Extension
# ============================================================================
Write-Step "Installing VS Code extension..."

# On Windows, "code" is the exe but "code.cmd" supports CLI flags like --install-extension
$codeCmd = $null
$codeCandidates = @("code.cmd", "code")
foreach ($c in $codeCandidates) {
    try {
        & $c --version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $codeCmd = $c; break }
    } catch {}
}

if ($codeCmd) {
    $vsixPath = Join-Path $env:TEMP "canvasquartosync.vsix"
    try {
        Write-Host "   Downloading extension from GitHub..." -ForegroundColor White
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/cenmir/CanvasQuartoSync/releases/latest" -Headers @{ Accept = "application/vnd.github.v3+json" }
        $asset = $release.assets | Where-Object { $_.name -like "*.vsix" } | Select-Object -First 1
        if ($asset) {
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $vsixPath -UseBasicParsing
            Write-Host "   Installing extension..." -ForegroundColor White
            $installOutput = & $codeCmd --install-extension $vsixPath --force 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "VS Code extension installed! Restart VS Code to activate."
            } else {
                Write-Warn "Install returned exit code $LASTEXITCODE"
                Write-Host "   $installOutput" -ForegroundColor Yellow
                Write-Host "   Try manually: $codeCmd --install-extension $vsixPath" -ForegroundColor Yellow
            }
            Remove-Item $vsixPath -ErrorAction SilentlyContinue
        } else {
            Write-Warn "No .vsix in latest release. Download from https://github.com/cenmir/CanvasQuartoSync/releases"
        }
    } catch {
        Write-Warn "Could not download extension: $_"
        Write-Host "   Download manually from: https://github.com/cenmir/CanvasQuartoSync/releases" -ForegroundColor Yellow
    }
} else {
    Write-Warn "VS Code not found in PATH."
    Write-Host "   Install VS Code from https://code.visualstudio.com" -ForegroundColor Yellow
    Write-Host "   Then run: code.cmd --install-extension <path-to-vsix>" -ForegroundColor Yellow
}

# ============================================================================
#  Done
# ============================================================================
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "   Installation Complete!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "   Next steps:" -ForegroundColor Cyan
Write-Host "     1. Restart VS Code (close all windows and reopen)" -ForegroundColor White
Write-Host "     2. Click the graduation cap icon in the sidebar" -ForegroundColor White
Write-Host "     3. Click 'New Project' to set up your course" -ForegroundColor White
Write-Host ""
