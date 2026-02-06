#!/bin/bash
# Start vLLM model servers from config
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
PID_DIR="$PROJECT_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

CONFIG="${BHL_CONFIG_PATH:-$PROJECT_DIR/config/models.yaml}"

echo "  Loading model config from: $CONFIG"

# Parse models from YAML using Python (portable)
python3 -c "
import yaml, sys
with open('$CONFIG') as f:
    cfg = yaml.safe_load(f)

seen_ports = set()
for key, model in cfg['models'].items():
    port = model['port']
    if port in seen_ports:
        continue  # skip shared-port models
    seen_ports.add(port)
    name = model['name']
    gpu_util = model['gpu_memory_utilization']
    max_len = model['max_model_len']
    tp = model.get('tensor_parallel_size', 1)
    quant = model.get('quantization', None)
    dtype = model.get('dtype', 'auto')
    print(f'{key}|{name}|{port}|{gpu_util}|{max_len}|{tp}|{quant}|{dtype}')
" | while IFS='|' read -r key name port gpu_util max_len tp quant dtype; do

    echo "  [*] Starting $name ($key) on :$port..."

    VLLM_ARGS=(
        --model "$name"
        --port "$port"
        --gpu-memory-utilization "$gpu_util"
        --max-model-len "$max_len"
        --tensor-parallel-size "$tp"
        --trust-remote-code
        --disable-log-requests
    )

    # Add quantization if specified
    if [ "$quant" != "None" ] && [ "$quant" != "" ]; then
        VLLM_ARGS+=(--quantization "$quant")
    fi

    # Add dtype if specified
    if [ "$dtype" != "auto" ] && [ "$dtype" != "None" ] && [ "$dtype" != "" ]; then
        VLLM_ARGS+=(--dtype "$dtype")
    fi

    python -m vllm.entrypoints.openai.api_server "${VLLM_ARGS[@]}" \
        > "$LOG_DIR/vllm-$key.log" 2>&1 &
    echo $! > "$PID_DIR/vllm-$key.pid"

done

echo "  [+] vLLM servers starting (models take 3-8 minutes to load)"
echo "  [*] Check logs: tail -f $LOG_DIR/vllm-*.log"
