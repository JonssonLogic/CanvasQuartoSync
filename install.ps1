# ============================================================================
#  Canvas Quarto Sync — One-Line Installer (Windows PowerShell)
#
#  Usage:
#    irm https://raw.githubusercontent.com/JonssonLogic/CanvasQuartoSync/main/install.ps1 | iex
#
#  Or run locally:
#    powershell -ExecutionPolicy Bypass -File install.ps1
# ============================================================================

# --- Configuration ---
$REPO_URL   = "https://github.com/JonssonLogic/CanvasQuartoSync.git"
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

function Ask-YesNo {
    param([string]$question)
    $answer = Read-Host "$question [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

# ============================================================================
#  Banner
# ============================================================================
Write-Host ""
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host "   Canvas Quarto Sync - Installer" -ForegroundColor Magenta
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "This script will set up everything you need to run Canvas Quarto Sync:" -ForegroundColor White
Write-Host "  - Check for Python, Quarto, and Git" -ForegroundColor White
Write-Host "  - Clone the repository" -ForegroundColor White
Write-Host "  - Create a virtual environment and install packages" -ForegroundColor White
Write-Host "  - Help you configure your Canvas API credentials" -ForegroundColor White
Write-Host ""
Write-Host "You will be asked Y/N questions along the way." -ForegroundColor Yellow
Write-Host "Type your answer and press Enter after each." -ForegroundColor Yellow
Write-Host ""

# ============================================================================
#  Step 1 — Check Python
# ============================================================================
Write-Step "Checking for Python..."

$pythonCmd = $null
try {
    $ver = & python --version 2>&1
    if ($ver -match "Python \d") {
        $pythonCmd = "python"
        Write-Ok "Found: $ver"
    }
} catch {}

if (-not $pythonCmd) {
    try {
        $ver = & python3 --version 2>&1
        if ($ver -match "Python \d") {
            $pythonCmd = "python3"
            Write-Ok "Found: $ver"
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Warn "Python was not found on your system."
    if (Ask-YesNo "Install Python via uv (recommended)?") {
        Write-Step "Installing uv..."
        try {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            # Refresh PATH so uv is available
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            Write-Ok "uv installed."
        } catch {
            Write-Err "Failed to install uv: $_"
            Write-Host "   Please install Python manually from https://www.python.org/downloads/" -ForegroundColor Yellow
            Write-Host "   Then re-run this installer." -ForegroundColor Yellow
            exit 1
        }

        Write-Step "Installing Python 3.13 via uv..."
        try {
            & uv python install 3.13
            $pythonCmd = "python"
            Write-Ok "Python 3.13 installed via uv."
        } catch {
            Write-Err "Failed to install Python via uv: $_"
            exit 1
        }
    } else {
        Write-Err "Python is required. Install it from https://www.python.org/downloads/ and re-run this script."
        exit 1
    }
}

# ============================================================================
#  Step 2 — Check Quarto
# ============================================================================
Write-Step "Checking for Quarto CLI..."

$quartoFound = $false
try {
    $ver = & quarto --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $quartoFound = $true
        Write-Ok "Found: Quarto $ver"
    }
} catch {}

if (-not $quartoFound) {
    Write-Warn "Quarto CLI was not found on your system."
    Write-Host "   Quarto is required to render .qmd files to HTML." -ForegroundColor Yellow
    if (Ask-YesNo "Open the Quarto download page in your browser?") {
        Start-Process "https://quarto.org/docs/get-started/"
        Write-Host "   Install Quarto, then continue or re-run this script." -ForegroundColor Yellow
        Write-Host ""
        Read-Host "Press Enter to continue after installing Quarto (or skip for now)"
    } else {
        Write-Warn "Skipping Quarto. You will need it before running the sync tool."
        Write-Host "   Download from: https://quarto.org/docs/get-started/" -ForegroundColor Yellow
    }
}

# ============================================================================
#  Step 3 — Check Git
# ============================================================================
Write-Step "Checking for Git..."

$gitFound = $false
try {
    $ver = & git --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $gitFound = $true
        Write-Ok "Found: $ver"
    }
} catch {}

if (-not $gitFound) {
    Write-Warn "Git was not found on your system."
    if (Ask-YesNo "Install Git via winget?") {
        Write-Step "Installing Git..."
        try {
            & winget install --id Git.Git -e --source winget
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            $gitFound = $true
            Write-Ok "Git installed."
        } catch {
            Write-Warn "winget install failed. Opening Git download page instead..."
            Start-Process "https://git-scm.com/download/win"
            Write-Host "   Install Git, then re-run this script." -ForegroundColor Yellow
            exit 1
        }
    } else {
        Write-Err "Git is required to clone the repository."
        Write-Host "   Download from: https://git-scm.com/download/win" -ForegroundColor Yellow
        exit 1
    }
}

# ============================================================================
#  Step 4 — Clone Repository
# ============================================================================
Write-Step "Setting up project directory..."

# Ensure venv root exists
if (-not (Test-Path $VENV_ROOT)) {
    New-Item -ItemType Directory -Path $VENV_ROOT -Force | Out-Null
    Write-Ok "Created directory: $VENV_ROOT"
}

if (-not (Test-Path $VENV_DIR)) {
    New-Item -ItemType Directory -Path $VENV_DIR -Force | Out-Null
}

if (Test-Path (Join-Path $CLONE_DIR ".git")) {
    Write-Ok "Repository already cloned at $CLONE_DIR"
    Write-Host "   Pulling latest changes..." -ForegroundColor White
    try {
        Push-Location $CLONE_DIR
        & git pull
        Pop-Location
        Write-Ok "Updated to latest version."
    } catch {
        Pop-Location
        Write-Warn "Could not pull updates: $_"
    }
} else {
    Write-Host "   Cloning into: $CLONE_DIR" -ForegroundColor White
    try {
        & git clone $REPO_URL $CLONE_DIR
        if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
        Write-Ok "Repository cloned successfully."
    } catch {
        Write-Err "Failed to clone repository: $_"
        exit 1
    }
}

# ============================================================================
#  Step 5 — Create Virtual Environment
# ============================================================================
Write-Step "Setting up virtual environment..."

$venvActivate = Join-Path $VENV_DIR "Scripts\Activate.ps1"
$requirementsFile = Join-Path $CLONE_DIR "requirements.txt"

$createVenv = $true
if (Test-Path $venvActivate) {
    Write-Warn "A virtual environment already exists at $VENV_DIR"
    if (Ask-YesNo "Recreate it from scratch?") {
        Write-Host "   Removing old virtual environment..." -ForegroundColor White
        Remove-Item -Path (Join-Path $VENV_DIR "Scripts") -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $VENV_DIR "Lib") -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $VENV_DIR "pyvenv.cfg") -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $VENV_DIR "Include") -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Ok "Reusing existing virtual environment."
        $createVenv = $false
    }
}

if ($createVenv) {
    # Try uv first, fall back to python -m venv
    $uvAvailable = $false
    try {
        & uv --version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $uvAvailable = $true }
    } catch {}

    if ($uvAvailable) {
        Write-Host "   Creating venv with uv..." -ForegroundColor White
        try {
            & uv venv --python 3.13 $VENV_DIR
            if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
            Write-Ok "Virtual environment created with uv."
        } catch {
            Write-Warn "uv venv failed, falling back to python -m venv..."
            & $pythonCmd -m venv $VENV_DIR
            if ($LASTEXITCODE -ne 0) {
                Write-Err "Failed to create virtual environment."
                exit 1
            }
            Write-Ok "Virtual environment created with python -m venv."
        }
    } else {
        Write-Host "   Creating venv with python..." -ForegroundColor White
        & $pythonCmd -m venv $VENV_DIR
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Failed to create virtual environment."
            exit 1
        }
        Write-Ok "Virtual environment created."
    }
}

# ============================================================================
#  Step 6 — Install Packages
# ============================================================================
Write-Step "Installing Python packages..."

# Activate the venv
try {
    & $venvActivate
} catch {
    Write-Warn "Could not activate venv via script. Setting PATH manually..."
    $env:Path = (Join-Path $VENV_DIR "Scripts") + ";" + $env:Path
    $env:VIRTUAL_ENV = $VENV_DIR
}

# Install from requirements.txt
$uvAvailable = $false
try {
    & uv --version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $uvAvailable = $true }
} catch {}

if ($uvAvailable) {
    Write-Host "   Installing packages with uv pip..." -ForegroundColor White
    & uv pip install -r $requirementsFile
} else {
    Write-Host "   Installing packages with pip..." -ForegroundColor White
    & pip install -r $requirementsFile
}

if ($LASTEXITCODE -ne 0) {
    Write-Err "Package installation failed."
    exit 1
}

Write-Ok "All packages installed."

# ============================================================================
#  Step 7 — Patch run_sync_here.bat
# ============================================================================
Write-Step "Updating run_sync_here.bat with install paths..."

$batFile = Join-Path $CLONE_DIR "run_sync_here.bat"

if (Test-Path $batFile) {
    $batContent = Get-Content $batFile -Raw

    # Replace the PROJECT_DIR line
    $batContent = $batContent -replace `
        '(?m)^set "PROJECT_DIR=.*"', `
        "set `"PROJECT_DIR=$CLONE_DIR`""

    # Replace the python+script execution line to use the venv outside PROJECT_DIR
    $batContent = $batContent -replace `
        '(?m)^"%PROJECT_DIR%\\\.venv\\Scripts\\python\.exe".*', `
        "`"$VENV_DIR\Scripts\python.exe`" `"%PROJECT_DIR%\sync_to_canvas.py`" `"%~dp0.`" %*"

    Set-Content -Path $batFile -Value $batContent -NoNewline
    Write-Ok "run_sync_here.bat updated:"
    Write-Host "   PROJECT_DIR : $CLONE_DIR" -ForegroundColor White
    Write-Host "   Python      : $VENV_DIR\Scripts\python.exe" -ForegroundColor White
} else {
    Write-Warn "run_sync_here.bat not found in cloned repo - skipping patch."
}

# ============================================================================
#  Step 8 — Configure Canvas Environment Variables
# ============================================================================
Write-Step "Configuring Canvas API credentials..."

$skipCredentials = $false

# --- CANVAS_API_URL ---
Write-Host ""
Write-Host "   --- Canvas URL ---" -ForegroundColor White
Write-Host "   You need your Canvas URL. Open your browser, log in to Canvas," -ForegroundColor White
Write-Host "   and copy the base URL from the address bar." -ForegroundColor White
Write-Host "   It looks like: https://yourschool.instructure.com" -ForegroundColor Yellow
Write-Host ""

$canvasUrl = Read-Host "   Paste your Canvas URL (or press Enter to skip)"

if ($canvasUrl -ne "") {
    # Strip trailing slash
    $canvasUrl = $canvasUrl.TrimEnd("/")
    setx CANVAS_API_URL $canvasUrl | Out-Null
    $env:CANVAS_API_URL = $canvasUrl
    Write-Ok "CANVAS_API_URL set to: $canvasUrl"
} else {
    Write-Warn "Skipped. You will need to set CANVAS_API_URL before running the sync."
    $skipCredentials = $true
}

# --- CANVAS_API_TOKEN ---
Write-Host ""
Write-Host "   --- Canvas API Token ---" -ForegroundColor White
Write-Host "   Follow these steps to generate a token:" -ForegroundColor White
Write-Host "   1. Log in to Canvas in your browser" -ForegroundColor White
Write-Host "   2. Go to Account -> Settings" -ForegroundColor White
Write-Host "   3. Under 'Approved integrations', click '+ New access token'" -ForegroundColor White
Write-Host "   4. Enter a purpose (e.g. 'CanvasQuartoSync') and click 'Generate token'" -ForegroundColor White
Write-Host "   5. IMPORTANT: Copy the token string now - you won't be able to see it again!" -ForegroundColor Yellow
Write-Host ""

$canvasToken = Read-Host "   Paste your Canvas API token (or press Enter to skip)"

if ($canvasToken -ne "") {
    setx CANVAS_API_TOKEN $canvasToken | Out-Null
    $env:CANVAS_API_TOKEN = $canvasToken
    Write-Ok "CANVAS_API_TOKEN has been set."
} else {
    Write-Warn "Skipped. You will need to set CANVAS_API_TOKEN before running the sync."
    $skipCredentials = $true
}

if ($skipCredentials) {
    Write-Host ""
    Write-Host "   To set credentials later, see:" -ForegroundColor Yellow
    Write-Host "   $CLONE_DIR\Guides\Canvas_token_setup.md" -ForegroundColor Yellow
    Write-Host "   Or run these commands in PowerShell:" -ForegroundColor Yellow
    Write-Host "     setx CANVAS_API_URL `"https://yourschool.instructure.com`"" -ForegroundColor Gray
    Write-Host "     setx CANVAS_API_TOKEN `"your_token_here`"" -ForegroundColor Gray
}

# ============================================================================
#  Summary
# ============================================================================
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "   Installation Complete!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "   Project location : $CLONE_DIR" -ForegroundColor White
Write-Host "   Virtual env      : $VENV_DIR" -ForegroundColor White
Write-Host ""
Write-Host "   To activate the environment:" -ForegroundColor Cyan
Write-Host "     & $venvActivate" -ForegroundColor Gray
Write-Host ""
Write-Host "   To sync your course:" -ForegroundColor Cyan
Write-Host "     cd $CLONE_DIR" -ForegroundColor Gray
Write-Host "     python sync_to_canvas.py <your_content_folder>" -ForegroundColor Gray
Write-Host ""
Write-Host "   For full documentation, see:" -ForegroundColor Cyan
Write-Host "     $CLONE_DIR\Guides\Canvas_Sync_User_Guide.md" -ForegroundColor Gray
Write-Host ""
