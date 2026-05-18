#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# Gemma 4 RSS Intelligence Monitor — One-Command Installer
# ═══════════════════════════════════════════════════════════
# Tested on: Ubuntu 22.04, Ubuntu 24.04, Debian 12, macOS 14
# Requirements: Python 3.9+, curl, 3GB free RAM

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓ $*${NC}"; }
info() { echo -e "${BLUE}→ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
fail() { echo -e "${RED}✗ $*${NC}"; exit 1; }

echo ""
echo "════════════════════════════════════════════════════════"
echo "   Gemma 4 RSS Intelligence Monitor — Installer"
echo "════════════════════════════════════════════════════════"
echo ""

# ── Python check ──────────────────────────────────────────
info "Checking Python..."
if ! command -v python3 &>/dev/null; then
    fail "Python 3 not found. Install it: https://python.org"
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
PY_MINOR=$(echo $PY_VER | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]); then
    fail "Python 3.9+ required. Found: $PY_VER"
fi
ok "Python $PY_VER"

# ── Ollama check / install ─────────────────────────────────
info "Checking Ollama..."
if command -v ollama &>/dev/null; then
    ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
else
    warn "Ollama not found."
    read -p "Install Ollama now? (y/N) " -n 1 -r; echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
        ok "Ollama installed"
    else
        fail "Ollama is required. Install from https://ollama.com"
    fi
fi

# ── Start Ollama ──────────────────────────────────────────
if ! pgrep -x "ollama" &>/dev/null; then
    info "Starting Ollama service..."
    ollama serve &>/dev/null &
    sleep 3
    ok "Ollama started"
else
    ok "Ollama already running"
fi

# ── Pull Gemma 4 E4B model ────────────────────────────────
info "Checking Gemma 4 E4B model..."
if ollama list 2>/dev/null | grep -q "gemma4"; then
    ok "Gemma 4 model already downloaded"
else
    echo ""
    warn "Downloading Gemma 4 E4B model (~2.5 GB). This takes 3–10 minutes."
    warn "E4B = Edge 4B: 4 billion parameters, optimised for CPU, 128K context."
    echo ""
    ollama pull gemma4:e4b
    ok "Gemma 4 E4B downloaded"
fi

# ── Python venv + deps ────────────────────────────────────
info "Setting up Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
ok "Python dependencies installed"

# ── Config check ──────────────────────────────────────────
if [ ! -f "config.yaml" ]; then
    fail "config.yaml not found. Are you in the right directory?"
fi
ok "config.yaml found"

# ── Test run ──────────────────────────────────────────────
echo ""
info "Running configuration check..."
python3 monitor.py --check
echo ""

echo "════════════════════════════════════════════════════════"
ok "Installation complete!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "  1. (Optional) Add your Slack webhook to config.yaml:"
echo "     nano config.yaml"
echo ""
echo "  2. Test a manual run:"
echo "     source venv/bin/activate"
echo "     python3 monitor.py --hours 24 --dry-run"
echo ""
echo "  3. Set up automated monitoring (every 6 hours):"
echo "     crontab -e"
echo ""
echo "     Add this line:"
echo "     0 */6 * * * cd $(pwd) && ./venv/bin/python3 monitor.py >> monitor.log 2>&1"
echo ""
echo "  Full docs: https://github.com/YOUR_USERNAME/gemma4-rss-intelligence"
echo ""
