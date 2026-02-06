# BountyHound Local - Vast.ai H100 NVL Setup Guide

Complete guide to deploying BountyHound Local on a Vast.ai H100 NVL GPU instance.

## What You Need on Vast.ai

### Instance Specifications

| Setting | Required Value | Why |
|---------|---------------|-----|
| **GPU** | H100 NVL (1x) | 94GB VRAM fits all 5 models |
| **GPU RAM** | >= 80 GB | 72B + 14B + 7B + 7B + 3B = ~80GB |
| **Disk Space** | >= 200 GB | 80GB models + 40GB OS/deps + 80GB workspace |
| **System RAM** | >= 64 GB | Celery workers + Redis + vLLM overhead |
| **CPU Cores** | >= 16 | 14 Celery workers + system processes |
| **Upload Speed** | >= 200 Mbps | Fast model downloads (~80GB) |
| **Direct Port Count** | >= 3 | SSH + Dashboard + Flower |
| **CUDA** | >= 12.0 | vLLM requirement |

### Pricing Estimate

| Type | Typical Rate | 24h Cost | Monthly |
|------|-------------|----------|---------|
| **Interruptible** | $1.50-2.50/hr | $36-60 | $1,080-1,800 |
| **On-Demand** | $2.50-4.00/hr | $60-96 | $1,800-2,880 |
| **Reserved** | $1.20-2.00/hr | $29-48 | $864-1,440 |

**Recommendation**: Use **On-Demand** for hunting sessions. Use **Interruptible** for overnight swarms (auto-saves to SQLite on interruption).

---

## Step-by-Step Setup

### Step 1: Create Vast.ai Account

1. Go to [cloud.vast.ai](https://cloud.vast.ai)
2. Create account, add $10+ credit (minimum $5)
3. Add your SSH key at **Account > SSH Keys**

### Step 2: Search for H100 NVL Instance

1. Go to **Search** tab
2. Set filters:

```
GPU Type:       H100 NVL
Num GPUs:       1
GPU RAM:        >= 80 GB
Disk Space:     >= 200 GB
CPU RAM:        >= 64 GB
CPU Cores:      >= 16
Direct Ports:   >= 3
CUDA Version:   >= 12.0
Reliability:    >= 0.95
Verified:       Yes (recommended)
```

3. Sort by **price (low to high)**
4. Pick a machine with good upload speed (for model downloads)

### Step 3: Configure Template

Click **RENT** on your chosen machine, then configure:

#### Docker Image
```
vllm/vllm-openai:latest
```
This image has CUDA, PyTorch, and vLLM pre-installed (~15GB, saves setup time).

#### Launch Mode
```
SSH / Jupyter
```
This gives you SSH access and lets you run the on-start script.

#### Disk Space
```
200 GB
```

#### Ports to Expose
Add these ports in the "Ports" section:
```
8000/tcp    (FastAPI Dashboard)
5555/tcp    (Flower Monitoring)
22/tcp      (SSH - usually auto-added)
```

Internal-only ports (no need to expose):
- 8100-8104 (vLLM model servers)
- 6379 (Redis)

#### On-Start Script
Paste this entire script into the **on-start** field:

```bash
#!/bin/bash
# BountyHound Local - Vast.ai Auto-Setup
cd /workspace

# Install Redis
apt-get update && apt-get install -y redis-server > /dev/null 2>&1
redis-server --daemonize yes --appendonly yes --dir /workspace/redis-data

# Clone or update repo
if [ -d "bountyhound-local" ]; then
    cd bountyhound-local && git pull
else
    git clone https://github.com/AshtonVaughan/bountyhound-local.git
    cd bountyhound-local
fi

# Install dependencies
pip install -r requirements.txt -q 2>/dev/null
pip install bountyhound huggingface_hub[cli] -q 2>/dev/null
playwright install chromium --with-deps 2>/dev/null

# Set environment
export HF_HOME=/workspace/models
export BHL_DB_PATH=/workspace/data/bountyhound.db
export BHL_VAST_AI=1
mkdir -p /workspace/models /workspace/data /workspace/bounty-findings /workspace/redis-data

# Download models (skips if already cached)
bash scripts/download-models.sh

# Start the system
bash scripts/start.sh
```

#### Environment Variables
Add these in the template env vars:
```
HF_HOME=/workspace/models
BHL_DB_PATH=/workspace/data/bountyhound.db
BHL_VAST_AI=1
PYTHONPATH=/workspace/bountyhound-local
```

If you have a HuggingFace token (for gated models):
```
HF_TOKEN=hf_your_token_here
```

### Step 4: Launch Instance

1. Click **RENT** to confirm
2. Wait for instance to start (1-3 minutes)
3. The on-start script will begin automatically

### Step 5: Connect via SSH

Once the instance shows "Running":

```bash
# Vast.ai shows you the SSH command, it looks like:
ssh -p <PORT> root@<IP_ADDRESS>

# Or use vast CLI:
vastai ssh-url <instance_id>
```

### Step 6: Monitor Setup Progress

```bash
# Watch the on-start script progress
tail -f /var/log/vast-ai-onstart.log

# Or check manually
cd /workspace/bountyhound-local
cat logs/vllm-orchestrator.log
```

### Step 7: Verify Everything Works

```bash
cd /workspace/bountyhound-local
bash scripts/status.sh
```

You should see all services HEALTHY. Model loading takes 3-8 minutes on first boot.

### Step 8: Access the Dashboard

Vast.ai maps your internal ports to random external ports. Find them:

```bash
# In the Vast.ai console, check "Open Ports" column
# Or in SSH:
echo "Dashboard: check Vast.ai console for port mapping to internal 8000"
echo "Flower:    check Vast.ai console for port mapping to internal 5555"
```

In the Vast.ai web console, click the **Open** button next to your instance, and you'll see mapped URLs like:
```
http://<ip>:<random_port>  →  :8000 (Dashboard)
http://<ip>:<random_port>  →  :5555 (Flower)
```

### Step 9: Start Hunting

```bash
cd /workspace/bountyhound-local

# Add targets
python cli.py add example.com --platform hackerone --priority 8
python cli.py add target2.com --platform bugcrowd --priority 6

# Hunt a single target
python cli.py hunt example.com

# Or start the autonomous swarm
python cli.py swarm

# Monitor
python cli.py status
```

---

## Alternative: Manual Setup (Without On-Start Script)

If you prefer manual control, SSH in and run:

```bash
# 1. Install Redis (no Docker-in-Docker on Vast.ai)
apt-get update && apt-get install -y redis-server
redis-server --daemonize yes --appendonly yes --dir /workspace/redis-data

# 2. Clone the repo
cd /workspace
git clone https://github.com/AshtonVaughan/bountyhound-local.git
cd bountyhound-local

# 3. Install dependencies
pip install -r requirements.txt
pip install bountyhound huggingface_hub[cli]

# 4. Install browser
playwright install chromium --with-deps

# 5. Install recon tools
pip install bountyhound
# For subfinder/httpx/nuclei (Go tools):
apt-get install -y golang-go
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
export PATH=$PATH:$HOME/go/bin

# 6. Set environment
export HF_HOME=/workspace/models
export BHL_DB_PATH=/workspace/data/bountyhound.db
export BHL_VAST_AI=1
mkdir -p /workspace/models /workspace/data /workspace/bounty-findings

# 7. Download models (~80GB, 15-45 min depending on bandwidth)
bash scripts/download-models.sh

# 8. Start everything
bash scripts/start.sh
```

---

## Vast.ai Instance Management

### Saving Money

```bash
# Stop the instance when not hunting (keeps disk, stops billing GPU)
# In Vast.ai console: click "Stop" on your instance

# Resume later: click "Start" - on-start script auto-restores everything
```

### Persistent Storage

Everything in `/workspace/` survives instance stop/start:
- `/workspace/models/` - HuggingFace model cache (80GB)
- `/workspace/data/` - SQLite database
- `/workspace/bounty-findings/` - Hunt results
- `/workspace/bountyhound-local/` - Source code + logs
- `/workspace/redis-data/` - Redis AOF persistence

**WARNING**: Destroying the instance deletes ALL data. Always download findings before destroying.

### Download Findings

```bash
# From your local machine:
scp -P <PORT> -r root@<IP>:/workspace/bounty-findings/ ./my-findings/

# Or use vast CLI:
vastai copy <instance_id> /workspace/bounty-findings/ ./my-findings/
```

### Using Vast.ai CLI

```bash
# Install
pip install vastai

# Set API key (from vast.ai account settings)
vastai set api-key <your_key>

# Search for H100 NVL instances
vastai search offers 'gpu_name=H100_NVL num_gpus=1 disk_space>=200 gpu_ram>=80 direct_port_count>=3 reliability>=0.95' -o 'dph'

# Create instance from template
vastai create instance <offer_id> \
    --image vllm/vllm-openai:latest \
    --disk 200 \
    --onstart-cmd 'bash /workspace/bountyhound-local/scripts/vast-onstart.sh' \
    --env 'HF_HOME=/workspace/models BHL_DB_PATH=/workspace/data/bountyhound.db BHL_VAST_AI=1' \
    --direct

# Check instance status
vastai show instances

# SSH into instance
vastai ssh-url <instance_id>

# Stop instance (preserves disk)
vastai stop instance <instance_id>

# Start instance (resumes)
vastai start instance <instance_id>

# Destroy instance (deletes everything)
vastai destroy instance <instance_id>
```

---

## Troubleshooting

### Models not loading / OOM

```bash
# Check GPU memory
nvidia-smi

# If VRAM is insufficient, use quantized models:
cp config/models-h100-awq.yaml config/models.yaml
bash scripts/start.sh
```

### Redis connection errors

```bash
# Check Redis
redis-cli ping

# If not running:
redis-server --daemonize yes --appendonly yes --dir /workspace/redis-data
```

### vLLM server not responding

```bash
# Check logs
tail -50 logs/vllm-orchestrator.log

# Common fix: wait longer for model loading
# The 72B model takes 3-8 minutes to load on H100
```

### Port not accessible externally

Vast.ai uses random port mapping. Check the Vast.ai console for your mapped ports. You CANNOT choose external port numbers - they are assigned automatically.

```bash
# Internal ports are always consistent:
# 8000 = Dashboard, 5555 = Flower, 6379 = Redis
# 8100-8104 = vLLM servers (internal only)
```

### Instance was interrupted (Interruptible pricing)

All state is preserved in SQLite and Redis AOF. When you start a new instance:
1. Attach the same disk (or use the on-start script to re-clone)
2. Models are cached in `/workspace/models/` (no re-download)
3. Database preserved in `/workspace/data/`
4. Run `scripts/start.sh` to resume

### Slow model downloads

```bash
# Use HuggingFace mirror if needed:
export HF_ENDPOINT=https://hf-mirror.com
bash scripts/download-models.sh
```

---

## Cost Optimization Tips

1. **Use Interruptible** for overnight swarms - 30-50% cheaper
2. **Stop when idle** - Don't pay GPU rates for analysis/reporting
3. **Cache models** - First boot downloads 80GB, subsequent boots use cache
4. **Batch targets** - Queue 10-50 targets, let swarm handle them all in one session
5. **Download findings before destroy** - Disk is deleted with instance
6. **Use Reserved** for 7+ day campaigns - cheapest per-hour rate
7. **Right-size disk** - 200GB is enough, don't overpay for storage
