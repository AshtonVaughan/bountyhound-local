"""Tests for priority scheduler."""

import os
import tempfile
import pytest

os.environ["BHL_DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_scheduler.db")

from src.database.models import init_db, TargetDB
from src.orchestrator.scheduler import PriorityScheduler


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield
    if os.path.exists(os.environ["BHL_DB_PATH"]):
        os.remove(os.environ["BHL_DB_PATH"])


def test_score_never_hunted():
    scheduler = PriorityScheduler()

    target = {
        "bounty_min": 100,
        "bounty_max": 10000,
        "last_full_hunt_at": None,
        "total_findings": 0,
        "priority": 5,
    }

    score = scheduler.score_target(target)
    assert score > 0
    # Never hunted gets staleness=10
    assert score >= 5.0


def test_score_recently_hunted():
    scheduler = PriorityScheduler()
    from datetime import datetime

    target = {
        "bounty_min": 100,
        "bounty_max": 10000,
        "last_full_hunt_at": datetime.utcnow().isoformat(),
        "total_findings": 0,
        "priority": 5,
    }

    score = scheduler.score_target(target)
    # Recently hunted should have low staleness
    assert score < 5.0


def test_high_priority_first():
    TargetDB.add("high.com", priority=10, bounty_max=10000)
    TargetDB.add("low.com", priority=1, bounty_max=100)

    scheduler = PriorityScheduler()
    batch = scheduler.get_next_batch()

    if len(batch) >= 2:
        assert batch[0]["domain"] == "high.com"
