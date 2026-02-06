#!/bin/bash
# Start all BountyHound Local services
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
PID_DIR="$PROJECT_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║               BOUNTY HOUND LOCAL v1.0.0                     ║"
echo "║          Autonomous Bug Bounty Hunting Swarm                 ║"
echo "║                   H100 NVL (94GB)                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# 1. Start Redis via Docker
echo "[1/6] Starting Redis..."
cd "$PROJECT_DIR"
docker compose up -d redis
echo "  [+] Redis running on :6379"

# 2. Initialize database
echo "[2/6] Initializing database..."
cd "$PROJECT_DIR"
python -c "from src.database.models import init_db; init_db()"
echo "  [+] SQLite database ready"

# 3. Start vLLM model servers
echo "[3/6] Starting vLLM model servers..."

# Orchestrator - port 8100
echo "  [*] Starting Qwen2.5-72B-Instruct (orchestrator) on :8100..."
python -m vllm.entrypoints.openai.api_server \
    --model "Qwen/Qwen2.5-72B-Instruct" \
    --port 8100 \
    --gpu-memory-utilization 0.42 \
    --max-model-len 32768 \
    --trust-remote-code \
    > "$LOG_DIR/vllm-orchestrator.log" 2>&1 &
echo $! > "$PID_DIR/vllm-orchestrator.pid"

# Discovery/Auth - port 8101
echo "  [*] Starting Qwen2.5-14B-Instruct (discovery) on :8101..."
python -m vllm.entrypoints.openai.api_server \
    --model "Qwen/Qwen2.5-14B-Instruct" \
    --port 8101 \
    --gpu-memory-utilization 0.14 \
    --max-model-len 16384 \
    --trust-remote-code \
    > "$LOG_DIR/vllm-discovery.log" 2>&1 &
echo $! > "$PID_DIR/vllm-discovery.pid"

# Exploit/Reporter - port 8102
echo "  [*] Starting DeepSeek-Coder-7B (exploit) on :8102..."
python -m vllm.entrypoints.openai.api_server \
    --model "deepseek-ai/deepseek-coder-7b-instruct-v1.5" \
    --port 8102 \
    --gpu-memory-utilization 0.10 \
    --max-model-len 16384 \
    --trust-remote-code \
    > "$LOG_DIR/vllm-exploit.log" 2>&1 &
echo $! > "$PID_DIR/vllm-exploit.pid"

# Validator - port 8103
echo "  [*] Starting Mistral-7B-Instruct (validator) on :8103..."
python -m vllm.entrypoints.openai.api_server \
    --model "mistralai/Mistral-7B-Instruct-v0.3" \
    --port 8103 \
    --gpu-memory-utilization 0.10 \
    --max-model-len 8192 \
    --trust-remote-code \
    > "$LOG_DIR/vllm-validator.log" 2>&1 &
echo $! > "$PID_DIR/vllm-validator.pid"

# Fast utility - port 8104
echo "  [*] Starting Phi-3-mini (utility) on :8104..."
python -m vllm.entrypoints.openai.api_server \
    --model "microsoft/Phi-3-mini-4k-instruct" \
    --port 8104 \
    --gpu-memory-utilization 0.06 \
    --max-model-len 4096 \
    --trust-remote-code \
    > "$LOG_DIR/vllm-utility.log" 2>&1 &
echo $! > "$PID_DIR/vllm-utility.pid"

echo "  [+] Waiting for models to load (this takes 2-5 minutes)..."
sleep 30

# 4. Start Celery workers
echo "[4/6] Starting Celery workers..."
cd "$PROJECT_DIR"

# Orchestrator worker
celery -A src.workers.celery_app worker \
    -Q orchestrate -n orchestrator@%h -c 1 \
    --loglevel=info \
    > "$LOG_DIR/celery-orchestrator.log" 2>&1 &
echo $! > "$PID_DIR/celery-orchestrator.pid"

# Recon workers
celery -A src.workers.celery_app worker \
    -Q recon -n recon@%h -c 3 \
    --loglevel=info \
    > "$LOG_DIR/celery-recon.log" 2>&1 &
echo $! > "$PID_DIR/celery-recon.pid"

# Discovery workers
celery -A src.workers.celery_app worker \
    -Q discovery -n discovery@%h -c 2 \
    --loglevel=info \
    > "$LOG_DIR/celery-discovery.log" 2>&1 &
echo $! > "$PID_DIR/celery-discovery.pid"

# Exploit workers
celery -A src.workers.celery_app worker \
    -Q exploit -n exploit@%h -c 2 \
    --loglevel=info \
    > "$LOG_DIR/celery-exploit.log" 2>&1 &
echo $! > "$PID_DIR/celery-exploit.pid"

# Validation workers
celery -A src.workers.celery_app worker \
    -Q validate -n validate@%h -c 4 \
    --loglevel=info \
    > "$LOG_DIR/celery-validate.log" 2>&1 &
echo $! > "$PID_DIR/celery-validate.pid"

# Report + Auth workers
celery -A src.workers.celery_app worker \
    -Q report,auth -n support@%h -c 2 \
    --loglevel=info \
    > "$LOG_DIR/celery-support.log" 2>&1 &
echo $! > "$PID_DIR/celery-support.pid"

echo "  [+] 6 Celery worker groups started"

# 5. Start Celery Beat (scheduler)
echo "[5/6] Starting Celery Beat scheduler..."
celery -A src.workers.celery_app beat \
    --loglevel=info \
    > "$LOG_DIR/celery-beat.log" 2>&1 &
echo $! > "$PID_DIR/celery-beat.pid"
echo "  [+] Beat scheduler running"

# 6. Start FastAPI dashboard
echo "[6/6] Starting FastAPI dashboard..."
cd "$PROJECT_DIR"
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 \
    > "$LOG_DIR/fastapi.log" 2>&1 &
echo $! > "$PID_DIR/fastapi.pid"
echo "  [+] Dashboard at http://localhost:8000"

# Start Flower monitoring
echo "[+] Starting Flower monitoring..."
docker compose up -d flower
echo "  [+] Flower at http://localhost:5555"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ALL SERVICES STARTED                                       ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Dashboard:  http://localhost:8000                           ║"
echo "║  Flower:     http://localhost:5555                           ║"
echo "║  Redis:      localhost:6379                                  ║"
echo "║  vLLM:       localhost:8100-8104                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Logs:       $LOG_DIR/                      ║"
echo "║  PIDs:       $PID_DIR/                      ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  CLI:  python cli.py add example.com                        ║"
echo "║        python cli.py hunt example.com                       ║"
echo "║        python cli.py swarm                                  ║"
echo "║        python cli.py status                                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
