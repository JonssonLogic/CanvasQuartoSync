# ============================================================================
#  Canvas Quarto Sync - One-Line Installer (Windows PowerShell)
#
#  Usage:
#    irm https://raw.githubusercontent.com/JonssonLogic/CanvasQuartoSync/main/install.ps1 | iex
#
#  Interactive component selector, then fully automatic install.
# ============================================================================

# --- Configuration ---
# The tool is an app, not a project: it lives in the per-user app folder with
# its venv inside, so deleting one folder uninstalls it. Course folders, the
# VS Code extension and dev-deploy.ps1 all look here first.
$REPO_URL   = "https://github.com/JonssonLogic/CanvasQuartoSync.git"
$CLONE_DIR  = Join-Path $env:LOCALAPPDATA "CanvasQuartoSync"
$VENV_DIR   = Join-Path $CLONE_DIR ".venv"

# Earlier installers used these; reported at the end so they can be deleted.
$OLD_INSTALLS = @(
    (Join-Path $env:USERPROFILE "CanvasQuartoSync"),
    (Join-Path $env:USERPROFILE ".venvs\canvas_quarto_env"),
    (Join-Path $env:USERPROFILE "venvs\canvas_quarto_env")
)

# --- Enforce TLS 1.2 ---
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Helpers ---
function Write-Step  { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$msg) Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param([string]$msg) Write-Host "   [!] $msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$msg) Write-Host "   [ERROR] $msg" -ForegroundColor Red }

# ============================================================================
#  Interactive Selection Menu (arrow keys + spacebar)
# ============================================================================
function Show-InstallMenu {
    param([string[]]$MenuItems)

    $selected = [bool[]](@($true) * $MenuItems.Count)
    $pos = 0

    [Console]::CursorVisible = $false

    # Reserve space
    $totalLines = $MenuItems.Count + 2
    for ($i = 0; $i -lt $totalLines; $i++) { Write-Host "" }
    $curPos = $Host.UI.RawUI.CursorPosition
    $startPos = New-Object System.Management.Automation.Host.Coordinates(0, ($curPos.Y - $totalLines))

    while ($true) {
        $Host.UI.RawUI.CursorPosition = $startPos

        for ($i = 0; $i -lt $MenuItems.Count; $i++) {
            $isCurrent = ($i -eq $pos)
            $check = if ($selected[$i]) { "X" } else { " " }
            $prefix = if ($isCurrent) { ">" } else { " " }
            $color = if ($isCurrent) { "Yellow" } else { "Cyan" }
            $text = "  $prefix [$check] $($MenuItems[$i])"
            $pad = ' ' * [Math]::Max(0, [Console]::WindowWidth - $text.Length - 1)
            Write-Host "$text$pad" -ForegroundColor $color
        }

        Write-Host ""
        $help = "  Up/Down: navigate | Space: toggle | A: all | N: none | Enter: continue"
        Write-Host "$help$(' ' * [Math]::Max(0, [Console]::WindowWidth - $help.Length - 1))" -ForegroundColor DarkGray

        $key = [Console]::ReadKey($true)
        switch ($key.Key) {
            'UpArrow'   { $pos = if ($pos -gt 0) { $pos - 1 } else { $MenuItems.Count - 1 } }
            'DownArrow' { $pos = if ($pos -lt $MenuItems.Count - 1) { $pos + 1 } else { 0 } }
            'Spacebar'  { $selected[$pos] = -not $selected[$pos] }
            'A'         { for ($i = 0; $i -lt $selected.Count; $i++) { $selected[$i] = $true } }
            'N'         { for ($i = 0; $i -lt $selected.Count; $i++) { $selected[$i] = $false } }
            'Enter'     { [Console]::CursorVisible = $true; Write-Host ""; return $selected }
        }
    }
}

# ============================================================================
#  Banner + Component Selection
# ============================================================================
Write-Host ""
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host "   Canvas Quarto Sync - Installer v0.2.1"      -ForegroundColor Magenta
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "  Select components to install:"               -ForegroundColor White
Write-Host ""

$menuItems = @(
    'uv and Python'
    'Git'
    'Quarto (check only)'
    'Clone/update CanvasQuartoSync repository'
    'Python virtual environment + packages'
    'VS Code extension'
)

$choices = Show-InstallMenu $menuItems

$doPython  = $choices[0]
$doGit     = $choices[1]
$doQuarto  = $choices[2]
$doClone   = $choices[3]
$doVenv    = $choices[4]
$doVSCode  = $choices[5]

# Check if anything was selected
$anySelected = $false
foreach ($c in $choices) { if ($c) { $anySelected = $true; break } }
if (-not $anySelected) {
    Write-Host "  Nothing selected. Exiting." -ForegroundColor Yellow
    exit 0
}

# ============================================================================
#  Step 1 - uv and Python
# ============================================================================
if ($doPython) {
    Write-Step "Setting up uv and Python..."

    # Check if uv is available
    $uvLocalBinPath = Join-Path $env:USERPROFILE ".local\bin"
    $uvExePath = Join-Path $uvLocalBinPath "uv.exe"
    $uvAvailable = $false

    if (Test-Path $uvExePath) {
        if (-not ($env:Path -like "*$([System.Text.RegularExpressions.Regex]::Escape($uvLocalBinPath))*")) {
            $env:Path += ";$uvLocalBinPath"
        }
        $uvAvailable = $true
        Write-Ok "uv already installed."
    } else {
        $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($uvCmd) {
            $uvAvailable = $true
            Write-Ok "uv already installed."
        } else {
            Write-Host "   Installing uv..." -ForegroundColor White
            try {
                Invoke-Expression "powershell -ExecutionPolicy ByPass -c 'irm https://astral.sh/uv/install.ps1 | iex'"
                if (Test-Path $uvLocalBinPath) {
                    $env:Path += ";$uvLocalBinPath"
                }
                $uvAvailable = $true
                Write-Ok "uv installed."
            } catch {
                Write-Err "Failed to install uv: $_"
                exit 1
            }
        }
    }

    # Install Python via uv
    if ($uvAvailable) {
        Write-Host "   Installing Python 3.13 via uv..." -ForegroundColor White
        uv python install 3.13
        if ($LASTEXITCODE -eq 0) {
            # Use uv python find to get the actual path (avoids Windows Store stub)
            $pythonPath = (uv python find 3.13 2>$null)
            if ($pythonPath -and (Test-Path $pythonPath)) {
                $pythonVersion = & $pythonPath --version
                Write-Ok "Python installed: $pythonVersion"
            } else {
                Write-Ok "Python 3.13 installed via uv."
            }
        } else {
            Write-Err "Failed to install Python via uv."
            exit 1
        }
    }
}

# ============================================================================
#  Step 2 - Quarto
# ============================================================================
if ($doQuarto) {
    Write-Step "Checking for Quarto CLI..."
    try {
        $ver = & quarto --version 2>&1
        if ($LASTEXITCODE -eq 0) { Write-Ok "Found: Quarto $ver" }
        else { throw "not found" }
    } catch {
        Write-Warn "Quarto not found. Install from https://quarto.org/docs/get-started/"
        Write-Host "   (Needed to render .qmd files, but you can install it later)" -ForegroundColor Yellow
    }
}

# ============================================================================
#  Step 3 - Git
# ============================================================================
$gitFound = $false
try { $ver = & git --version 2>&1; if ($LASTEXITCODE -eq 0) { $gitFound = $true } } catch {}

if ($doGit) {
    Write-Step "Setting up Git..."
    if ($gitFound) {
        Write-Ok "Found: $(git --version)"
    } else {
        Write-Host "   Installing Git via winget..." -ForegroundColor White
        try {
            & winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            $gitFound = $true
            Write-Ok "Git installed."
        } catch {
            Write-Err "Failed to install Git. Install from https://git-scm.com/download/win"
            exit 1
        }
    }
}

# ============================================================================
#  Step 4 - Clone Repository
# ============================================================================
if ($doClone) {
    Write-Step "Setting up CanvasQuartoSync..."

    if (Test-Path (Join-Path $CLONE_DIR ".git")) {
        Write-Ok "Already installed at $CLONE_DIR"
        Push-Location $CLONE_DIR
        & git pull 2>&1 | Out-Null
        Pop-Location
        Write-Ok "Updated to latest version."
    } elseif (Test-Path (Join-Path $CLONE_DIR "sync_to_canvas.py")) {
        # dev-deploy.ps1 copies a working tree here without .git
        Write-Warn "$CLONE_DIR holds a copy that is not a git clone (dev-deploy.ps1?). Left as is."
    } else {
        & git clone $REPO_URL $CLONE_DIR 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Err "Failed to clone repository."; exit 1 }
        Write-Ok "Repository cloned to $CLONE_DIR"
    }
}

# ============================================================================
#  Step 5 - Virtual Environment + Packages
# ============================================================================
if ($doVenv) {
    Write-Step "Setting up Python environment..."

    # Check uv is available (required for venv creation)
    $uvAvailable = $false
    try { uv --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $uvAvailable = $true } } catch {}

    if (-not $uvAvailable) {
        Write-Err "uv is required for this step. Select 'uv and Python' in the menu or install uv manually."
        exit 1
    }

    $venvPython = Join-Path $VENV_DIR "Scripts\python.exe"
    $requirementsFile = Join-Path $CLONE_DIR "requirements.txt"

    if (-not (Test-Path $venvPython)) {
        Write-Host "   Creating virtual environment..." -ForegroundColor White
        uv venv --clear --python 3.13 $VENV_DIR
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Failed to create virtual environment."
            exit 1
        }
        Write-Ok "Virtual environment created at $VENV_DIR"
    } else {
        Write-Ok "Virtual environment exists at $VENV_DIR"
    }

    # Install packages. --python targets the venv without activating it:
    # activation would change PATH for the rest of the user's PowerShell window.
    if (Test-Path $requirementsFile) {
        Write-Host "   Installing packages..." -ForegroundColor White
        uv pip install --python $venvPython -r $requirementsFile
        if ($LASTEXITCODE -ne 0) { Write-Err "Package installation failed."; exit 1 }
        Write-Ok "Python packages installed."
    } else {
        Write-Warn "requirements.txt not found. Clone the repository first."
    }
}

# ============================================================================
#  Step 6 - VS Code Extension
# ============================================================================
if ($doVSCode) {
    Write-Step "Installing VS Code extension..."

    $codeCmd = $null
    foreach ($c in @("code.cmd", "code")) {
        try { & $c --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $codeCmd = $c; break } } catch {}
    }

    if ($codeCmd) {
        $vsixPath = Join-Path $env:TEMP "canvasquartosync.vsix"
        try {
            Write-Host "   Downloading extension from GitHub..." -ForegroundColor White
            $release = Invoke-RestMethod -Uri "https://api.github.com/repos/JonssonLogic/CanvasQuartoSync/releases/latest" -Headers @{ Accept = "application/vnd.github.v3+json" }
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
                }
                Remove-Item $vsixPath -ErrorAction SilentlyContinue
            } else {
                Write-Warn "No .vsix in latest release. Download from https://github.com/JonssonLogic/CanvasQuartoSync/releases"
            }
        } catch {
            Write-Warn "Could not download extension: $_"
            Write-Host "   Download manually from: https://github.com/JonssonLogic/CanvasQuartoSync/releases" -ForegroundColor Yellow
        }
    } else {
        Write-Warn "VS Code not found in PATH."
        Write-Host "   Install from https://code.visualstudio.com" -ForegroundColor Yellow
    }
}

# ============================================================================
#  Done
# ============================================================================
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "   Installation Complete!"                     -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "   Next steps:"                                -ForegroundColor Cyan
Write-Host "     1. Restart VS Code (close all windows and reopen)" -ForegroundColor White
Write-Host "     2. Click the graduation cap icon in the sidebar"   -ForegroundColor White
Write-Host "     3. Click 'New Project' to set up your course"      -ForegroundColor White
Write-Host ""

# ============================================================================
#  Earlier installs
# ============================================================================
$found = @($OLD_INSTALLS | Where-Object { Test-Path $_ })
if ($found.Count -gt 0) {
    Write-Host "   Earlier install(s) found. Nothing uses them once your course" -ForegroundColor Yellow
    Write-Host "   folders are refreshed, so they can be deleted:"              -ForegroundColor Yellow
    foreach ($dir in $found) { Write-Host "     $dir" -ForegroundColor White }
    Write-Host ""
    Write-Host "   Refresh each course folder once, so its check_content and update_kit" -ForegroundColor Yellow
    Write-Host "   find this install (they look it up themselves from then on):"        -ForegroundColor Yellow
    Write-Host "     & `"$VENV_DIR\Scripts\python.exe`" `"$CLONE_DIR\init_content_project.py`" <course folder> --update" -ForegroundColor White
    Write-Host ""
}
