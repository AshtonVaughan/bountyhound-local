"""Celery application configuration."""

import os
from celery import Celery

BROKER_URL = os.environ.get("BHL_REDIS_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.environ.get("BHL_REDIS_RESULT", "redis://localhost:6379/1")

app = Celery("bountyhound_local")
app.conf.update(
    broker_url=BROKER_URL,
    result_backend=RESULT_BACKEND,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "src.workers.recon.*": {"queue": "recon"},
        "src.workers.discovery.*": {"queue": "discovery"},
        "src.workers.exploit.*": {"queue": "exploit"},
        "src.workers.validator.*": {"queue": "validate"},
        "src.workers.reporter.*": {"queue": "report"},
        "src.workers.auth.*": {"queue": "auth"},
        "src.orchestrator.*": {"queue": "orchestrate"},
    },
    beat_schedule={
        "light-retest": {
            "task": "src.orchestrator.brain.schedule_light_retest",
            "schedule": 43200.0,
        },
        "full-retest": {
            "task": "src.orchestrator.brain.schedule_full_retest",
            "schedule": 604800.0,
        },
        "token-refresh": {
            "task": "src.workers.auth.check_all_token_expiry",
            "schedule": 600.0,
        },
        "health-check": {
            "task": "src.orchestrator.brain.health_check",
            "schedule": 300.0,
        },
    },
)

app.autodiscover_tasks([
    "src.workers.recon",
    "src.workers.discovery",
    "src.workers.exploit",
    "src.workers.validator",
    "src.workers.reporter",
    "src.workers.auth",
    "src.orchestrator.brain",
])
