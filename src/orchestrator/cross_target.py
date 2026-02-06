"""Cross-target intelligence - apply findings from one target to others."""

import json
import logging
from datetime import datetime

from src.database.models import TargetDB, FindingDB
from src.database.redis_manager import TaskQueue

logger = logging.getLogger("bhl.cross_target")


class CrossTargetAnalyzer:
    """Analyzes findings across all targets and generates transfer hypotheses."""

    TRANSFERABLE_PATTERNS = {
        "graphql_introspection": {
            "indicator": "graphql",
            "test": "GraphQL introspection may be enabled",
            "payload": '{"query":"{ __schema { types { name } } }"}',
        },
        "graphql_aliasing": {
            "indicator": "graphql",
            "test": "GraphQL aliasing for batch operations",
            "payload": '{"query":"{ a1: mutation1 { id } a2: mutation2 { id } }"}',
        },
        "idor_sequential": {
            "indicator": "api",
            "test": "Sequential ID IDOR on API endpoints",
            "payload": "Change numeric ID in URL path",
        },
        "jwt_none_alg": {
            "indicator": "jwt",
            "test": "JWT algorithm none bypass",
            "payload": "Modify JWT header to alg:none",
        },
        "cors_reflection": {
            "indicator": "api",
            "test": "CORS origin reflection with credentials",
            "payload": "Origin: https://evil.com",
        },
        "admin_panel": {
            "indicator": "web",
            "test": "Exposed admin panel at common paths",
            "payload": "/admin, /admin/login, /administrator, /wp-admin",
        },
        "debug_endpoints": {
            "indicator": "web",
            "test": "Debug/dev endpoints in production",
            "payload": "/debug, /trace, /actuator, /env, /.env",
        },
    }

    def analyze_findings(self, source_domain: str, findings: list[dict]):
        """Extract patterns from findings and store for cross-target transfer."""
        for finding in findings:
            pattern = {
                "source_domain": source_domain,
                "finding_type": finding.get("finding_type"),
                "category": finding.get("finding_type"),
                "technique": finding.get("title"),
                "payload": finding.get("payload"),
                "severity": finding.get("severity"),
                "discovered_at": datetime.utcnow().isoformat(),
            }
            TaskQueue.store_cross_target_pattern(pattern)
            logger.info(f"Stored cross-target pattern from {source_domain}: {finding.get('title', '')[:50]}")

    def get_transfer_hypotheses(self, target_domain: str, tech_stack: list[str]) -> list[dict]:
        """Generate hypotheses for a target based on findings from other targets."""
        patterns = TaskQueue.get_cross_target_patterns()
        target_patterns = [p for p in patterns if p.get("source_domain") != target_domain]

        hypotheses = []

        # Type 1: Direct transfer - same finding type found on another target
        seen_types = set()
        for pattern in target_patterns:
            ftype = pattern.get("finding_type", "")
            if ftype and ftype not in seen_types:
                seen_types.add(ftype)
                hypotheses.append({
                    "id": f"CT-{len(hypotheses)+1:03d}",
                    "hypothesis": f"{ftype} found on {pattern.get('source_domain')} - test on {target_domain}",
                    "category": pattern.get("category", ftype),
                    "confidence": "medium",
                    "reasoning": f"Successfully exploited on {pattern.get('source_domain')}",
                    "test_method": "curl",
                    "payload": pattern.get("payload", ""),
                    "success_indicator": f"Same behavior as on {pattern.get('source_domain')}",
                })

        # Type 2: Tech-stack matching
        tech_lower = [t.lower() for t in tech_stack]
        for name, pattern in self.TRANSFERABLE_PATTERNS.items():
            if any(pattern["indicator"] in t for t in tech_lower):
                hypotheses.append({
                    "id": f"CT-{len(hypotheses)+1:03d}",
                    "hypothesis": pattern["test"],
                    "category": "info_disclosure",
                    "confidence": "low",
                    "reasoning": f"Tech stack includes {pattern['indicator']}-related technology",
                    "test_method": "curl",
                    "payload": pattern["payload"],
                    "success_indicator": "Endpoint returns meaningful data",
                })

        return hypotheses[:15]
