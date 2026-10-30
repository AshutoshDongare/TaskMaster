"""
Celery Application Configuration.

Task queue backed by Redis for async task execution, retries,
and parallel sub-agent spawning.
"""

import os
import sys

# Ensure /app is on the path so forked Celery workers can import agents/core/etc.
_app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

celery_app = Celery(
    "autonomous_agent",
    broker=f"{redis_url}/0",
    backend=f"{redis_url}/1",
    include=["workers.task_worker"],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    # Concurrency
    worker_concurrency=4,

    # Task time limits
    task_soft_time_limit=1800,   # 30 minutes soft limit
    task_time_limit=3600,        # 60 minutes hard limit

    # Result expiry
    result_expires=86400,        # 24 hours

    # Beat schedule (Phase 4: recurring tasks)
    beat_schedule={
        "timeout-expired-approvals": {
            "task": "workers.task_worker.check_approval_timeouts",
            "schedule": 60.0,  # Every minute
        },
    },
)
