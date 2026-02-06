#!/bin/bash
# Start Celery workers and beat scheduler
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
PID_DIR="$PROJECT_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"
cd "$PROJECT_DIR"

# Orchestrator worker (1 concurrent)
celery -A src.workers.celery_app worker \
    -Q orchestrate -n orchestrator@%h -c 1 \
    --loglevel=info \
    > "$LOG_DIR/celery-orchestrator.log" 2>&1 &
echo $! > "$PID_DIR/celery-orchestrator.pid"

# Recon workers (3 concurrent)
celery -A src.workers.celery_app worker \
    -Q recon -n recon@%h -c 3 \
    --loglevel=info \
    > "$LOG_DIR/celery-recon.log" 2>&1 &
echo $! > "$PID_DIR/celery-recon.pid"

# Discovery workers (2 concurrent)
celery -A src.workers.celery_app worker \
    -Q discovery -n discovery@%h -c 2 \
    --loglevel=info \
    > "$LOG_DIR/celery-discovery.log" 2>&1 &
echo $! > "$PID_DIR/celery-discovery.pid"

# Exploit workers (2 concurrent)
celery -A src.workers.celery_app worker \
    -Q exploit -n exploit@%h -c 2 \
    --loglevel=info \
    > "$LOG_DIR/celery-exploit.log" 2>&1 &
echo $! > "$PID_DIR/celery-exploit.pid"

# Validation workers (4 concurrent)
celery -A src.workers.celery_app worker \
    -Q validate -n validate@%h -c 4 \
    --loglevel=info \
    > "$LOG_DIR/celery-validate.log" 2>&1 &
echo $! > "$PID_DIR/celery-validate.pid"

# Report + Auth workers (2 concurrent)
celery -A src.workers.celery_app worker \
    -Q report,auth -n support@%h -c 2 \
    --loglevel=info \
    > "$LOG_DIR/celery-support.log" 2>&1 &
echo $! > "$PID_DIR/celery-support.pid"

echo "  [+] 6 Celery worker groups started (14 total workers)"

# Start Celery Beat (scheduler)
celery -A src.workers.celery_app beat \
    --loglevel=info \
    > "$LOG_DIR/celery-beat.log" 2>&1 &
echo $! > "$PID_DIR/celery-beat.pid"
echo "  [+] Celery Beat scheduler running"
