"""Validator worker - independent PoC verification using curl."""

import json
import time
import subprocess

from src.workers.celery_app import app
from src.models.vllm_client import get_llm
from src.database.models import FindingDB, WorkerLogDB
from src.database.redis_manager import TaskQueue
from src.services.browser import run_curl


@app.task(name="src.workers.validator.validate_finding", bind=True, queue="validate")
def validate_finding(self, finding_id: int, hunt_id: int):
    """Validate a single finding through the fail-fast pipeline."""
    start = time.time()
    TaskQueue.set_worker_status(f"validator-{self.request.id}", {
        "task": "validate", "finding_id": finding_id, "status": "running"
    })

    from src.database.models import get_db
    conn = get_db()
    finding = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
    conn.close()

    if not finding:
        return {"status": "error", "error": "Finding not found"}

    finding = dict(finding)
    url = finding.get("url", "")
    finding_type = finding.get("finding_type", "")

    steps = []

    # Step 1: DNS Resolution
    domain = _extract_domain(url)
    dns_result = run_curl(f"nslookup {domain}", timeout=10)
    dns_pass = "Address" in dns_result.get("stdout", "") and "NXDOMAIN" not in dns_result.get("stdout", "")
    steps.append({"step": "dns", "result": "PASS" if dns_pass else "FAIL",
                  "evidence": dns_result["stdout"][:200]})
    if not dns_pass:
        return _fail_finding(finding_id, steps, "Domain does not resolve", start)

    # Step 2: HTTP Reachability
    http_result = run_curl(f'curl -s -I -m 10 "https://{domain}"')
    http_pass = http_result["returncode"] == 0 and http_result["stdout"].strip()
    waf_blocked = _check_waf(http_result["stdout"])
    steps.append({"step": "http", "result": "PASS" if http_pass and not waf_blocked else "FAIL",
                  "evidence": http_result["stdout"][:300]})
    if not http_pass:
        return _fail_finding(finding_id, steps, f"Host unreachable (exit {http_result['returncode']})", start)

    # Step 3: Endpoint Existence
    endpoint_result = run_curl(f'curl -s -o /dev/null -w "%{{http_code}}" -m 10 "{url}"')
    status_code = endpoint_result["stdout"].strip()
    endpoint_pass = status_code.startswith("2") or status_code == "301" or status_code == "302"

    body_result = run_curl(f'curl -s -m 10 "{url}"', timeout=15)
    body = body_result["stdout"][:3000]

    steps.append({"step": "endpoint", "result": "PASS" if endpoint_pass else "FAIL",
                  "evidence": f"HTTP {status_code}, body length: {len(body)}"})

    if not endpoint_pass and status_code in ("404", "403", "401"):
        return _fail_finding(finding_id, steps, f"Endpoint returns HTTP {status_code}", start)

    # Step 4: Vulnerability-Specific Proof
    llm = get_llm()
    proof_prompt = f"""You are validating a claimed {finding_type} vulnerability.

URL: {url}
Claimed behavior: {finding.get('description', '')}
Payload used: {finding.get('payload', '')}
Curl command: {finding.get('curl_command', '')}

I ran the curl command and got this response:
Status: {status_code}
Body (first 2000 chars):
{body[:2000]}

Based on the ACTUAL response data above, is this vulnerability CONFIRMED or FALSE_POSITIVE?

Return JSON:
{{
  "verdict": "CONFIRMED|FALSE_POSITIVE",
  "evidence": "specific evidence from the response that proves/disproves",
  "reasoning": "1-2 sentences",
  "severity_adjustment": "none|upgrade|downgrade",
  "adjusted_severity": "critical|high|medium|low|info"
}}

CRITICAL: A 403/WAF block is FALSE_POSITIVE. A 401 is FALSE_POSITIVE. Empty response is FALSE_POSITIVE.
Only CONFIRMED if the response contains actual evidence of the vulnerability."""

    verdict = llm.chat_json("validator", [{"role": "user", "content": proof_prompt}],
                            temperature=0.1, max_tokens=1024)

    steps.append({"step": "vuln_proof", "result": "PASS" if verdict.get("verdict") == "CONFIRMED" else "FAIL",
                  "evidence": verdict.get("evidence", "")[:300]})

    duration = time.time() - start

    if verdict.get("verdict") == "CONFIRMED":
        FindingDB.update(finding_id,
                         status="verified",
                         verified_by="poc-validator",
                         verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"))

        if verdict.get("severity_adjustment") != "none":
            FindingDB.update(finding_id, severity=verdict.get("adjusted_severity", finding["severity"]))

        WorkerLogDB.log("validator", "mistral-7b", "validate", hunt_id=hunt_id,
                        output_summary=f"CONFIRMED: {finding['title'][:100]}",
                        duration_seconds=duration)

        TaskQueue.set_worker_status(f"validator-{self.request.id}", {"status": "complete"})
        return {
            "status": "CONFIRMED",
            "finding_id": finding_id,
            "steps": steps,
            "verdict": verdict,
            "duration": duration,
        }
    else:
        return _fail_finding(finding_id, steps, verdict.get("reasoning", "Not confirmed"), start)


@app.task(name="src.workers.validator.batch_validate", bind=True, queue="validate")
def batch_validate(self, hunt_id: int, finding_ids: list[int]):
    """Validate multiple findings in sequence."""
    results = []
    for fid in finding_ids[:10]:
        result = validate_finding(fid, hunt_id)
        results.append(result)
    return {
        "total": len(results),
        "confirmed": len([r for r in results if r.get("status") == "CONFIRMED"]),
        "false_positive": len([r for r in results if r.get("status") == "FALSE_POSITIVE"]),
        "results": results,
    }


def _fail_finding(finding_id: int, steps: list, reason: str, start_time: float) -> dict:
    FindingDB.update(finding_id, status="false_positive",
                     verified_by="poc-validator",
                     verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    duration = time.time() - start_time
    WorkerLogDB.log("validator", "mistral-7b", "validate",
                    output_summary=f"FALSE_POSITIVE: {reason[:100]}",
                    duration_seconds=duration)
    return {
        "status": "FALSE_POSITIVE",
        "finding_id": finding_id,
        "steps": steps,
        "reason": reason,
        "duration": duration,
    }


def _extract_domain(url: str) -> str:
    if "://" in url:
        from urllib.parse import urlparse
        return urlparse(url).hostname or url
    return url.split("/")[0].split(":")[0]


def _check_waf(headers: str) -> bool:
    waf_indicators = [
        "cloudflare", "akamai", "incapsula", "imperva",
        "sucuri", "barracuda", "f5", "fortinet",
        "attention required", "access denied", "request blocked",
    ]
    headers_lower = headers.lower()
    return any(indicator in headers_lower for indicator in waf_indicators)
