#!/bin/bash
# Start all BountyHound Local services
# Works on both bare metal and Vast.ai instances
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
PID_DIR="$PROJECT_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

# Detect environment
IS_VAST_AI=false
if [ "${BHL_VAST_AI:-0}" = "1" ] || [ -f "/etc/vast-ai" ] || [ -d "/workspace" ]; then
    IS_VAST_AI=true
fi

# Detect GPU(s)
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l || echo "0")
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "Unknown")
GPU_MEM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
GPU_MEM_GB=$((GPU_MEM_MB / 1024))
TOTAL_VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | awk '{sum+=$1} END {print sum}' || echo "0")
TOTAL_VRAM_GB=$((TOTAL_VRAM_MB / 1024))

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║               BOUNTY HOUND LOCAL v1.0.0                     ║"
echo "║          Autonomous Bug Bounty Hunting Swarm                 ║"
echo "║            ${GPU_COUNT}x $GPU_NAME (${TOTAL_VRAM_GB}GB total)"
if [ "$IS_VAST_AI" = true ]; then
echo "║                   [Vast.ai Instance]                         ║"
fi
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Start Redis ─────────────────────────────────────────────────
echo "[1/6] Starting Redis..."
if [ "$IS_VAST_AI" = true ]; then
    REDIS_DIR="${REDIS_DATA_DIR:-/workspace/redis-data}"
    mkdir -p "$REDIS_DIR"
    # Kill existing Redis if running
    redis-cli shutdown 2>/dev/null || true
    sleep 1
    redis-server --daemonize yes --appendonly yes --dir "$REDIS_DIR" \
        --maxmemory 4gb --maxmemory-policy allkeys-lru
    echo "  [+] Redis daemon running on :6379"
else
    cd "$PROJECT_DIR"
    docker compose up -d redis
    echo "  [+] Redis running on :6379 (Docker)"
fi

# ── 2. Initialize database ────────────────────────────────────────
echo "[2/6] Initializing database..."
cd "$PROJECT_DIR"
python -c "from src.database.models import init_db; init_db()"
echo "  [+] SQLite database ready"

# ── 3. Start vLLM model servers ───────────────────────────────────
echo "[3/6] Starting vLLM model servers..."

# Auto-select config based on GPU count and VRAM
if [ -n "$BHL_CONFIG_PATH" ] && [ -f "$BHL_CONFIG_PATH" ]; then
    CONFIG="$BHL_CONFIG_PATH"
    echo "  [*] Config (env override): $CONFIG"
elif [ "$GPU_COUNT" -ge 2 ]; then
    CONFIG="$PROJECT_DIR/config/models-dual-gpu.yaml"
    echo "  [*] Dual GPU detected ($GPU_COUNT GPUs) - using FP16 tensor parallel config"
elif [ "$GPU_MEM_GB" -ge 90 ]; then
    CONFIG="$PROJECT_DIR/config/models.yaml"
    echo "  [*] Single GPU (${GPU_MEM_GB}GB) - using AWQ config"
else
    CONFIG="$PROJECT_DIR/config/models-h100-awq.yaml"
    echo "  [*] Single GPU (${GPU_MEM_GB}GB) - using AWQ config (conservative)"
fi
export BHL_CONFIG_PATH="$CONFIG"

bash "$SCRIPT_DIR/start-vllm.sh"

echo "  [+] Waiting for models to load (3-8 minutes on H100)..."
sleep 30

# ── 4. Start Celery workers ───────────────────────────────────────
echo "[4/6] Starting Celery workers..."
bash "$SCRIPT_DIR/start-workers.sh"

# ── 5. Start FastAPI dashboard ─────────────────────────────────────
echo "[5/6] Starting FastAPI dashboard..."
cd "$PROJECT_DIR"
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 \
    > "$LOG_DIR/fastapi.log" 2>&1 &
echo $! > "$PID_DIR/fastapi.pid"
echo "  [+] Dashboard at http://localhost:8000"

# ── 6. Start Flower monitoring ─────────────────────────────────────
echo "[6/6] Starting Flower monitoring..."
if [ "$IS_VAST_AI" = true ]; then
    # Vast.ai: run Flower as Python process (no Docker-in-Docker)
    cd "$PROJECT_DIR"
    celery -A src.workers.celery_app flower --port=5555 \
        > "$LOG_DIR/flower.log" 2>&1 &
    echo $! > "$PID_DIR/flower.pid"
    echo "  [+] Flower at http://localhost:5555"
else
    cd "$PROJECT_DIR"
    docker compose up -d flower
    echo "  [+] Flower at http://localhost:5555 (Docker)"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ALL SERVICES STARTED                                       ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Dashboard:  http://localhost:8000                           ║"
echo "║  Flower:     http://localhost:5555                           ║"
echo "║  Redis:      localhost:6379                                  ║"
echo "║  vLLM:       localhost:8100-8104                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Logs:       $LOG_DIR/"
echo "║  PIDs:       $PID_DIR/"
echo "╠══════════════════════════════════════════════════════════════╣"
if [ "$IS_VAST_AI" = true ]; then
echo "║  NOTE: Check Vast.ai console for external port mappings      ║"
echo "║  Internal :8000 and :5555 map to random external ports       ║"
echo "╠══════════════════════════════════════════════════════════════╣"
fi
echo "║  CLI:  python cli.py add example.com                        ║"
echo "║        python cli.py hunt example.com                       ║"
echo "║        python cli.py swarm                                  ║"
echo "║        python cli.py status                                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
