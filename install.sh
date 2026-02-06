#!/bin/bash
# BountyHound Local - One-Command Installer
# Works on both bare metal and Vast.ai instances
set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           BOUNTY HOUND LOCAL - INSTALLER                     ║"
echo "║      Autonomous Bug Bounty Hunting on H100 NVL               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# Detect Vast.ai environment
IS_VAST_AI=false
if [ "${BHL_VAST_AI:-0}" = "1" ] || [ -f "/etc/vast-ai" ] || [ -d "/workspace" ]; then
    IS_VAST_AI=true
    echo "[*] Vast.ai environment detected"
    echo ""
fi

# 1. System dependencies
echo "[1/7] Checking system dependencies..."
for cmd in python3 pip curl git; do
    if command -v $cmd &> /dev/null; then
        echo "  [+] $cmd found"
    else
        echo "  [!] $cmd NOT FOUND - please install it"
        exit 1
    fi
done

# Docker only required on bare metal (not on Vast.ai)
if [ "$IS_VAST_AI" = false ]; then
    if command -v docker &> /dev/null; then
        echo "  [+] docker found"
    else
        echo "  [!] docker NOT FOUND - needed for Redis on bare metal"
        echo "  [*] On Vast.ai, Redis runs as a daemon (no Docker needed)"
        exit 1
    fi
fi

# Check NVIDIA GPU
if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
    echo "  [+] GPU: $GPU_NAME ($GPU_MEM)"
else
    echo "  [!] nvidia-smi not found - GPU required"
    exit 1
fi

# 2. Python virtual environment (skip on Vast.ai - already in container)
echo ""
echo "[2/7] Setting up Python environment..."
if [ "$IS_VAST_AI" = true ]; then
    echo "  [+] Using container Python (Vast.ai)"
else
    if [ ! -d "venv" ]; then
        python3 -m venv venv
        echo "  [+] Virtual environment created"
    fi
    source venv/bin/activate
    echo "  [+] Activated venv"
fi

# 3. Install Python dependencies
echo ""
echo "[3/7] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  [+] All dependencies installed"

# 4. Install bountyhound CLI
echo ""
echo "[4/7] Installing bountyhound CLI..."
if command -v bountyhound &> /dev/null; then
    echo "  [+] bountyhound already installed"
else
    pip install bountyhound -q
    echo "  [+] bountyhound installed"
fi

# Check bountyhound dependencies
echo "  [*] Checking recon tools..."
for tool in subfinder httpx nmap nuclei; do
    if command -v $tool &> /dev/null; then
        echo "    [+] $tool found"
    else
        echo "    [!] $tool not found"
        if [ "$tool" = "nmap" ]; then
            echo "      Install: apt install nmap"
        else
            echo "      Install: go install github.com/projectdiscovery/$tool/v2/cmd/$tool@latest"
        fi
    fi
done

# 5. Install Playwright
echo ""
echo "[5/7] Installing Playwright browser..."
playwright install chromium --with-deps 2>/dev/null || python -m playwright install chromium
echo "  [+] Playwright chromium installed"

# 6. Start Redis
echo ""
echo "[6/7] Starting Redis..."
if [ "$IS_VAST_AI" = true ]; then
    # Vast.ai: run Redis as daemon (no Docker-in-Docker)
    REDIS_DIR="${REDIS_DATA_DIR:-/workspace/redis-data}"
    mkdir -p "$REDIS_DIR"
    if command -v redis-server &> /dev/null; then
        redis-cli shutdown 2>/dev/null || true
        sleep 1
        redis-server --daemonize yes --appendonly yes --dir "$REDIS_DIR" --maxmemory 4gb --maxmemory-policy allkeys-lru
        echo "  [+] Redis daemon running on :6379"
    else
        echo "  [*] Installing Redis..."
        apt-get update -qq && apt-get install -y -qq redis-server > /dev/null 2>&1
        redis-server --daemonize yes --appendonly yes --dir "$REDIS_DIR" --maxmemory 4gb --maxmemory-policy allkeys-lru
        echo "  [+] Redis installed and running on :6379"
    fi
else
    # Bare metal: use Docker
    docker compose up -d redis
    echo "  [+] Redis running on :6379 (Docker)"
fi

# 7. Download models
echo ""
echo "[7/7] Downloading LLM models..."
echo "  Models: Qwen2.5-72B, Qwen2.5-14B, DeepSeek-7B, Mistral-7B, Phi-3-mini"
echo "  Total download: ~80GB"
echo ""

if [ "$IS_VAST_AI" = true ]; then
    # On Vast.ai, always download (non-interactive)
    echo "  [*] Downloading models to ${HF_HOME:-/workspace/models}..."
    bash scripts/download-models.sh
else
    read -p "  Download now? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        bash scripts/download-models.sh
    else
        echo "  [*] Skipping. Run: bash scripts/download-models.sh"
    fi
fi

# Initialize database
echo ""
echo "[*] Initializing database..."
python -c "from src.database.models import init_db; init_db()"
echo "  [+] SQLite database initialized"

# Create directories
mkdir -p logs pids data/checkpoints

if [ "$IS_VAST_AI" = true ]; then
    mkdir -p /workspace/bounty-findings
    FINDINGS_DIR="/workspace/bounty-findings"
else
    mkdir -p "$HOME/bounty-findings"
    FINDINGS_DIR="$HOME/bounty-findings"
fi

# Make scripts executable
chmod +x scripts/*.sh

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  INSTALLATION COMPLETE                                       ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Next steps:                                                 ║"
echo "║  1. Edit config/targets.yaml to add your targets             ║"
echo "║     OR: python cli.py add example.com --priority 8           ║"
echo "║                                                              ║"
echo "║  2. Start the system:                                        ║"
echo "║     ./scripts/start.sh                                       ║"
echo "║                                                              ║"
echo "║  3. Launch a hunt:                                           ║"
echo "║     python cli.py hunt example.com                           ║"
echo "║     OR: python cli.py swarm  (hunt all targets)              ║"
echo "║                                                              ║"
echo "║  Findings: $FINDINGS_DIR"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
