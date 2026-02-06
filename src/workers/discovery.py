"""Discovery worker - LLM-powered vulnerability hypothesis generation."""

import json
import time
import yaml
from datetime import datetime
from pathlib import Path

from src.workers.celery_app import app
from src.models.vllm_client import get_llm
from src.database.models import HuntDB, HypothesisDB, ReconDB, FindingDB, WorkerLogDB
from src.database.redis_manager import TaskQueue


PAYLOADS_DIR = Path(__file__).parent.parent.parent / "data" / "payloads"


@app.task(name="src.workers.discovery.generate_hypotheses", bind=True, queue="discovery")
def generate_hypotheses(self, target_id: int, hunt_id: int, domain: str, recon_data: dict):
    """Phase 1.5: Generate 5-15 hypothesis cards from recon data."""
    start = time.time()
    TaskQueue.set_worker_status(f"discovery-{self.request.id}", {
        "task": "discovery", "target": domain, "status": "running"
    })

    llm = get_llm()

    # Build context from recon data
    subdomains = recon_data.get("subdomains", [])
    live_hosts = [s for s in subdomains if s.get("status_code")]

    # Load cross-target patterns for Track D
    cross_patterns = TaskQueue.get_cross_target_patterns()

    prompt = f"""Analyze the following recon data for {domain} and generate 5-15 vulnerability hypothesis cards.

## Recon Data
- Total subdomains: {len(subdomains)}
- Live hosts: {len(live_hosts)}
- Live URLs: {json.dumps([s.get('hostname', '') for s in live_hosts[:20]], indent=2)}
- Technologies detected: {json.dumps(recon_data.get('technologies', []), indent=2)}

## Run ALL 4 reasoning tracks:

Track A (Pattern Synthesis): Cross-reference tech stack with known vulnerability patterns.
Track B (Behavioral Anomaly): Look for auth inconsistencies, timing differences, error leakage.
Track C (Code Research): If any GitHub repos or source code visible, note dangerous patterns.
Track D (Cross-Domain Transfer): Apply these patterns from past hunts: {json.dumps(cross_patterns[:5], indent=2)}

## Output
Return a JSON array of hypothesis cards. Each card:
{{
  "id": "H001",
  "hypothesis": "Description",
  "category": "sqli|xss|idor|auth_bypass|ssrf|rce|info_disclosure|business_logic",
  "confidence": "high|medium|low",
  "reasoning": "Why this might exist",
  "test_method": "curl|browser|both",
  "payload": "Exact test payload",
  "success_indicator": "What confirms vulnerability"
}}

Prioritize novel findings scanners miss. Business logic > technical vulns."""

    response = llm.chat_json("discovery", [{"role": "user", "content": prompt}],
                             temperature=0.8, max_tokens=8192)

    cards = response if isinstance(response, list) else response.get("hypotheses", response.get("cards", []))

    # Store hypothesis cards
    HypothesisDB.create_batch(hunt_id, target_id, cards)
    HuntDB.checkpoint(hunt_id, "discovery_complete", {"hypothesis_count": len(cards)})

    duration = time.time() - start
    high = len([c for c in cards if c.get("confidence") == "high"])
    medium = len([c for c in cards if c.get("confidence") == "medium"])
    low = len([c for c in cards if c.get("confidence") == "low"])

    WorkerLogDB.log("discovery", "qwen-14b", "generate_hypotheses", hunt_id=hunt_id,
                    output_summary=f"Generated {len(cards)} cards ({high}H/{medium}M/{low}L)",
                    duration_seconds=duration)

    TaskQueue.set_worker_status(f"discovery-{self.request.id}", {"status": "complete"})
    return {
        "status": "complete",
        "total_cards": len(cards),
        "high": high, "medium": medium, "low": low,
        "duration": duration,
    }


@app.task(name="src.workers.discovery.gap_triggered_discovery", bind=True, queue="discovery")
def gap_triggered_discovery(self, target_id: int, hunt_id: int, domain: str,
                            failed_tests: list, observed_defenses: list):
    """Gap-triggered second-wave discovery when Phase 2 finds nothing."""
    start = time.time()
    llm = get_llm()

    prompt = f"""Phase 2 scanning of {domain} found NOTHING. Generate second-wave hypotheses.

## What was tested and failed:
{json.dumps(failed_tests[:20], indent=2)}

## Observed defenses:
{json.dumps(observed_defenses[:10], indent=2)}

## Generate second-wave hypotheses focusing on:
1. WAF bypass variants for blocked payloads
2. Timing-based attacks (race conditions, time-based blind injection)
3. Business logic flaws that scanners can't detect
4. Chain opportunities - combine low-impact findings
5. Alternative injection points (headers, cookies, file uploads)
6. NoSQL/LDAP/XPath injection if SQL was blocked
7. SSRF via internal services
8. Deserialization attacks

Return JSON array of hypothesis cards. Be creative and think like an elite researcher."""

    response = llm.chat_json("discovery", [{"role": "user", "content": prompt}],
                             temperature=0.9, max_tokens=8192)

    cards = response if isinstance(response, list) else response.get("hypotheses", response.get("cards", []))

    HypothesisDB.create_batch(hunt_id, target_id, cards)

    duration = time.time() - start
    WorkerLogDB.log("discovery", "qwen-14b", "gap_triggered", hunt_id=hunt_id,
                    output_summary=f"Second wave: {len(cards)} cards",
                    duration_seconds=duration)

    return {"status": "complete", "total_cards": len(cards), "duration": duration}
