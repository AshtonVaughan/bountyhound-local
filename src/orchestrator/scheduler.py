"""Priority scheduler - decides which targets to hunt next."""

import json
import yaml
from datetime import datetime, timedelta
from pathlib import Path

from src.database.models import TargetDB, HuntDB, FindingDB


class PriorityScheduler:
    """Scores and prioritizes targets for hunting."""

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent

    def score_target(self, target: dict) -> float:
        """Calculate priority score for a target.

        Score = bounty_value * endpoint_factor * staleness * findings_rate * manual_priority
        """
        bounty_avg = (target.get("bounty_min", 0) + target.get("bounty_max", 1000)) / 2
        bounty_factor = max(bounty_avg / 1000, 0.1)

        # Staleness: how long since last hunt
        last_hunt = target.get("last_full_hunt_at")
        if not last_hunt:
            staleness = 10.0  # Never hunted - highest priority
        else:
            hours_since = (datetime.utcnow() - datetime.fromisoformat(last_hunt)).total_seconds() / 3600
            staleness = min(hours_since / 24, 10.0)

        # Past findings rate
        total_findings = target.get("total_findings", 0)
        findings_rate = 1.0 + (total_findings * 0.2)

        # Manual priority (1-10)
        manual = target.get("priority", 5) / 5.0

        score = bounty_factor * staleness * findings_rate * manual
        return round(score, 2)

    def get_next_batch(self) -> list[dict]:
        """Get the next batch of targets to hunt, sorted by priority."""
        targets = TargetDB.list_all()
        active_hunts = HuntDB.get_active()
        active_domains = {
            TargetDB.get_by_id(h["target_id"])["domain"]
            for h in active_hunts
            if TargetDB.get_by_id(h["target_id"])
        }

        available = [t for t in targets if t["status"] != "disabled" and t["domain"] not in active_domains]

        scored = [(self.score_target(t), t) for t in available]
        scored.sort(key=lambda x: x[0], reverse=True)

        slots = self.max_concurrent - len(active_hunts)
        if slots <= 0:
            return []

        return [t for _, t in scored[:slots]]

    def get_status(self) -> dict:
        """Get current scheduler status."""
        targets = TargetDB.list_all()
        active_hunts = HuntDB.get_active()

        scored = [(self.score_target(t), t) for t in targets]
        scored.sort(key=lambda x: x[0], reverse=True)

        return {
            "total_targets": len(targets),
            "active_hunts": len(active_hunts),
            "available_slots": self.max_concurrent - len(active_hunts),
            "priority_queue": [
                {
                    "domain": t["domain"],
                    "score": s,
                    "last_hunt": t.get("last_full_hunt_at", "never"),
                    "findings": t.get("total_findings", 0),
                    "status": t.get("status", "unknown"),
                }
                for s, t in scored[:10]
            ],
        }
