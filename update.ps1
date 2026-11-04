# ============================================================================
#  Canvas Quarto Sync - Update Script
#
#  Usage (one-liner):
#    irm https://raw.githubusercontent.com/JonssonLogic/CanvasQuartoSync/main/update.ps1 | iex
#
#  Updates the repo, installs latest VSIX, and updates Python packages.
# ============================================================================

$CLONE_DIR  = Join-Path $env:LOCALAPPDATA "CanvasQuartoSync"
$VENV_DIR   = Join-Path $CLONE_DIR ".venv"
$REPO_URL   = "https://github.com/JonssonLogic/CanvasQuartoSync.git"

# Earlier installers used these. If one exists and the new location doesn't,
# this machine still needs install.ps1, which sets up the new layout.
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

# Same as Add-CqsToUserPath in install.ps1; keep them in step. An install made
# before cqs existed gets it on the PATH by updating.
function Add-CqsToUserPath {
    param([string]$BinDir)
    $key = Get-Item 'HKCU:\Environment'
    $raw = $key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
    $parts = @($raw -split ';' | Where-Object { $_ })
    if ($parts | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') }) { return $false }
    Set-ItemProperty -Path 'HKCU:\Environment' -Name Path -Value ((@($parts) + $BinDir) -join ';') -Type ExpandString
    [Environment]::SetEnvironmentVariable('CQS_PATH_REFRESH', '1', 'User')
    [Environment]::SetEnvironmentVariable('CQS_PATH_REFRESH', $null, 'User')
    return $true
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "   Canvas Quarto Sync - Updater"              -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

$oldFound = @($OLD_INSTALLS | Where-Object { Test-Path $_ })
if (-not (Test-Path $CLONE_DIR) -and $oldFound.Count -gt 0) {
    Write-Warn "CanvasQuartoSync now installs to $CLONE_DIR."
    Write-Host "   This machine has the earlier layout. Run install.ps1 once to move over:" -ForegroundColor Yellow
    Write-Host "     irm https://raw.githubusercontent.com/JonssonLogic/CanvasQuartoSync/main/install.ps1 | iex" -ForegroundColor White
    exit 1
}

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
# A dev build's stamp (dev-deploy.ps1) no longer describes this copy.
Remove-Item (Join-Path $CLONE_DIR "BUILD") -ErrorAction SilentlyContinue

# ---- cqs on the PATH ----
$cqsBin = Join-Path $CLONE_DIR "bin"
if (Test-Path (Join-Path $cqsBin "cqs.cmd")) {
    if (Add-CqsToUserPath $cqsBin) {
        Write-Ok "Added $cqsBin to your PATH."
        Write-Warn "In VS Code: reload the window (Developer: Reload Window), then open a new terminal. Restart Windows Terminal."
    }
    if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $cqsBin })) {
        $env:Path = "$env:Path;$cqsBin"
    }
}

# ---- Step 2: Update Python packages ----
Write-Step "Updating Python packages..."

$venvPython = Join-Path $VENV_DIR "Scripts\python.exe"
$requirementsFile = Join-Path $CLONE_DIR "requirements.txt"

if (Test-Path $venvPython) {
    # --python targets the venv without activating it, which would change
    # PATH for the rest of the user's PowerShell window.
    if (Test-Path $requirementsFile) {
        uv pip install --python $venvPython --upgrade -r $requirementsFile
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
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/JonssonLogic/CanvasQuartoSync/releases/latest" -Headers @{ Accept = "application/vnd.github.v3+json" }
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

if ($oldFound.Count -gt 0) {
    Write-Host "   Earlier install(s) still on disk; nothing uses them now, so they can be deleted:" -ForegroundColor Yellow
    foreach ($dir in $oldFound) { Write-Host "     $dir" -ForegroundColor White }
    Write-Host ""
}
