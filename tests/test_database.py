"""Tests for database models."""

import os
import tempfile
import pytest

os.environ["BHL_DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_bhl.db")

from src.database.models import init_db, TargetDB, HuntDB, FindingDB, HypothesisDB


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield
    if os.path.exists(os.environ["BHL_DB_PATH"]):
        os.remove(os.environ["BHL_DB_PATH"])


def test_add_and_get_target():
    target_id = TargetDB.add("example.com", "hackerone", priority=8, bounty_max=5000)
    assert target_id > 0

    target = TargetDB.get("example.com")
    assert target is not None
    assert target["domain"] == "example.com"
    assert target["platform"] == "hackerone"
    assert target["priority"] == 8


def test_duplicate_target():
    id1 = TargetDB.add("test.com")
    id2 = TargetDB.add("test.com")
    assert id1 == id2


def test_list_targets():
    TargetDB.add("a.com", priority=3)
    TargetDB.add("b.com", priority=8)
    TargetDB.add("c.com", priority=5)

    targets = TargetDB.list_all()
    assert len(targets) >= 3
    # Should be sorted by priority DESC
    priorities = [t["priority"] for t in targets]
    assert priorities == sorted(priorities, reverse=True)


def test_hunt_lifecycle():
    target_id = TargetDB.add("hunt-test.com")
    hunt_id = HuntDB.create(target_id, "full")
    assert hunt_id > 0

    hunt = HuntDB.get(hunt_id)
    assert hunt["status"] == "running"
    assert hunt["phase"] == "recon"

    HuntDB.update(hunt_id, phase="testing", status="running")
    hunt = HuntDB.get(hunt_id)
    assert hunt["phase"] == "testing"


def test_findings():
    target_id = TargetDB.add("findings-test.com")
    hunt_id = HuntDB.create(target_id, "full")

    finding_id = FindingDB.create(
        hunt_id=hunt_id, target_id=target_id,
        finding_type="xss", severity="high",
        title="Stored XSS in comments",
        url="https://findings-test.com/comments",
        payload="<script>alert(1)</script>",
    )
    assert finding_id > 0

    findings = FindingDB.get_by_hunt(hunt_id)
    assert len(findings) == 1
    assert findings[0]["title"] == "Stored XSS in comments"


def test_hypothesis_cards():
    target_id = TargetDB.add("hypo-test.com")
    hunt_id = HuntDB.create(target_id, "full")

    cards = [
        {"id": "H001", "hypothesis": "IDOR in /api/users", "confidence": "high",
         "category": "idor", "test_method": "curl", "payload": "change user ID",
         "success_indicator": "returns other user data", "reasoning": "sequential IDs"},
        {"id": "H002", "hypothesis": "XSS in search", "confidence": "medium",
         "category": "xss", "test_method": "browser", "payload": "<script>alert(1)</script>",
         "success_indicator": "alert fires", "reasoning": "input reflected without encoding"},
    ]

    HypothesisDB.create_batch(hunt_id, target_id, cards)
    pending = HypothesisDB.get_pending(hunt_id)
    assert len(pending) == 2
    # High confidence should come first
    assert pending[0]["confidence"] == "high"


def test_get_next_targets():
    TargetDB.add("never-hunted.com", priority=10)
    TargetDB.add("old-hunt.com", priority=5)
    TargetDB.update("old-hunt.com", last_full_hunt_at="2020-01-01T00:00:00")

    targets = TargetDB.get_next_targets(limit=2)
    assert len(targets) >= 1
    # Never-hunted should come first due to NULL last_full_hunt_at
    domains = [t["domain"] for t in targets]
    assert "never-hunted.com" in domains
