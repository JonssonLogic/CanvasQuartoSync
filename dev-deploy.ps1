# ============================================================================
#  Dev Deploy - Build, install, and reload in one shot
#
#  Run from the repo root:
#    .\dev-deploy.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

$REPO_ROOT  = $PSScriptRoot
$EXT_DIR    = Join-Path $REPO_ROOT "extension"
$CLONE_DIR  = Join-Path $env:LOCALAPPDATA "CanvasQuartoSync"   # same as install.ps1
$VENV_DIR   = Join-Path $CLONE_DIR ".venv"

# --- Helpers ---
function Write-Step  { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$msg) Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Err   { param([string]$msg) Write-Host "   [ERROR] $msg" -ForegroundColor Red }

# ---- Step 1: Sync repo files to the install location ----
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

# The venv lives inside the install; create it on first deploy, and keep its
# packages in step with requirements.txt on every deploy.
$venvPython = Join-Path $VENV_DIR "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "Creating virtual environment at $VENV_DIR..."
    uv venv --python 3.13 $VENV_DIR
    if ($LASTEXITCODE -ne 0) { Write-Err "Could not create the venv (is uv installed?)."; exit 1 }
}
uv pip install --quiet --python $venvPython -r (Join-Path $CLONE_DIR "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Err "Package install failed."; exit 1 }
Write-Ok "Python packages up to date."

# cqs on the user PATH: the install's bin folder, which holds only cqs.
# Same as Add-CqsToUserPath in install.ps1: written as REG_EXPAND_SZ so other
# entries keep their %VARIABLES%, then a round trip that broadcasts the change.
$cqsBin = Join-Path $CLONE_DIR "bin"
$envKey = Get-Item 'HKCU:\Environment'
$userPath = $envKey.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
$pathParts = @($userPath -split ';' | Where-Object { $_ })
if (-not ($pathParts | Where-Object { $_.TrimEnd('\') -ieq $cqsBin })) {
    Set-ItemProperty -Path 'HKCU:\Environment' -Name Path -Value ((@($pathParts) + $cqsBin) -join ';') -Type ExpandString
    [Environment]::SetEnvironmentVariable('CQS_PATH_REFRESH', '1', 'User')
    [Environment]::SetEnvironmentVariable('CQS_PATH_REFRESH', $null, 'User')
    Write-Ok "Added $cqsBin to your PATH. New terminals have cqs."
} else {
    Write-Ok "cqs is on your PATH."
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

npx @vscode/vsce package --no-dependencies -o $vsixName
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
    try { & $c --version | Out-Null; if ($LASTEXITCODE -eq 0) { $codeCmd = $c; break } } catch {}
}

if (-not $codeCmd) {
    Write-Err "VS Code not found in PATH."
    exit 1
}

$vsixPath = Join-Path $EXT_DIR $vsixName
& $codeCmd --install-extension $vsixPath --force
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
