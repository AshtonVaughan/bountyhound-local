#!/bin/bash
# BountyHound Local - Vast.ai On-Start Bootstrap
# This script runs automatically when a Vast.ai instance starts.
# It handles first-time setup AND resume after stop/start.
set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           BOUNTY HOUND LOCAL - VAST.AI BOOTSTRAP             ║"
echo "║              H100 NVL GPU Instance Setup                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"

# ── Environment Setup ──────────────────────────────────────────────
export HF_HOME="${HF_HOME:-/workspace/models}"
export BHL_DB_PATH="${BHL_DB_PATH:-/workspace/data/bountyhound.db}"
export BHL_VAST_AI=1
export PATH="$PATH:/root/go/bin:$HOME/go/bin"

mkdir -p /workspace/models /workspace/data /workspace/bounty-findings /workspace/redis-data

# ── 1. Start Redis ─────────────────────────────────────────────────
echo "[1/6] Starting Redis..."
if command -v redis-server &> /dev/null; then
    # Kill any existing Redis
    redis-cli shutdown 2>/dev/null || true
    sleep 1
    redis-server --daemonize yes --appendonly yes --dir /workspace/redis-data --maxmemory 4gb --maxmemory-policy allkeys-lru
    echo "  [+] Redis running on :6379"
else
    echo "  [*] Installing Redis..."
    apt-get update -qq && apt-get install -y -qq redis-server > /dev/null 2>&1
    redis-server --daemonize yes --appendonly yes --dir /workspace/redis-data --maxmemory 4gb --maxmemory-policy allkeys-lru
    echo "  [+] Redis installed and running"
fi

# ── 2. Clone/Update Repository ────────────────────────────────────
echo "[2/6] Setting up BountyHound Local..."
cd /workspace

if [ -d "bountyhound-local" ]; then
    echo "  [*] Existing installation found, updating..."
    cd bountyhound-local
    git pull --ff-only 2>/dev/null || echo "  [*] Git pull skipped (local changes)"
else
    echo "  [*] Fresh install, cloning repository..."
    git clone https://github.com/AshtonVaughan/bountyhound-local.git
    cd bountyhound-local
fi

export PYTHONPATH=/workspace/bountyhound-local

# ── 3. Install Dependencies ───────────────────────────────────────
echo "[3/6] Installing dependencies..."

# Python deps (skip if already installed)
if ! python -c "import celery" 2>/dev/null; then
    pip install --no-cache-dir -r requirements.txt -q
    pip install --no-cache-dir bountyhound huggingface_hub[cli] -q
    echo "  [+] Python dependencies installed"
else
    echo "  [+] Python dependencies already present"
fi

# Playwright browser
if ! playwright install --dry-run chromium 2>/dev/null | grep -q "already"; then
    playwright install chromium --with-deps 2>/dev/null || python -m playwright install chromium
    echo "  [+] Playwright chromium installed"
else
    echo "  [+] Playwright chromium already present"
fi

# Go recon tools (install if missing)
for tool in subfinder httpx nuclei; do
    if ! command -v $tool &> /dev/null; then
        echo "  [*] Installing $tool..."
        case $tool in
            subfinder) go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest 2>/dev/null ;;
            httpx)     go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest 2>/dev/null ;;
            nuclei)    go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest 2>/dev/null ;;
        esac
    fi
done
echo "  [+] Recon tools ready"

# ── 4. Download Models ─────────────────────────────────────────────
echo "[4/6] Checking model weights..."

MODELS=(
    "Qwen/Qwen2.5-72B-Instruct"
    "Qwen/Qwen2.5-14B-Instruct"
    "deepseek-ai/deepseek-coder-7b-instruct-v1.5"
    "mistralai/Mistral-7B-Instruct-v0.3"
    "microsoft/Phi-3-mini-4k-instruct"
)

MODELS_READY=true
for model in "${MODELS[@]}"; do
    model_dir="$HF_HOME/hub/models--$(echo $model | tr '/' '--')"
    if [ ! -d "$model_dir" ]; then
        MODELS_READY=false
        break
    fi
done

if [ "$MODELS_READY" = true ]; then
    echo "  [+] All models cached in $HF_HOME (skipping download)"
else
    echo "  [*] Downloading models to $HF_HOME (~80GB)..."
    echo "  [*] This takes 15-45 minutes depending on bandwidth."
    for model in "${MODELS[@]}"; do
        model_dir="$HF_HOME/hub/models--$(echo $model | tr '/' '--')"
        if [ -d "$model_dir" ]; then
            echo "  [+] $model (cached)"
        else
            echo "  [*] Downloading $model..."
            huggingface-cli download "$model" --quiet
            echo "  [+] $model (done)"
        fi
    done
fi

# ── 5. Initialize Database ────────────────────────────────────────
echo "[5/6] Initializing database..."
cd /workspace/bountyhound-local
python -c "from src.database.models import init_db; init_db()"
echo "  [+] SQLite database ready at $BHL_DB_PATH"

# ── 6. Start All Services ─────────────────────────────────────────
echo "[6/6] Starting BountyHound services..."
mkdir -p logs pids

# Detect GPU and VRAM
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "Unknown GPU")
GPU_MEM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
GPU_MEM_GB=$((GPU_MEM_MB / 1024))
echo "  [*] GPU: $GPU_NAME (${GPU_MEM_GB}GB VRAM)"

# Select model config based on available VRAM
if [ "$GPU_MEM_GB" -ge 90 ]; then
    echo "  [*] Using full-precision config (${GPU_MEM_GB}GB >= 90GB)"
    CONFIG="config/models.yaml"
elif [ "$GPU_MEM_GB" -ge 75 ]; then
    echo "  [*] Using AWQ quantized config (${GPU_MEM_GB}GB >= 75GB)"
    CONFIG="config/models-h100-awq.yaml"
else
    echo "  [!] WARNING: Only ${GPU_MEM_GB}GB VRAM detected. May not fit all models."
    CONFIG="config/models-h100-awq.yaml"
fi
export BHL_CONFIG_PATH="/workspace/bountyhound-local/$CONFIG"

# Start vLLM model servers
echo "  [*] Starting vLLM model servers..."
bash scripts/start-vllm.sh

# Start Celery workers
echo "  [*] Starting Celery workers..."
bash scripts/start-workers.sh

# Start FastAPI dashboard
echo "  [*] Starting dashboard..."
cd /workspace/bountyhound-local
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 \
    > logs/fastapi.log 2>&1 &
echo $! > pids/fastapi.pid

# Start Flower (without Docker)
echo "  [*] Starting Flower..."
celery -A src.workers.celery_app flower --port=5555 \
    > logs/flower.log 2>&1 &
echo $! > pids/flower.pid

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  BOUNTY HOUND LOCAL - RUNNING ON VAST.AI                    ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  GPU: $GPU_NAME (${GPU_MEM_GB}GB)"
echo "║  Config: $CONFIG"
echo "║                                                              ║"
echo "║  Dashboard:  :8000 (check Vast.ai console for external port) ║"
echo "║  Flower:     :5555 (check Vast.ai console for external port) ║"
echo "║  Redis:      :6379 (internal)                                ║"
echo "║  vLLM:       :8100-8104 (internal)                           ║"
echo "║                                                              ║"
echo "║  Models:     $HF_HOME"
echo "║  Database:   $BHL_DB_PATH"
echo "║  Findings:   /workspace/bounty-findings/"
echo "║                                                              ║"
echo "║  Usage:                                                      ║"
echo "║    cd /workspace/bountyhound-local                           ║"
echo "║    python cli.py add example.com --priority 8                ║"
echo "║    python cli.py hunt example.com                            ║"
echo "║    python cli.py swarm                                       ║"
echo "╚══════════════════════════════════════════════════════════════╝"

# Keep container alive (Vast.ai needs a foreground process)
echo "[*] BountyHound Local is running. Press Ctrl+C to stop."
tail -f logs/fastapi.log
