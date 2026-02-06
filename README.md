# BountyHound Local

Autonomous bug bounty hunting swarm powered by local LLMs on H100 NVL GPU. Runs 24/7 across 10-50 targets with zero API costs.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   H100 NVL (94GB)                   │
│                                                     │
│  Qwen2.5-72B (orchestrator)           ~42GB         │
│  Qwen2.5-14B (discovery + auth)      ~14GB         │
│  DeepSeek-7B (exploit + report)       ~9GB         │
│  Mistral-7B (validation)              ~9GB         │
│  Phi-3-mini (utility)                 ~6GB         │
│                                    ────────         │
│                               Total: ~80GB          │
└─────────────────────────────────────────────────────┘
         │                    │                │
    ┌────┴────┐         ┌────┴────┐      ┌────┴────┐
    │  vLLM   │         │  Redis  │      │ SQLite  │
    │ :8100-  │         │ :6379   │      │  (disk) │
    │  8104   │         │ (tasks) │      │(persist)│
    └────┬────┘         └────┬────┘      └────┬────┘
         │                    │                │
    ┌────┴────────────────────┴────────────────┴────┐
    │              Celery Workers                     │
    │  orchestrate(1) recon(3) discovery(2)           │
    │  exploit(2) validate(4) report+auth(2)          │
    └────────────────────┬───────────────────────────┘
                         │
              ┌──────────┴──────────┐
              │   FastAPI Dashboard  │
              │   http://localhost   │
              │       :8000          │
              └─────────────────────┘
```

## Deployment Options

### Option A: Vast.ai (Recommended)

Rent an H100 NVL on [Vast.ai](https://vast.ai) for $1.50-4.00/hr. Full setup guide: **[VAST_AI_SETUP.md](VAST_AI_SETUP.md)**

**Quick version:**

1. Search Vast.ai for: `H100 NVL, 1 GPU, >= 200GB disk, >= 64GB RAM`
2. Select Docker image: `vllm/vllm-openai:latest`
3. Launch mode: SSH
4. Expose ports: `8000, 5555`
5. Paste the on-start script from `scripts/vast-onstart.sh`
6. SSH in and start hunting:

```bash
cd /workspace/bountyhound-local
python cli.py add example.com --priority 8
python cli.py hunt example.com
```

### Option B: Bare Metal

Requirements:
- **GPU**: NVIDIA H100 NVL (94GB) or equivalent with >= 80GB VRAM
- **OS**: Linux (Ubuntu 22.04+)
- **Python**: 3.10+
- **Docker**: For Redis + Flower
- **CUDA**: 12.0+

```bash
git clone https://github.com/AshtonVaughan/bountyhound-local.git
cd bountyhound-local
./install.sh
./scripts/start.sh
```

### Required CLI Tools

```bash
# bountyhound CLI (recon + scanning)
pip install bountyhound

# Recon tools
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
sudo apt install nmap
```

## Quick Start

```bash
# 1. Start all services
./scripts/start.sh

# 2. Add targets
python cli.py add example.com --platform hackerone --priority 8
python cli.py add target2.com --platform bugcrowd --priority 6

# 3. Hunt a single target
python cli.py hunt example.com

# 4. Or start the swarm (hunts all targets by priority)
python cli.py swarm

# 5. Monitor
# Dashboard: http://localhost:8000
# Flower:    http://localhost:5555
```

## Model Configurations

| Config | VRAM | Use Case |
|--------|------|----------|
| `config/models.yaml` | ~82GB | Full precision, H100 NVL (94GB) |
| `config/models-h100-awq.yaml` | ~65GB | AWQ quantized, H100 SXM/PCIe (80GB) |

Switch configs:
```bash
export BHL_CONFIG_PATH=./config/models-h100-awq.yaml
./scripts/start.sh
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `python cli.py add <domain>` | Add a target (--platform, --priority, --bounty-min/max) |
| `python cli.py targets` | List all targets with status |
| `python cli.py hunt <domain>` | Start a hunt on one target |
| `python cli.py swarm` | Start autonomous swarm across all targets |
| `python cli.py recon <domain>` | Run recon only |
| `python cli.py status` | Show system status |
| `python cli.py creds list` | List saved credentials |
| `python cli.py creds show <domain>` | Show credentials (masked) |
| `python cli.py creds refresh <domain>` | Refresh expired tokens |
| `python cli.py health` | Check all model servers |
| `python cli.py load` | Load targets from config/targets.yaml |

## REST API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/targets` | GET/POST | List or add targets |
| `/api/hunts` | POST | Start a hunt |
| `/api/swarm/start` | POST | Start the swarm |
| `/api/hunts/active` | GET | Active hunts |
| `/api/findings/{target_id}` | GET | Findings for target |
| `/api/scheduler` | GET | Priority queue status |
| `/api/health` | GET | System health |
| `/api/credentials` | GET | Saved credentials |
| `/api/recon` | POST | Start recon |

## Hunt Pipeline

```
Phase 1: RECON (bountyhound CLI)           ~5 min
  └─ subfinder → httpx → nmap

Phase 1.5: DISCOVERY (14B reasoning)       ~2 min
  └─ 4 tracks: Pattern + Anomaly + Code + Transfer
  └─ Output: 5-15 hypothesis cards

Phase 2: PARALLEL TESTING                  ~15 min
  ├─ Track A: nuclei scan (background)
  └─ Track B: browser + curl testing

Phase 3: SYNC & DEDUPE                    ~2 min
  └─ If nothing found → gap-triggered 2nd wave

Phase 4: VALIDATION & REPORTING            ~5 min
  ├─ PoC validation (curl-confirmed)
  └─ Platform-formatted reports

TOTAL: ~29 min per target
```

## Configuration

### config/targets.yaml

```yaml
targets:
  - domain: "example.com"
    platform: "hackerone"
    bounty_range: [100, 10000]
    priority: 8
    scope:
      in_scope: ["*.example.com"]
      out_of_scope: ["staging.example.com"]
```

## Output

Findings saved to `~/bounty-findings/<target>/` (or `/workspace/bounty-findings/` on Vast.ai):

```
<target>/
├── REPORT.md              # Hunt summary
├── reports/
│   └── F-1_hackerone_*.md # Platform-formatted reports
├── browser-findings.md
├── VERIFIED-*.md
├── screenshots/
└── credentials/
    └── <target>-creds.env
```

## Scripts

| Script | Description |
|--------|-------------|
| `./install.sh` | One-command setup (bare metal + Vast.ai) |
| `./scripts/start.sh` | Start all services |
| `./scripts/stop.sh` | Graceful shutdown |
| `./scripts/status.sh` | Health check (GPU, storage, services) |
| `./scripts/download-models.sh` | Download model weights (~80GB) |
| `./scripts/vast-onstart.sh` | Vast.ai instance bootstrap |
| `./scripts/start-vllm.sh` | Start vLLM servers only |
| `./scripts/start-workers.sh` | Start Celery workers only |

## Periodic Tasks (Celery Beat)

| Task | Interval | Description |
|------|----------|-------------|
| Light retest | 12 hours | Check for new subdomains |
| Full retest | 7 days | Complete hunt pipeline |
| Token refresh | 10 minutes | Check/refresh expired creds |
| Health check | 5 minutes | Monitor all services |

## Vast.ai Cost Optimization

| Strategy | Savings |
|----------|---------|
| Use **Interruptible** pricing for overnight swarms | 30-50% cheaper |
| **Stop** instance when not actively hunting | Pay only for disk |
| Models cached after first download | Skip 80GB on restart |
| Queue 10-50 targets, let swarm batch them | Max GPU utilization |
| Use **Reserved** for week-long campaigns | Cheapest per-hour |

See **[VAST_AI_SETUP.md](VAST_AI_SETUP.md)** for complete Vast.ai deployment guide.
