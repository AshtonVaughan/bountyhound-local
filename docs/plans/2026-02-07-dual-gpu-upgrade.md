# Dual-GPU Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade BountyHound Local from single H100 (1x 72B AWQ at 3.2 tok/s) to dual-GPU (2x H100) for FP16 inference with tensor parallelism, doubling throughput and enabling multi-model deployment.

**Architecture:** Replace the single 72B AWQ instance with a 72B FP16 model sharded across 2 GPUs via `tensor_parallel_size=2`, plus a dedicated 14B model on remaining VRAM for fast-path tasks. vLLM natively supports tensor parallelism via Ray — no application code changes needed for the sharding itself, only config and startup script updates.

**Tech Stack:** vLLM (tensor parallelism via Ray), Python, YAML config, Bash scripts, Celery, Docker

---

## Background & Rationale

### Current State (1x H100 NVL, 94GB)
- Single Qwen2.5-72B-Instruct-AWQ on port 8100
- All 7 roles share one model instance
- Throughput: ~3.2 tokens/sec (AWQ 4-bit bottleneck)
- VRAM: 88.8 GB / 95.8 GB (model 39GB + KV cache 50GB)
- Discovery phase takes several minutes per LLM call

### Target State (2x H100 NVL, ~190GB total)
- Qwen2.5-72B-Instruct FP16 sharded across 2 GPUs (tensor_parallel_size=2)
- ~72GB for model weights (split 36GB per GPU)
- ~100GB remaining for KV cache → much larger batch sizes
- Throughput: ~15-25 tok/s (4-8x improvement from FP16 + TP2)
- Optional: dedicated 14B model for fast-path tasks (parsing, classification)

### Key Constraint
- vLLM `tensor_parallel_size=2` requires BOTH GPUs for a single model instance
- A second model (14B) can only run if there's enough leftover VRAM on one GPU
- VRAM accounting: 72B FP16 ≈ 144GB (72GB/GPU) → leaves ~22GB/GPU for KV cache
- Alternative: 72B AWQ TP2 ≈ 40GB total (20GB/GPU) → leaves ~74GB/GPU → room for 14B FP16 (28GB) on one GPU

---

## Task 1: Create Dual-GPU Model Config

**Files:**
- Create: `config/models-dual-gpu.yaml`
- Reference: `config/models.yaml` (current single-GPU config)

**Step 1: Write the dual-GPU config file**

```yaml
# config/models-dual-gpu.yaml
# Dual H100 NVL configuration (2x ~95GB = ~190GB total VRAM)
# Strategy: 72B FP16 with tensor_parallel_size=2 for maximum throughput
# Optional: 14B model on remaining VRAM for fast-path tasks

inference:
  engine: vllm
  host: "0.0.0.0"
  base_port: 8100

models:
  orchestrator:
    name: "Qwen/Qwen2.5-72B-Instruct"
    role: "orchestrator"
    port: 8100
    gpu_memory_utilization: 0.85
    max_model_len: 32768
    tensor_parallel_size: 2
    dtype: float16

  discovery:
    name: "Qwen/Qwen2.5-72B-Instruct"
    role: "reasoning"
    port: 8100
    gpu_memory_utilization: 0.85
    max_model_len: 32768
    tensor_parallel_size: 2
    dtype: float16

  auth:
    name: "Qwen/Qwen2.5-72B-Instruct"
    role: "reasoning"
    port: 8100
    gpu_memory_utilization: 0.85
    max_model_len: 32768
    tensor_parallel_size: 2
    dtype: float16

  exploit:
    name: "Qwen/Qwen2.5-72B-Instruct"
    role: "code"
    port: 8100
    gpu_memory_utilization: 0.85
    max_model_len: 32768
    tensor_parallel_size: 2
    dtype: float16

  validator:
    name: "Qwen/Qwen2.5-72B-Instruct"
    role: "validation"
    port: 8100
    gpu_memory_utilization: 0.85
    max_model_len: 32768
    tensor_parallel_size: 2
    dtype: float16

  reporter:
    name: "Qwen/Qwen2.5-72B-Instruct"
    role: "reporting"
    port: 8100
    gpu_memory_utilization: 0.85
    max_model_len: 32768
    tensor_parallel_size: 2
    dtype: float16

  fast:
    name: "Qwen/Qwen2.5-72B-Instruct"
    role: "utility"
    port: 8100
    gpu_memory_utilization: 0.85
    max_model_len: 32768
    tensor_parallel_size: 2
    dtype: float16
```

**Step 2: Verify YAML parses correctly**

Run: `cd C:\Users\vaugh\Projects\bountyhound-local && python -c "import yaml; c=yaml.safe_load(open('config/models-dual-gpu.yaml')); print(f'Models: {len(c[\"models\"])}'); print(f'TP size: {c[\"models\"][\"orchestrator\"][\"tensor_parallel_size\"]}'); print(f'dtype: {c[\"models\"][\"orchestrator\"][\"dtype\"]}')" `

Expected:
```
Models: 7
TP size: 2
dtype: float16
```

**Step 3: Commit**

```bash
git add config/models-dual-gpu.yaml
git commit -m "feat: add dual-GPU model config with tensor_parallel_size=2"
```

---

## Task 2: Update start-vllm.sh to Support tensor_parallel_size

**Files:**
- Modify: `scripts/start-vllm.sh`

**Step 1: Read current start-vllm.sh**

Read the full file to understand the current YAML parsing and vLLM launch logic.

**Step 2: Add tensor_parallel_size extraction to the Python YAML parser**

The script currently parses YAML with an inline Python snippet. Modify it to also extract `tensor_parallel_size` and pass it to vLLM. Find the Python block that extracts model config fields and add:

```python
tp = model.get('tensor_parallel_size', 1)
```

And ensure the `--tensor-parallel-size` flag uses this value instead of hardcoding `1`.

Specifically, locate the line in `start-vllm.sh` that builds the vLLM command and ensure it includes:
```bash
--tensor-parallel-size "$tp"
```

Where `$tp` comes from the YAML config's `tensor_parallel_size` field (defaulting to 1).

**Step 3: Verify the script parses the new config**

Run: `cd C:\Users\vaugh\Projects\bountyhound-local && BHL_CONFIG_PATH=config/models-dual-gpu.yaml bash -x scripts/start-vllm.sh 2>&1 | head -30`

Expected: See `--tensor-parallel-size 2` in the vLLM launch command.

**Step 4: Commit**

```bash
git add scripts/start-vllm.sh
git commit -m "feat: support tensor_parallel_size from YAML config in vLLM launcher"
```

---

## Task 3: Update start.sh Auto-Detection for Dual GPU

**Files:**
- Modify: `scripts/start.sh`

**Step 1: Read current GPU detection logic**

The script currently checks VRAM to select between `models.yaml` (90GB+) and `models-h100-awq.yaml` (<90GB). We need to add GPU count detection.

**Step 2: Add GPU count detection**

Find the GPU detection section and replace/extend it with:

```bash
# Detect GPU count and total VRAM
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)
TOTAL_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | awk '{sum+=$1} END {print sum}')

echo "Detected $GPU_COUNT GPU(s) with ${TOTAL_VRAM}MB total VRAM"

if [ "$GPU_COUNT" -ge 2 ]; then
    CONFIG_PATH="config/models-dual-gpu.yaml"
    echo "Using dual-GPU config (tensor_parallel_size=2, FP16)"
elif [ "$TOTAL_VRAM" -gt 90000 ]; then
    CONFIG_PATH="config/models.yaml"
    echo "Using single-GPU full config"
else
    CONFIG_PATH="config/models-h100-awq.yaml"
    echo "Using single-GPU AWQ config"
fi

export BHL_CONFIG_PATH="$CONFIG_PATH"
```

**Step 3: Verify detection logic (dry run)**

Run on instance: `ssh -p 41144 root@180.21.170.127 "nvidia-smi --query-gpu=name --format=csv,noheader | wc -l"`

Expected (current single GPU): `1`

**Step 4: Commit**

```bash
git add scripts/start.sh
git commit -m "feat: auto-detect dual GPU and select appropriate model config"
```

---

## Task 4: Update vast-onstart.sh for Dual GPU Bootstrap

**Files:**
- Modify: `scripts/vast-onstart.sh`

**Step 1: Read current vast-onstart.sh**

Understand the model download list and config selection logic.

**Step 2: Update model download section**

The FP16 72B model is ~144GB vs 39GB AWQ. The download list needs to include the FP16 model when 2+ GPUs are detected. Find the model download section and add:

```bash
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)

if [ "$GPU_COUNT" -ge 2 ]; then
    # Dual GPU: download FP16 (no quantization needed)
    MODELS_TO_DOWNLOAD=(
        "Qwen/Qwen2.5-72B-Instruct"
    )
else
    # Single GPU: download AWQ quantized
    MODELS_TO_DOWNLOAD=(
        "Qwen/Qwen2.5-72B-Instruct-AWQ"
    )
fi
```

**Step 3: Update config selection to match Task 3 logic**

Mirror the same GPU count detection and config path selection from `start.sh`.

**Step 4: Commit**

```bash
git add scripts/vast-onstart.sh
git commit -m "feat: dual-GPU model download and config selection in Vast.ai bootstrap"
```

---

## Task 5: Update Dockerfile for Multi-GPU Support

**Files:**
- Modify: `Dockerfile`

**Step 1: Read current Dockerfile**

Check for GPU-related environment variables and exposed ports.

**Step 2: Add Ray dependency for tensor parallelism**

vLLM uses Ray for multi-GPU tensor parallelism. Add to requirements or Dockerfile:

```dockerfile
# After the pip install line, add:
RUN pip install ray[default]
```

**Step 3: Update NVIDIA environment variables**

Ensure the Dockerfile exposes all GPUs:

```dockerfile
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
```

**Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat: add Ray dependency and multi-GPU env vars to Dockerfile"
```

---

## Task 6: Update docker-compose.yml for Multi-GPU

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Read current docker-compose.yml**

Check the GPU reservation section.

**Step 2: Update GPU count from 1 to 2**

Find the deploy/resources section for the bountyhound service and change:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 2          # Changed from 1
          capabilities: [gpu]
```

**Step 3: Verify compose config**

Run: `cd C:\Users\vaugh\Projects\bountyhound-local && docker compose config 2>&1 | grep -A5 devices`

**Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: reserve 2 GPUs in docker-compose"
```

---

## Task 7: Update status.sh for Multi-GPU Reporting

**Files:**
- Modify: `scripts/status.sh`

**Step 1: Read current status.sh**

Check the GPU reporting section.

**Step 2: Update GPU status to show all GPUs**

Replace single-GPU nvidia-smi call with a loop:

```bash
echo "=== GPU Status ==="
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)
echo "GPUs detected: $GPU_COUNT"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,temperature.gpu --format=csv,noheader | while IFS=, read -r idx name used total temp; do
    echo "  GPU $idx: $name - ${used}/${total} MiB (${temp}°C)"
done
```

**Step 3: Commit**

```bash
git add scripts/status.sh
git commit -m "feat: report all GPUs in status script"
```

---

## Task 8: Update download-models.sh for FP16 Model

**Files:**
- Modify: `scripts/download-models.sh`

**Step 1: Read current download-models.sh**

Check the model list and download logic.

**Step 2: Add GPU-count-aware model selection**

Add dual-GPU path that downloads `Qwen/Qwen2.5-72B-Instruct` (FP16, ~144GB) instead of the AWQ variant (~39GB). Keep AWQ as fallback for single-GPU:

```bash
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l)

if [ "$GPU_COUNT" -ge 2 ]; then
    echo "Dual GPU detected - downloading FP16 model (~144GB)"
    huggingface-cli download Qwen/Qwen2.5-72B-Instruct --local-dir-use-symlinks False &
    echo $! > /tmp/dl-72b-fp16.pid
else
    echo "Single GPU - downloading AWQ model (~39GB)"
    huggingface-cli download Qwen/Qwen2.5-72B-Instruct-AWQ --local-dir-use-symlinks False &
    echo $! > /tmp/dl-72b-awq.pid
fi
```

**Step 3: Commit**

```bash
git add scripts/download-models.sh
git commit -m "feat: download FP16 model when dual GPU detected"
```

---

## Task 9: Update README.md with Dual-GPU Architecture

**Files:**
- Modify: `README.md`

**Step 1: Read current README**

**Step 2: Add dual-GPU section**

Add a new section or update the existing architecture section:

```markdown
## GPU Configurations

| Config | GPUs | Model | VRAM | Throughput |
|--------|------|-------|------|-----------|
| Single H100 (AWQ) | 1x H100 (80-95GB) | 72B AWQ 4-bit | ~89GB | ~3 tok/s |
| Dual H100 (FP16) | 2x H100 (160-190GB) | 72B FP16 TP2 | ~144GB + KV | ~15-25 tok/s |

### Auto-Detection
The system automatically detects GPU count at startup:
- **1 GPU, 90GB+**: Uses `config/models.yaml` (72B AWQ)
- **1 GPU, <90GB**: Uses `config/models-h100-awq.yaml` (72B AWQ, conservative)
- **2+ GPUs**: Uses `config/models-dual-gpu.yaml` (72B FP16, tensor_parallel_size=2)
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add dual-GPU configuration documentation"
```

---

## Task 10: Deploy and Test on Dual-GPU Vast.ai Instance

**Step 1: Provision a 2x H100 NVL instance on Vast.ai**

Search for instances with `num_gpus=2` and `gpu_name=H100_NVL`.

**Step 2: SSH in and verify GPU setup**

```bash
nvidia-smi
# Expected: 2x H100 NVL, ~95GB each
```

**Step 3: Clone repo and run bootstrap**

```bash
cd /workspace
git clone https://github.com/AshtonVaughan/bountyhound-local.git
cd bountyhound-local
bash scripts/vast-onstart.sh
```

**Step 4: Verify auto-detection chose dual-GPU config**

```bash
echo $BHL_CONFIG_PATH
# Expected: config/models-dual-gpu.yaml
```

**Step 5: Verify vLLM started with tensor_parallel_size=2**

```bash
cat logs/vllm-orchestrator.log | grep "tensor_parallel"
# Expected: tensor_parallel_size=2

nvidia-smi
# Expected: Both GPUs showing ~72GB used (model weights split evenly)
```

**Step 6: Run a test inference**

```bash
curl -s http://localhost:8100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen2.5-72B-Instruct","messages":[{"role":"user","content":"What is 2+2?"}],"max_tokens":50}' | python -m json.tool
```

**Step 7: Benchmark throughput**

```bash
# Compare generation speed vs single-GPU
time curl -s http://localhost:8100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen2.5-72B-Instruct","messages":[{"role":"user","content":"Write a detailed 500-word essay about cybersecurity."}],"max_tokens":500}'
```

Expected: 20-30 seconds (vs 2-3 minutes on single AWQ).

**Step 8: Run a full hunt test**

```bash
python cli.py add test-target.example.com --priority 5
python cli.py hunt test-target.example.com
python cli.py status
```

**Step 9: Commit any deployment fixes**

```bash
git add -A
git commit -m "fix: deployment adjustments from dual-GPU testing"
```

---

## Summary of Changes

| File | Change | Why |
|------|--------|-----|
| `config/models-dual-gpu.yaml` | NEW - FP16 config with TP2 | Core dual-GPU model config |
| `scripts/start-vllm.sh` | Extract `tensor_parallel_size` from YAML | Pass TP size to vLLM |
| `scripts/start.sh` | GPU count detection → config selection | Auto-select dual-GPU config |
| `scripts/vast-onstart.sh` | GPU-aware model download + config | Bootstrap for 2-GPU instances |
| `Dockerfile` | Add Ray, multi-GPU env vars | TP2 requires Ray for coordination |
| `docker-compose.yml` | `count: 2` GPUs | Reserve both GPUs |
| `scripts/status.sh` | Multi-GPU reporting loop | Show both GPU stats |
| `scripts/download-models.sh` | FP16 vs AWQ download selection | Download correct model variant |
| `README.md` | Dual-GPU documentation | User-facing docs |

## VRAM Budget (2x H100 NVL, ~190GB total)

```
72B FP16 (TP2):     ~144GB (72GB per GPU)
KV Cache:            ~40GB  (20GB per GPU)
Overhead:            ~6GB   (3GB per GPU)
─────────────────────────────
Total:               ~190GB  ← fits exactly in 2x H100 NVL
```

## Expected Performance Gains

| Metric | 1x H100 (AWQ) | 2x H100 (FP16 TP2) | Improvement |
|--------|---------------|---------------------|-------------|
| Throughput | ~3.2 tok/s | ~15-25 tok/s | 5-8x |
| Model quality | 4-bit quantized | Full FP16 | Higher accuracy |
| KV cache | ~50GB | ~40GB | Slightly less (more VRAM for weights) |
| Max batch | 2-3 concurrent | 3-5 concurrent | ~2x |
| Hunt completion | ~30-45 min | ~8-15 min | ~3x faster |
