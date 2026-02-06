#!/bin/bash
# Check health of all BountyHound Local services
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$PROJECT_DIR/pids"

echo "╔════════════════════════════════════════╗"
echo "║  BountyHound Local - Health Check      ║"
echo "╚════════════════════════════════════════╝"
echo ""

# Check Redis
echo "[Redis]"
if redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "  Status: HEALTHY"
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

# Print stats
echo ""
echo "[Stats]"
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
