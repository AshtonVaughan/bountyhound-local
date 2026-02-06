# BountyHound Local

Autonomous bug bounty hunting swarm powered by local LLMs on H100 NVL GPU. Runs 24/7 across 10-50 targets with zero API costs.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   H100 NVL (94GB)                   │
│                                                     │
│  Qwen2.5-72B (orchestrator)           ~40GB         │
│  Qwen2.5-14B x2 (discovery + auth)   ~12GB         │
│  DeepSeek-7B x2 (exploit + report)    ~8GB         │
│  Mistral-7B x2 (validation)           ~8GB         │
│  Phi-3-mini x2 (utility)              ~4GB         │
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

## Prerequisites

- **GPU**: NVIDIA H100 NVL (94GB) or equivalent
- **OS**: Linux (Ubuntu 22.04+ recommended)
- **Python**: 3.10+
- **Docker**: For Redis
- **CUDA**: 12.0+

### Required CLI Tools

```bash
# bountyhound CLI (recon + scanning)
pip install bountyhound

# Recon tools (used by bountyhound)
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
sudo apt install nmap
```

## Installation

```bash
git clone https://github.com/yourusername/bountyhound-local.git
cd bountyhound-local
./install.sh
```

The installer handles:
1. Python venv + dependencies
2. bountyhound CLI + recon tools check
3. Playwright browser
4. Redis via Docker
5. Model downloads (~80GB)
6. Database initialization

## Quick Start

```bash
# 1. Start all services
./scripts/start.sh

# 2. Add targets
bhl add example.com --platform hackerone --priority 8
bhl add target2.com --platform bugcrowd --priority 6

# 3. Hunt a single target
bhl hunt example.com

# 4. Or start the swarm (hunts all targets by priority)
bhl swarm

# 5. Monitor
# Dashboard: http://localhost:8000
# Flower:    http://localhost:5555
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `bhl add <domain>` | Add a target (--platform, --priority, --bounty-min/max) |
| `bhl targets` | List all targets with status |
| `bhl hunt <domain>` | Start a hunt on one target |
| `bhl swarm` | Start autonomous swarm across all targets |
| `bhl recon <domain>` | Run recon only |
| `bhl status` | Show system status |
| `bhl creds list` | List saved credentials |
| `bhl creds show <domain>` | Show credentials (masked) |
| `bhl creds refresh <domain>` | Refresh expired tokens |
| `bhl health` | Check all model servers |
| `bhl load` | Load targets from config/targets.yaml |

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

### config/models.yaml

Adjust VRAM allocation per model. Default config uses ~80GB of 94GB.

## Output

Findings saved to `~/bounty-findings/<target>/`:

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
| `./install.sh` | One-command setup |
| `./scripts/start.sh` | Start all services |
| `./scripts/stop.sh` | Graceful shutdown |
| `./scripts/status.sh` | Health check |
| `./scripts/download-models.sh` | Download model weights |

## Periodic Tasks (Celery Beat)

| Task | Interval | Description |
|------|----------|-------------|
| Light retest | 12 hours | Check for new subdomains |
| Full retest | 7 days | Complete hunt pipeline |
| Token refresh | 10 minutes | Check/refresh expired creds |
| Health check | 5 minutes | Monitor all services |
