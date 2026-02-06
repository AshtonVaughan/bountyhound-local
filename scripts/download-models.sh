#!/bin/bash
# Download all model weights for BountyHound Local
set -e

echo "========================================"
echo "  BountyHound Local - Model Downloader"
echo "  GPU: H100 NVL (94GB)"
echo "========================================"

# Check for huggingface-cli
if ! command -v huggingface-cli &> /dev/null; then
    echo "[*] Installing huggingface_hub..."
    pip install huggingface_hub[cli]
fi

MODELS=(
    "Qwen/Qwen2.5-72B-Instruct"
    "Qwen/Qwen2.5-14B-Instruct"
    "deepseek-ai/deepseek-coder-7b-instruct-v1.5"
    "mistralai/Mistral-7B-Instruct-v0.3"
    "microsoft/Phi-3-mini-4k-instruct"
)

CACHE_DIR="${HF_HOME:-$HOME/.cache/huggingface}"

echo ""
echo "[*] Downloading ${#MODELS[@]} models to $CACHE_DIR"
echo "[*] Total download: ~80GB (this will take a while)"
echo ""

for model in "${MODELS[@]}"; do
    echo "────────────────────────────────────"
    echo "[*] Downloading: $model"
    echo "────────────────────────────────────"
    huggingface-cli download "$model" --quiet
    echo "[+] Done: $model"
    echo ""
done

echo "========================================"
echo "  All models downloaded successfully!"
echo "========================================"
echo ""
echo "VRAM allocation plan:"
echo "  Qwen2.5-72B-Instruct     ~40GB (orchestrator)"
echo "  Qwen2.5-14B-Instruct     ~12GB (discovery + auth)"
echo "  DeepSeek-Coder-7B         ~8GB (exploit + reporter)"
echo "  Mistral-7B-Instruct       ~8GB (validator)"
echo "  Phi-3-mini                 ~4GB (fast utility)"
echo "  ────────────────────────────────"
echo "  Total:                   ~80GB / 94GB available"
echo ""
