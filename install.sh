#!/bin/bash
# BountyHound Local - One-Command Installer
set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           BOUNTY HOUND LOCAL - INSTALLER                     ║"
echo "║      Autonomous Bug Bounty Hunting on H100 NVL               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# 1. System dependencies
echo "[1/7] Checking system dependencies..."
for cmd in python3 pip docker curl git; do
    if command -v $cmd &> /dev/null; then
        echo "  [+] $cmd found"
    else
        echo "  [!] $cmd NOT FOUND - please install it"
        exit 1
    fi
done

# Check NVIDIA GPU
if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
    echo "  [+] GPU: $GPU_NAME ($GPU_MEM)"
else
    echo "  [!] nvidia-smi not found - GPU required"
    exit 1
fi

# 2. Python virtual environment
echo ""
echo "[2/7] Setting up Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  [+] Virtual environment created"
fi
source venv/bin/activate
echo "  [+] Activated venv"

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
echo "  [*] Checking bountyhound tools..."
for tool in subfinder httpx nmap nuclei; do
    if command -v $tool &> /dev/null; then
        echo "    [+] $tool found"
    else
        echo "    [!] $tool not found - install with: go install github.com/projectdiscovery/$tool/v2/cmd/$tool@latest"
    fi
done

# 5. Install Playwright
echo ""
echo "[5/7] Installing Playwright browser..."
playwright install chromium --with-deps 2>/dev/null || python -m playwright install chromium
echo "  [+] Playwright chromium installed"

# 6. Start Docker services (Redis)
echo ""
echo "[6/7] Starting Redis..."
docker compose up -d redis
echo "  [+] Redis running on :6379"

# 7. Download models
echo ""
echo "[7/7] Downloading LLM models..."
echo "  This will download ~80GB of model weights."
echo "  Models: Qwen2.5-72B, Qwen2.5-14B, DeepSeek-7B, Mistral-7B, Phi-3-mini"
echo ""
read -p "  Download now? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    bash scripts/download-models.sh
else
    echo "  [*] Skipping model download. Run: bash scripts/download-models.sh"
fi

# Initialize database
echo ""
echo "[*] Initializing database..."
python -c "from src.database.models import init_db; init_db()"
echo "  [+] SQLite database initialized"

# Create directories
mkdir -p logs pids data/checkpoints
mkdir -p "$HOME/bounty-findings"

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
echo "║  4. Monitor:                                                 ║"
echo "║     Dashboard: http://localhost:8000                          ║"
echo "║     Flower:    http://localhost:5555                          ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
