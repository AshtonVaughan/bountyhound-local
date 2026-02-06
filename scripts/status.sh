#!/bin/bash
# Check health of all BountyHound Local services
# Works on both bare metal and Vast.ai instances

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$PROJECT_DIR/pids"

# Detect environment
IS_VAST_AI=false
if [ "${BHL_VAST_AI:-0}" = "1" ] || [ -f "/etc/vast-ai" ] || [ -d "/workspace" ]; then
    IS_VAST_AI=true
fi

echo "╔════════════════════════════════════════╗"
echo "║  BountyHound Local - Health Check      ║"
echo "╚════════════════════════════════════════╝"
echo ""

# GPU Info
echo "[GPU]"
if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
    GPU_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -1)
    GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null | head -1)
    echo "  GPU: $GPU_NAME"
    echo "  VRAM: $GPU_USED / $GPU_MEM"
    echo "  Temp: ${GPU_TEMP}C"
else
    echo "  Status: NO GPU DETECTED"
fi

if [ "$IS_VAST_AI" = true ]; then
    echo "  Env: Vast.ai Instance"
fi

# Check Redis
echo ""
echo "[Redis]"
if redis-cli ping 2>/dev/null | grep -q PONG; then
    REDIS_MEM=$(redis-cli info memory 2>/dev/null | grep used_memory_human | cut -d: -f2 | tr -d '\r')
    echo "  Status: HEALTHY (${REDIS_MEM:-unknown} used)"
else
    echo "  Status: DOWN"
fi

# Check vLLM servers
echo ""
echo "[vLLM Model Servers]"
for port in 8100 8101 8102 8103 8104; do
    name="unknown"
    case $port in
        8100) name="orchestrator (72B)" ;;
        8101) name="discovery (14B)" ;;
        8102) name="exploit (7B)" ;;
        8103) name="validator (7B)" ;;
        8104) name="utility (3B)" ;;
    esac

    if curl -s "http://localhost:$port/v1/models" > /dev/null 2>&1; then
        echo "  :$port $name - HEALTHY"
    else
        echo "  :$port $name - DOWN"
    fi
done

# Check Celery workers
echo ""
echo "[Celery Workers]"
for pidfile in "$PID_DIR"/celery-*.pid; do
    if [ -f "$pidfile" ]; then
        name=$(basename "$pidfile" .pid)
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  $name (PID $pid) - RUNNING"
        else
            echo "  $name (PID $pid) - DEAD"
        fi
    fi
done

# Check FastAPI
echo ""
echo "[FastAPI Dashboard]"
if curl -s "http://localhost:8000/api/health" > /dev/null 2>&1; then
    echo "  Status: HEALTHY (http://localhost:8000)"
else
    echo "  Status: DOWN"
fi

# Check Flower
echo ""
echo "[Flower Monitoring]"
if curl -s "http://localhost:5555" > /dev/null 2>&1; then
    echo "  Status: HEALTHY (http://localhost:5555)"
else
    echo "  Status: DOWN"
fi

# Storage info (Vast.ai specific)
if [ "$IS_VAST_AI" = true ]; then
    echo ""
    echo "[Storage]"
    WORKSPACE_USED=$(du -sh /workspace 2>/dev/null | cut -f1)
    MODELS_USED=$(du -sh /workspace/models 2>/dev/null | cut -f1)
    DB_SIZE=$(du -sh /workspace/data 2>/dev/null | cut -f1)
    DISK_FREE=$(df -h /workspace 2>/dev/null | tail -1 | awk '{print $4}')
    echo "  Workspace:  $WORKSPACE_USED"
    echo "  Models:     $MODELS_USED"
    echo "  Database:   $DB_SIZE"
    echo "  Disk Free:  $DISK_FREE"
fi

# Print stats
echo ""
echo "[Stats]"
cd "$PROJECT_DIR"
python -c "
from src.database.redis_manager import TaskQueue
from src.database.models import TargetDB, HuntDB, init_db
init_db()
stats = TaskQueue.get_stats()
targets = TargetDB.list_all()
active = HuntDB.get_active()
print(f'  Targets: {len(targets)}')
print(f'  Active hunts: {len(active)}')
print(f'  Hunts completed: {stats.get(\"hunts_completed\", 0)}')
print(f'  Total findings: {stats.get(\"findings_total\", 0)}')
" 2>/dev/null || echo "  (stats unavailable - db or redis may be down)"

echo ""
