#!/bin/bash
# Gracefully stop all BountyHound Local services
# Works on both bare metal and Vast.ai instances
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$PROJECT_DIR/pids"

# Detect environment
IS_VAST_AI=false
if [ "${BHL_VAST_AI:-0}" = "1" ] || [ -f "/etc/vast-ai" ] || [ -d "/workspace" ]; then
    IS_VAST_AI=true
fi

echo "Stopping BountyHound Local..."

# Stop FastAPI
if [ -f "$PID_DIR/fastapi.pid" ]; then
    kill "$(cat "$PID_DIR/fastapi.pid")" 2>/dev/null && echo "  [x] FastAPI stopped" || echo "  [-] FastAPI already stopped"
    rm -f "$PID_DIR/fastapi.pid"
fi

# Stop Flower
if [ -f "$PID_DIR/flower.pid" ]; then
    kill "$(cat "$PID_DIR/flower.pid")" 2>/dev/null && echo "  [x] Flower stopped" || echo "  [-] Flower already stopped"
    rm -f "$PID_DIR/flower.pid"
fi

# Stop Celery Beat
if [ -f "$PID_DIR/celery-beat.pid" ]; then
    kill "$(cat "$PID_DIR/celery-beat.pid")" 2>/dev/null && echo "  [x] Celery Beat stopped" || echo "  [-] Beat already stopped"
    rm -f "$PID_DIR/celery-beat.pid"
fi

# Stop all Celery workers
for pidfile in "$PID_DIR"/celery-*.pid; do
    if [ -f "$pidfile" ]; then
        name=$(basename "$pidfile" .pid)
        kill "$(cat "$pidfile")" 2>/dev/null && echo "  [x] $name stopped" || echo "  [-] $name already stopped"
        rm -f "$pidfile"
    fi
done

# Stop vLLM servers
for pidfile in "$PID_DIR"/vllm-*.pid; do
    if [ -f "$pidfile" ]; then
        name=$(basename "$pidfile" .pid)
        kill "$(cat "$pidfile")" 2>/dev/null && echo "  [x] $name stopped" || echo "  [-] $name already stopped"
        rm -f "$pidfile"
    fi
done

# Stop Redis / Docker services
if [ "$IS_VAST_AI" = true ]; then
    redis-cli shutdown 2>/dev/null && echo "  [x] Redis stopped" || echo "  [-] Redis already stopped"
else
    cd "$PROJECT_DIR"
    docker compose down 2>/dev/null && echo "  [x] Docker services stopped" || echo "  [-] Docker already stopped"
fi

echo ""
echo "All services stopped. Data preserved in SQLite."
echo "Run ./scripts/start.sh to restart."
