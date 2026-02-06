"""Orchestrator brain - the central decision-making engine of BountyHound Local."""

import json
import time
import logging
from datetime import datetime, timezone, timedelta

from src.workers.celery_app import app
from src.models.vllm_client import get_llm
from src.database.models import (
    TargetDB, HuntDB, FindingDB, HypothesisDB, ReconDB, WorkerLogDB, init_db,
)
from src.database.redis_manager import TaskQueue
from src.services.scope_parser import is_in_scope, parse_scope
from src.orchestrator.scheduler import PriorityScheduler
from src.orchestrator.cross_target import CrossTargetAnalyzer

logger = logging.getLogger("bhl.orchestrator")


@app.task(name="src.orchestrator.brain.run_hunt", bind=True, queue="orchestrate")
def run_hunt(self, target_id: int):
    """Execute the full 4-phase hunt pipeline on a target."""
    target = TargetDB.get_by_id(target_id)
    if not target:
        return {"status": "error", "error": f"Target {target_id} not found"}

    domain = target["domain"]

    # Lock target to prevent parallel hunts
    if not TaskQueue.set_target_lock(domain, ttl=7200):
        return {"status": "skipped", "reason": f"{domain} is already being hunted"}

    try:
        hunt_id = HuntDB.create(target_id, "full")
        logger.info(f"Starting hunt {hunt_id} on {domain}")

        # PHASE 1: Recon
        HuntDB.update(hunt_id, phase="recon")
        from src.workers.recon import run_recon
        recon_result = run_recon(target_id, hunt_id, domain)

        if recon_result.get("status") == "error":
            HuntDB.update(hunt_id, status="failed", error=recon_result.get("error"))
            return {"status": "failed", "phase": "recon", "error": recon_result.get("error")}

        # PHASE 1.5: Discovery
        HuntDB.update(hunt_id, phase="discovery")
        recon_data = ReconDB.get_latest(target_id, "subdomains")
        recon_dict = recon_data.get("data", []) if recon_data else []

        from src.workers.discovery import generate_hypotheses
        discovery_result = generate_hypotheses(
            target_id, hunt_id, domain,
            {"subdomains": recon_dict, "technologies": []}
        )

        # PHASE 2: Parallel testing
        HuntDB.update(hunt_id, phase="testing")

        # Track A: Start nuclei scan (async)
        from src.workers.recon import run_scan
        scan_task = run_scan.delay(target_id, hunt_id, domain)

        # Track B: Test hypothesis cards
        cards = HypothesisDB.get_pending(hunt_id)
        from src.workers.exploit import test_hypothesis_browser
        test_results = []
        for card in cards:
            card["db_id"] = card["id"]
            result = test_hypothesis_browser(hunt_id, target_id, card, domain)
            test_results.append(result)

        # Wait for scan to complete
        scan_result = scan_task.get(timeout=900)

        # PHASE 3: Sync & dedupe
        HuntDB.update(hunt_id, phase="sync")
        all_findings = FindingDB.get_by_hunt(hunt_id)

        if not all_findings:
            # Gap-triggered second wave
            logger.info(f"No findings on {domain} - triggering gap discovery")
            failed_tests = [{"hypothesis": c.get("hypothesis"), "result": "no_finding"} for c in cards]
            from src.workers.discovery import gap_triggered_discovery
            gap_result = gap_triggered_discovery(target_id, hunt_id, domain, failed_tests, [])

            # Test second wave cards
            second_cards = HypothesisDB.get_pending(hunt_id)
            for card in second_cards:
                card["db_id"] = card["id"]
                test_hypothesis_browser(hunt_id, target_id, card, domain)

            all_findings = FindingDB.get_by_hunt(hunt_id)

        # PHASE 4: Validate findings
        HuntDB.update(hunt_id, phase="validation")
        unverified = FindingDB.get_unverified(hunt_id)

        from src.workers.validator import validate_finding
        verified_count = 0
        for finding in unverified:
            val_result = validate_finding(finding["id"], hunt_id)
            if val_result.get("status") == "CONFIRMED":
                verified_count += 1

        # Generate reports for verified findings
        HuntDB.update(hunt_id, phase="reporting")
        verified = [f for f in FindingDB.get_by_hunt(hunt_id) if f["status"] == "verified"]

        from src.workers.reporter import generate_report, generate_hunt_summary
        for finding in verified:
            platform = target.get("platform", "hackerone")
            generate_report(finding["id"], hunt_id, platform)

        generate_hunt_summary(hunt_id, target_id, domain)

        # Cross-target analysis
        cross_analyzer = CrossTargetAnalyzer()
        cross_analyzer.analyze_findings(domain, verified)

        # Complete hunt
        HuntDB.update(hunt_id,
                      status="complete",
                      completed_at=datetime.utcnow().isoformat(),
                      findings_count=verified_count)
        TargetDB.update(domain,
                        last_full_hunt_at=datetime.utcnow().isoformat(),
                        total_findings=len(verified))

        TaskQueue.increment_stat("hunts_completed")
        TaskQueue.increment_stat("findings_total", verified_count)

        logger.info(f"Hunt {hunt_id} on {domain} complete: {verified_count} verified findings")

        return {
            "status": "complete",
            "hunt_id": hunt_id,
            "domain": domain,
            "total_findings": len(all_findings),
            "verified": verified_count,
            "reported": len(verified),
        }

    finally:
        TaskQueue.release_target_lock(domain)


@app.task(name="src.orchestrator.brain.run_swarm")
def run_swarm():
    """Main swarm loop - select and hunt targets continuously."""
    init_db()
    scheduler = PriorityScheduler()

    targets = scheduler.get_next_batch()
    if not targets:
        logger.info("No targets available for hunting")
        return {"status": "idle", "reason": "no targets"}

    results = []
    for target in targets:
        if not TaskQueue.is_target_locked(target["domain"]):
            task = run_hunt.delay(target["id"])
            results.append({"domain": target["domain"], "task_id": str(task.id)})
            logger.info(f"Dispatched hunt for {target['domain']} (task: {task.id})")

    return {"status": "dispatched", "hunts": results}


@app.task(name="src.orchestrator.brain.schedule_light_retest")
def schedule_light_retest():
    """Periodic: light recon on all active targets."""
    targets = TargetDB.list_all()
    dispatched = 0

    for target in targets:
        if target["status"] == "disabled":
            continue
        last_recon = target.get("last_recon_at")
        if last_recon:
            last_dt = datetime.fromisoformat(last_recon)
            if datetime.utcnow() - last_dt < timedelta(hours=6):
                continue

        from src.workers.recon import light_recon
        light_recon.delay(target["id"], target["domain"])
        dispatched += 1

    return {"dispatched": dispatched}


@app.task(name="src.orchestrator.brain.schedule_full_retest")
def schedule_full_retest():
    """Periodic: full re-hunt on targets that haven't been tested recently."""
    targets = TargetDB.list_all()
    dispatched = 0

    for target in targets:
        if target["status"] == "disabled":
            continue
        last_hunt = target.get("last_full_hunt_at")
        if last_hunt:
            last_dt = datetime.fromisoformat(last_hunt)
            if datetime.utcnow() - last_dt < timedelta(days=7):
                continue

        run_hunt.delay(target["id"])
        dispatched += 1

    return {"dispatched": dispatched}


@app.task(name="src.orchestrator.brain.health_check")
def health_check():
    """Periodic: check system health."""
    llm = get_llm()
    model_status = llm.health_check()
    worker_status = TaskQueue.get_all_worker_status()
    stats = TaskQueue.get_stats()
    active_hunts = HuntDB.get_active()

    health = {
        "timestamp": datetime.utcnow().isoformat(),
        "models": model_status,
        "workers": len(worker_status),
        "active_hunts": len(active_hunts),
        "stats": stats,
    }

    TaskQueue.set_hunt_state(0, health)  # Store system health at hunt_id=0
    return health
