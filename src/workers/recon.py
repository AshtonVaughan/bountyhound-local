"""Recon worker - runs bountyhound CLI for reconnaissance and scanning."""

import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime

from src.workers.celery_app import app
from src.database.models import TargetDB, HuntDB, ReconDB, WorkerLogDB
from src.database.redis_manager import TaskQueue
from src.services.browser import run_bountyhound, run_curl


@app.task(name="src.workers.recon.run_recon", bind=True, queue="recon")
def run_recon(self, target_id: int, hunt_id: int, domain: str):
    """Phase 1: Run full recon via bountyhound CLI."""
    start = time.time()
    TaskQueue.set_worker_status(f"recon-{self.request.id}", {
        "task": "recon", "target": domain, "status": "running", "started": datetime.utcnow().isoformat()
    })

    # Add target to bountyhound
    run_bountyhound("target", "add", domain, timeout=30)

    # Run recon (subfinder -> httpx -> nmap)
    result = run_bountyhound("recon", domain, "--batch", timeout=300)

    if result["returncode"] != 0:
        WorkerLogDB.log("recon", "cli", "recon", hunt_id=hunt_id,
                        output_summary=f"FAILED: {result['stderr'][:200]}",
                        status="error", error=result["stderr"][:500])
        return {"status": "error", "error": result["stderr"][:500]}

    # Extract results from bountyhound database
    recon_data = _extract_recon_data(domain)

    # Store in our database
    ReconDB.store(target_id, "subdomains", recon_data.get("subdomains", []), "bountyhound-recon")
    ReconDB.store(target_id, "ports", recon_data.get("ports", []), "bountyhound-recon")
    ReconDB.store(target_id, "technologies", recon_data.get("technologies", []), "bountyhound-recon")

    TargetDB.update(domain, last_recon_at=datetime.utcnow().isoformat(), status="recon_complete")
    HuntDB.checkpoint(hunt_id, "recon_complete", recon_data)

    duration = time.time() - start
    WorkerLogDB.log("recon", "cli", "recon", hunt_id=hunt_id,
                    output_summary=f"Found {len(recon_data.get('subdomains', []))} subdomains",
                    duration_seconds=duration)

    TaskQueue.set_worker_status(f"recon-{self.request.id}", {"status": "complete"})
    return {
        "status": "complete",
        "subdomains": len(recon_data.get("subdomains", [])),
        "live_hosts": len([s for s in recon_data.get("subdomains", []) if s.get("status_code")]),
        "duration": duration,
    }


@app.task(name="src.workers.recon.run_scan", bind=True, queue="recon")
def run_scan(self, target_id: int, hunt_id: int, domain: str):
    """Phase 2 Track A: Run nuclei scan via bountyhound CLI."""
    start = time.time()
    TaskQueue.set_worker_status(f"scan-{self.request.id}", {
        "task": "scan", "target": domain, "status": "running"
    })

    result = run_bountyhound("scan", domain, "--batch", timeout=900)

    scan_findings = _extract_scan_findings(domain)

    for finding in scan_findings:
        from src.database.models import FindingDB
        FindingDB.create(
            hunt_id=hunt_id, target_id=target_id,
            finding_type=finding.get("type", "unknown"),
            severity=finding.get("severity", "info"),
            title=finding.get("name", "Nuclei finding"),
            url=finding.get("url", ""),
            payload=finding.get("template", ""),
            discovered_by="nuclei",
        )

    TargetDB.update(domain, last_scan_at=datetime.utcnow().isoformat())

    duration = time.time() - start
    WorkerLogDB.log("recon", "cli", "scan", hunt_id=hunt_id,
                    output_summary=f"Found {len(scan_findings)} findings",
                    duration_seconds=duration)

    TaskQueue.set_worker_status(f"scan-{self.request.id}", {"status": "complete"})
    return {
        "status": "complete",
        "findings": len(scan_findings),
        "duration": duration,
    }


@app.task(name="src.workers.recon.light_recon", bind=True, queue="recon")
def light_recon(self, target_id: int, domain: str):
    """Light recon for periodic monitoring - check for new subdomains/endpoints."""
    previous = ReconDB.get_latest(target_id, "subdomains")
    previous_domains = set()
    if previous and previous.get("data"):
        previous_domains = {s.get("hostname", "") for s in previous["data"]}

    result = run_bountyhound("recon", domain, "--batch", timeout=300)
    current = _extract_recon_data(domain)
    current_domains = {s.get("hostname", "") for s in current.get("subdomains", [])}

    new_domains = current_domains - previous_domains
    if new_domains:
        ReconDB.store(target_id, "subdomains", current.get("subdomains", []), "light-recon")
        return {"status": "changes_detected", "new_subdomains": list(new_domains)}

    return {"status": "no_changes"}


def _extract_recon_data(domain: str) -> dict:
    """Extract recon results from bountyhound's SQLite database."""
    bh_db_path = Path.home() / ".bountyhound" / "bountyhound.db"
    if not bh_db_path.exists():
        return {"subdomains": [], "ports": [], "technologies": []}

    try:
        conn = sqlite3.connect(str(bh_db_path))
        conn.row_factory = sqlite3.Row

        target_row = conn.execute(
            "SELECT id FROM targets WHERE domain = ?", (domain,)
        ).fetchone()

        if not target_row:
            conn.close()
            return {"subdomains": [], "ports": [], "technologies": []}

        target_id = target_row["id"]

        subdomains = conn.execute(
            "SELECT * FROM subdomains WHERE target_id = ?", (target_id,)
        ).fetchall()

        data = {
            "subdomains": [dict(s) for s in subdomains],
            "ports": [],
            "technologies": [],
        }
        conn.close()
        return data
    except Exception:
        return {"subdomains": [], "ports": [], "technologies": []}


def _extract_scan_findings(domain: str) -> list[dict]:
    """Extract scan findings from bountyhound's SQLite database."""
    bh_db_path = Path.home() / ".bountyhound" / "bountyhound.db"
    if not bh_db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(bh_db_path))
        conn.row_factory = sqlite3.Row

        findings = conn.execute("""
            SELECT f.* FROM findings f
            JOIN subdomains s ON f.subdomain_id = s.id
            JOIN targets t ON s.target_id = t.id
            WHERE t.domain = ?
        """, (domain,)).fetchall()

        result = [dict(f) for f in findings]
        conn.close()
        return result
    except Exception:
        return []
