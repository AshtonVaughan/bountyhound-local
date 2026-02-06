"""FastAPI application - dashboard and REST API for BountyHound Local."""

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.database.models import TargetDB, HuntDB, FindingDB, WorkerLogDB, init_db
from src.database.redis_manager import TaskQueue
from src.orchestrator.scheduler import PriorityScheduler
from src.models.vllm_client import get_llm

app = FastAPI(title="BountyHound Local", version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def startup():
    init_db()


# ─── Dashboard ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    targets = TargetDB.list_all()
    active_hunts = HuntDB.get_active()
    stats = TaskQueue.get_stats()

    total_findings = sum(t.get("total_findings", 0) for t in targets)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "targets": targets,
        "active_hunts": active_hunts,
        "total_targets": len(targets),
        "total_findings": total_findings,
        "stats": stats,
    })


# ─── Targets API ─────────────────────────────────────────────

@app.get("/api/targets")
async def list_targets():
    return TargetDB.list_all()


@app.post("/api/targets")
async def add_target(data: dict):
    domain = data.get("domain")
    if not domain:
        raise HTTPException(400, "domain is required")

    target_id = TargetDB.add(
        domain=domain,
        platform=data.get("platform", "private"),
        program_url=data.get("program_url", ""),
        scope=data.get("scope", {}),
        bounty_min=data.get("bounty_min", 0),
        bounty_max=data.get("bounty_max", 0),
        priority=data.get("priority", 5),
        notes=data.get("notes", ""),
    )
    return {"id": target_id, "domain": domain, "status": "added"}


@app.delete("/api/targets/{domain}")
async def remove_target(domain: str):
    TargetDB.update(domain, status="disabled")
    return {"domain": domain, "status": "disabled"}


# ─── Hunts API ───────────────────────────────────────────────

@app.post("/api/hunts")
async def start_hunt(data: dict):
    domain = data.get("domain")
    target = TargetDB.get(domain)
    if not target:
        raise HTTPException(404, f"Target {domain} not found. Add it first.")

    from src.orchestrator.brain import run_hunt
    task = run_hunt.delay(target["id"])
    return {"hunt_task_id": str(task.id), "domain": domain, "status": "dispatched"}


@app.post("/api/swarm/start")
async def start_swarm():
    from src.orchestrator.brain import run_swarm
    task = run_swarm.delay()
    return {"task_id": str(task.id), "status": "swarm_dispatched"}


@app.get("/api/hunts/active")
async def active_hunts():
    return HuntDB.get_active()


@app.get("/api/hunts/{hunt_id}")
async def get_hunt(hunt_id: int):
    hunt = HuntDB.get(hunt_id)
    if not hunt:
        raise HTTPException(404, "Hunt not found")
    return hunt


# ─── Findings API ────────────────────────────────────────────

@app.get("/api/findings/{target_id}")
async def get_findings(target_id: int):
    return FindingDB.get_by_target(target_id)


@app.get("/api/findings/hunt/{hunt_id}")
async def get_hunt_findings(hunt_id: int):
    return FindingDB.get_by_hunt(hunt_id)


# ─── Scheduler API ───────────────────────────────────────────

@app.get("/api/scheduler")
async def scheduler_status():
    scheduler = PriorityScheduler()
    return scheduler.get_status()


# ─── Health API ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    try:
        llm = get_llm()
        model_status = llm.health_check()
    except Exception as e:
        model_status = {"error": str(e)}

    stats = TaskQueue.get_stats()
    workers = TaskQueue.get_all_worker_status()

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "models": model_status,
        "workers": len(workers),
        "stats": stats,
    }


# ─── Credentials API ────────────────────────────────────────

@app.get("/api/credentials")
async def list_credentials():
    from src.services.credential_manager import list_targets_with_creds
    return list_targets_with_creds()


@app.get("/api/credentials/{domain}")
async def get_credentials(domain: str):
    from src.services.credential_manager import load_credentials, mask_value
    creds = load_credentials(domain)
    if not creds:
        raise HTTPException(404, f"No credentials for {domain}")

    masked = {}
    sensitive_keys = ["PASSWORD", "TOKEN", "COOKIE", "CSRF", "SECRET", "KEY"]
    for k, v in creds.items():
        if any(s in k.upper() for s in sensitive_keys):
            masked[k] = mask_value(v)
        else:
            masked[k] = v
    return masked


# ─── Recon API ───────────────────────────────────────────────

@app.post("/api/recon")
async def start_recon(data: dict):
    domain = data.get("domain")
    target = TargetDB.get(domain)
    if not target:
        target_id = TargetDB.add(domain)
    else:
        target_id = target["id"]

    from src.workers.recon import run_recon
    hunt_id = HuntDB.create(target_id, "recon_only")
    task = run_recon.delay(target_id, hunt_id, domain)
    return {"task_id": str(task.id), "domain": domain, "status": "recon_dispatched"}
