#!/usr/bin/env bash
# ============================================================================
#  Canvas Quarto Sync — One-Line Installer (Linux/macOS)
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/cenmir/CanvasQuartoSync/main/install.sh | bash
# ============================================================================

set -e

REPO_URL="https://github.com/cenmir/CanvasQuartoSync.git"
VENV_ROOT="$HOME/venvs"
VENV_DIR="$VENV_ROOT/canvas_quarto_env"
CLONE_DIR="$VENV_DIR/CanvasQuartoSync"

step()  { echo -e "\n\033[36m>> $1\033[0m"; }
ok()    { echo -e "   \033[32m[OK]\033[0m $1"; }
warn()  { echo -e "   \033[33m[!]\033[0m $1"; }
err()   { echo -e "   \033[31m[ERROR]\033[0m $1"; }

# ============================================================================
echo ""
echo -e "\033[35m=============================================\033[0m"
echo -e "\033[35m   Canvas Quarto Sync — Installer\033[0m"
echo -e "\033[35m=============================================\033[0m"
echo ""
echo "  Components to install (enter numbers to SKIP, or press Enter for all):"
echo ""
echo "    1. Python (via uv)"
echo "    2. Git"
echo "    3. Quarto (check only)"
echo "    4. Python virtual environment + packages"
echo "    5. Clone/update CanvasQuartoSync repository"
echo "    6. VS Code extension"
echo ""
read -p "  Skip (e.g. 1,3) or Enter to install all: " skip_input

do_python=true; do_git=true; do_quarto=true; do_venv=true; do_clone=true; do_vscode=true
for n in $(echo "$skip_input" | tr ',' ' '); do
    case "$n" in
        1) do_python=false ;;
        2) do_git=false ;;
        3) do_quarto=false ;;
        4) do_venv=false ;;
        5) do_clone=false ;;
        6) do_vscode=false ;;
    esac
done

# ============================================================================
#  Step 1 — Python
# ============================================================================
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$($cmd --version 2>&1)
        if [[ "$ver" == Python* ]]; then PYTHON_CMD="$cmd"; break; fi
    fi
done

if $do_python; then
    step "Setting up Python..."
    if [ -n "$PYTHON_CMD" ]; then
        ok "Found: $($PYTHON_CMD --version 2>&1)"
    else
        echo "   Installing Python via uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"
        uv python install 3.13
        PYTHON_CMD="python3"
        ok "Python 3.13 installed via uv."
    fi
fi

# ============================================================================
#  Step 2 — Quarto
# ============================================================================
if $do_quarto; then
    step "Checking for Quarto CLI..."
    if command -v quarto &>/dev/null; then
        ok "Found: Quarto $(quarto --version)"
    else
        warn "Quarto not found. Install from https://quarto.org/docs/get-started/"
    fi
fi

# ============================================================================
#  Step 3 — Git
# ============================================================================
if $do_git; then
    step "Setting up Git..."
    if command -v git &>/dev/null; then
        ok "Found: $(git --version)"
    else
        echo "   Installing Git..."
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y -qq git
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y -q git
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm git
        elif command -v brew &>/dev/null; then
            brew install git
        else
            err "Could not install Git. Install manually and re-run."
            exit 1
        fi
        ok "Git installed."
    fi
fi

# ============================================================================
#  Step 4 — Clone Repository
# ============================================================================
if $do_clone; then
    step "Setting up CanvasQuartoSync..."
    mkdir -p "$VENV_ROOT" "$VENV_DIR"

    if [ -d "$CLONE_DIR/.git" ]; then
        ok "Already installed at $CLONE_DIR"
        cd "$CLONE_DIR" && git pull -q && cd - >/dev/null
        ok "Updated to latest version."
    else
        git clone -q "$REPO_URL" "$CLONE_DIR"
        ok "Repository cloned."
    fi
fi

# ============================================================================
#  Step 5 — Virtual Environment + Packages
# ============================================================================
if $do_venv; then
    step "Setting up Python environment..."

    if [ -z "$PYTHON_CMD" ]; then
        err "Python is required. Select Python in the menu or install it manually."
        exit 1
    fi

    if [ ! -f "$VENV_DIR/bin/activate" ]; then
        if command -v uv &>/dev/null; then
            uv venv --python 3.13 "$VENV_DIR" 2>/dev/null || $PYTHON_CMD -m venv "$VENV_DIR"
        else
            $PYTHON_CMD -m venv "$VENV_DIR"
        fi
        ok "Virtual environment created."
    else
        ok "Virtual environment exists."
    fi

    source "$VENV_DIR/bin/activate"

    if [ -f "$CLONE_DIR/requirements.txt" ]; then
        if command -v uv &>/dev/null; then
            uv pip install -r "$CLONE_DIR/requirements.txt" -q
        else
            pip install -r "$CLONE_DIR/requirements.txt" -q
        fi
        ok "Python packages installed."
    else
        warn "requirements.txt not found. Clone the repository first."
    fi
fi

# ============================================================================
#  Step 6 — VS Code Extension
# ============================================================================
if $do_vscode; then
    step "Installing VS Code extension..."

    CODE_CMD=""
    for cmd in code code-insiders; do
        if command -v "$cmd" &>/dev/null; then CODE_CMD="$cmd"; break; fi
    done

    if [ -n "$CODE_CMD" ]; then
        VSIX_PATH="/tmp/canvasquartosync.vsix"
        DOWNLOAD_URL=$(curl -fsSL "https://api.github.com/repos/cenmir/CanvasQuartoSync/releases/latest" \
            | grep -o '"browser_download_url": "[^"]*\.vsix"' \
            | head -1 \
            | cut -d'"' -f4)

        if [ -n "$DOWNLOAD_URL" ]; then
            curl -fsSL "$DOWNLOAD_URL" -o "$VSIX_PATH"
            $CODE_CMD --install-extension "$VSIX_PATH" --force 2>/dev/null && \
                ok "VS Code extension installed! Restart VS Code to activate." || \
                warn "Extension install failed. Try: $CODE_CMD --install-extension $VSIX_PATH"
            rm -f "$VSIX_PATH"
        else
            warn "No .vsix in latest release. Download from https://github.com/cenmir/CanvasQuartoSync/releases"
        fi
    else
        warn "VS Code not found in PATH."
        echo "   Install from https://code.visualstudio.com"
    fi
fi

# ============================================================================
#  Done
# ============================================================================
echo ""
echo -e "\033[32m=============================================\033[0m"
echo -e "\033[32m   Installation Complete!\033[0m"
echo -e "\033[32m=============================================\033[0m"
echo ""
echo "   Next steps:"
echo "     1. Restart VS Code (close all windows and reopen)"
echo "     2. Click the graduation cap icon in the sidebar"
echo "     3. Click 'New Project' to set up your course"
echo ""
