#!/bin/bash
# Download all model weights for BountyHound Local
# Supports both bare metal and Vast.ai environments
set -e

echo "========================================"
echo "  BountyHound Local - Model Downloader"
echo "========================================"

# Detect GPU
GPU_MEM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
GPU_MEM_GB=$((GPU_MEM_MB / 1024))
echo "  GPU VRAM: ${GPU_MEM_GB}GB"

# Check for huggingface-cli
if ! command -v huggingface-cli &> /dev/null; then
    echo "[*] Installing huggingface_hub..."
    pip install huggingface_hub[cli] -q
fi

# Set cache directory
CACHE_DIR="${HF_HOME:-$HOME/.cache/huggingface}"
mkdir -p "$CACHE_DIR"

# Select model set based on config
CONFIG="${BHL_CONFIG_PATH:-./config/models.yaml}"
if [ -f "$CONFIG" ]; then
    echo "  Config: $CONFIG"
    # Extract unique model names from config
    MODELS=($(python3 -c "
import yaml
with open('$CONFIG') as f:
    cfg = yaml.safe_load(f)
seen = set()
for m in cfg['models'].values():
    name = m['name']
    if name not in seen:
        seen.add(name)
        print(name)
" 2>/dev/null))
else
    # Fallback to default models
    MODELS=(
        "Qwen/Qwen2.5-72B-Instruct"
        "Qwen/Qwen2.5-14B-Instruct"
        "deepseek-ai/deepseek-coder-7b-instruct-v1.5"
        "mistralai/Mistral-7B-Instruct-v0.3"
        "microsoft/Phi-3-mini-4k-instruct"
    )
fi

echo ""
echo "[*] Downloading ${#MODELS[@]} models to $CACHE_DIR"
echo "[*] This may take 15-45 minutes depending on bandwidth"
echo ""

DOWNLOADED=0
SKIPPED=0

for model in "${MODELS[@]}"; do
    # Check if already downloaded
    model_dir="$CACHE_DIR/hub/models--$(echo $model | tr '/' '--')"
    if [ -d "$model_dir" ] && [ "$(ls -A $model_dir/snapshots/ 2>/dev/null)" ]; then
        echo "  [CACHED] $model"
        ((SKIPPED++))
    else
        echo "────────────────────────────────────"
        echo "  [DOWNLOADING] $model"
        echo "────────────────────────────────────"
        huggingface-cli download "$model" --quiet
        echo "  [+] Done: $model"
        ((DOWNLOADED++))
    fi
done

echo ""
echo "========================================"
echo "  Download complete!"
echo "  Downloaded: $DOWNLOADED models"
echo "  Cached:     $SKIPPED models"
echo "  Location:   $CACHE_DIR"
echo "========================================"

# Show VRAM plan
echo ""
echo "VRAM allocation:"
python3 -c "
import yaml
try:
    with open('$CONFIG') as f:
        cfg = yaml.safe_load(f)
    seen = set()
    total = 0
    for key, m in cfg['models'].items():
        port = m['port']
        if port in seen:
            continue
        seen.add(port)
        util = m['gpu_memory_utilization']
        vram = int(util * $GPU_MEM_GB)
        total += vram
        quant = m.get('quantization', 'fp16')
        if quant is None:
            quant = 'fp16'
        print(f'  {m[\"name\"]:50s}  ~{vram}GB ({quant})')
    print(f'  {\"\":50s}  ────────')
    print(f'  {\"Total\":50s}  ~{total}GB / ${GPU_MEM_GB}GB')
except Exception as e:
    print(f'  (Could not parse config: {e})')
" 2>/dev/null
echo ""
