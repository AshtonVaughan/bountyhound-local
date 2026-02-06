"""Reporter worker - generates platform-optimized vulnerability reports."""

import json
import time
from pathlib import Path
from datetime import datetime

from src.workers.celery_app import app
from src.models.vllm_client import get_llm
from src.database.models import FindingDB, HuntDB, TargetDB, WorkerLogDB
from src.services.scope_parser import check_finding_eligibility


FINDINGS_DIR = Path.home() / "bounty-findings"


@app.task(name="src.workers.reporter.generate_report", bind=True, queue="report")
def generate_report(self, finding_id: int, hunt_id: int, platform: str = "hackerone"):
    """Generate a platform-formatted vulnerability report."""
    start = time.time()
    llm = get_llm()

    from src.database.models import get_db
    conn = get_db()
    finding = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
    hunt = conn.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()
    conn.close()

    if not finding:
        return {"status": "error", "error": "Finding not found"}

    finding = dict(finding)
    target = TargetDB.get_by_id(finding["target_id"])

    # Check eligibility
    eligibility = check_finding_eligibility(finding["finding_type"], platform)
    if not eligibility["eligible"]:
        return {"status": "ineligible", "reason": eligibility["reason"]}

    prompt = f"""Generate a professional bug bounty report for {platform}.

## Finding Details
- Title: {finding['title']}
- Type: {finding['finding_type']}
- Severity: {finding['severity']}
- URL: {finding['url']}
- Payload: {finding.get('payload', 'N/A')}
- Curl Command: {finding.get('curl_command', 'N/A')}
- Description: {finding.get('description', '')}
- Evidence: {finding.get('evidence_json', '{}')}

## Target
- Domain: {target['domain'] if target else 'unknown'}
- Platform: {platform}

## Report Format for {platform}
Generate the complete report with these sections:
1. Title: "[Vuln Type] in [Location] allows [Impact]"
2. Summary: 2-3 sentences (what, how, impact)
3. Severity justification with CVSS
4. Steps to reproduce (numbered, exact URLs, exact payloads)
5. Impact statement (business consequences)
6. Remediation (specific fixes with code)

Return JSON:
{{
  "title": "...",
  "severity": "critical|high|medium|low",
  "cvss_score": 0.0,
  "report_body": "Full markdown report",
  "duplicate_risk": "low|medium|high",
  "estimated_bounty": "$X-$Y",
  "quality_score": 0-100
}}

Focus on BUSINESS IMPACT. Make reproduction steps trivial for triagers.
Use their own severity criteria to justify ratings."""

    response = llm.chat_json("reporter", [{"role": "user", "content": prompt}],
                             temperature=0.5, max_tokens=8192)

    # Save report to file
    domain = target["domain"] if target else "unknown"
    report_dir = FINDINGS_DIR / domain / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"F-{finding_id}_{platform}_{ts}.md"

    report_body = response.get("report_body", "")
    with open(report_path, "w") as f:
        f.write(report_body)

    # Update finding with report
    FindingDB.update(finding_id,
                     status="reported",
                     report_json=json.dumps(response),
                     reported_at=datetime.utcnow().isoformat())

    duration = time.time() - start
    WorkerLogDB.log("reporter", "deepseek-7b", "generate_report", hunt_id=hunt_id,
                    output_summary=f"Report: {response.get('title', '')[:100]}",
                    duration_seconds=duration)

    return {
        "status": "complete",
        "finding_id": finding_id,
        "report_path": str(report_path),
        "title": response.get("title"),
        "severity": response.get("severity"),
        "estimated_bounty": response.get("estimated_bounty"),
        "quality_score": response.get("quality_score"),
        "duration": duration,
    }


@app.task(name="src.workers.reporter.generate_hunt_summary", bind=True, queue="report")
def generate_hunt_summary(self, hunt_id: int, target_id: int, domain: str):
    """Generate a summary report for an entire hunt."""
    findings = FindingDB.get_by_hunt(hunt_id)
    verified = [f for f in findings if f["status"] == "verified"]
    reported = [f for f in findings if f["status"] == "reported"]

    report_dir = FINDINGS_DIR / domain
    report_dir.mkdir(parents=True, exist_ok=True)

    summary_lines = [
        f"# Hunt Report: {domain}",
        f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Hunt ID:** {hunt_id}",
        "",
        f"## Summary",
        f"- Total findings: {len(findings)}",
        f"- Verified: {len(verified)}",
        f"- Reported: {len(reported)}",
        f"- False positives: {len([f for f in findings if f['status'] == 'false_positive'])}",
        "",
        "## Findings by Severity",
    ]

    for severity in ["critical", "high", "medium", "low", "info"]:
        sev_findings = [f for f in verified if f["severity"] == severity]
        if sev_findings:
            summary_lines.append(f"\n### {severity.upper()} ({len(sev_findings)})")
            for f in sev_findings:
                summary_lines.append(f"- **{f['title']}** ({f['finding_type']}) - {f['url']}")

    summary_lines.extend(["", "## Verified Findings Details", ""])
    for f in verified:
        summary_lines.extend([
            f"### F-{f['id']}: {f['title']}",
            f"- **Type:** {f['finding_type']}",
            f"- **Severity:** {f['severity']}",
            f"- **URL:** {f['url']}",
            f"- **Payload:** `{f.get('payload', 'N/A')}`",
            f"- **Curl:** `{f.get('curl_command', 'N/A')}`",
            "",
        ])

    report_path = report_dir / "REPORT.md"
    with open(report_path, "w") as f:
        f.write("\n".join(summary_lines))

    return {"status": "complete", "path": str(report_path), "verified_count": len(verified)}
