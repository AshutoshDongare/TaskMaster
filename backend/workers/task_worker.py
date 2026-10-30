"""
Task Worker — Async task execution entry point.

Called by Celery when a new task is submitted. Runs the full
Intake → Orchestrator → Output pipeline asynchronously.
"""

import os
import json
import asyncio
import logging
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from workers.celery_app import celery_app
from core.task_manifest import TaskManifest, TaskStatus

logger = logging.getLogger(__name__)


def get_event_loop():
    """Get or create an event loop for async execution in Celery."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Loop is closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


@celery_app.task(
    name="workers.task_worker.execute_task_async",
    bind=True,
    max_retries=0,  # Retries are managed by the Orchestrator, not Celery
    acks_late=True,
)
def execute_task_async(self, task_id: str):
    """
    Execute a task asynchronously.

    This is the Celery entry point. It:
    1. Loads the task from the database
    2. Sets up all service connections
    3. Runs the Orchestrator agent
    4. Updates the task with results
    """
    loop = get_event_loop()
    loop.run_until_complete(_execute_task(task_id))


async def _execute_task(task_id: str):
    """Async task execution logic."""
    from core.memory.postgres_store import PostgresStore
    from core.memory.redis_store import RedisStore
    from core.memory.chroma_store import LongTermMemory
    from core.memory.credential_vault import CredentialVault
    from core.safety.audit_logger import AuditLogger
    from core.safety.safety_gate import SafetyGate
    from agents.orchestrator_agent import OrchestratorAgent
    from agents.specialists.browser.browser_agent import BrowserSpecialist
    from agents.specialists.document.doc_agent import DocumentSpecialist
    from agents.specialists.email.email_agent import EmailSpecialist
    from agents.specialists.research.research_agent import ResearchSpecialist

    # Initialize services
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://agent:agent@localhost:5432/agentdb"
    )
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    chroma_url = os.environ.get("CHROMA_URL", "http://localhost:8001")

    db = PostgresStore(db_url)
    redis = RedisStore(redis_url)

    await db.connect()
    await redis.connect()

    memory = LongTermMemory(chroma_url)
    try:
        memory.connect()
    except Exception as e:
        logger.warning(f"ChromaDB connection failed: {e}")

    try:
        vault = CredentialVault()
    except Exception as e:
        logger.warning(f"Vault init failed: {e}")
        vault = None

    audit_logger = AuditLogger(db=db)
    safety_gate = SafetyGate(
        db=db, redis=redis, audit_logger=audit_logger
    )

    try:
        # Load task from database
        task_data = await db.get_task(task_id)
        if not task_data:
            logger.error(f"Task {task_id} not found in database")
            return

        # Reconstruct TaskManifest
        from core.task_manifest import TaskIntent, RiskLevel

        def _parse_json_field(value, default):
            """Parse a field that may be stored as a JSON string in the DB."""
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return default
            return value if value is not None else default

        manifest = TaskManifest(
            id=str(task_data["id"]),
            raw_input=task_data["raw_input"],
            intent=TaskIntent(task_data.get("intent", "generic")),
            risk_level=RiskLevel(task_data.get("risk_level", "medium")),
            entities=_parse_json_field(task_data.get("entities"), {}),
            constraints=_parse_json_field(task_data.get("constraints"), []),
            ambiguities=_parse_json_field(task_data.get("ambiguities"), []),
            status=TaskStatus(task_data.get("status", "pending")),
        )

        # Parse sub_tasks if they exist
        if task_data.get("sub_tasks"):
            sub_tasks_data = task_data["sub_tasks"]
            if isinstance(sub_tasks_data, str):
                sub_tasks_data = json.loads(sub_tasks_data)
            from core.task_manifest import SubTask
            manifest.sub_tasks = [SubTask(**st) for st in sub_tasks_data]

        # Create orchestrator
        orchestrator = OrchestratorAgent(
            db=db,
            redis=redis,
            memory=memory,
            vault=vault,
            safety_gate=safety_gate,
            audit_logger=audit_logger,
        )

        # Register specialists
        orchestrator.register_specialist("browser",
                                          BrowserSpecialist(vault=vault))
        orchestrator.register_specialist("document",
                                          DocumentSpecialist())
        orchestrator.register_specialist("email",
                                          EmailSpecialist())
        orchestrator.register_specialist("research",
                                          ResearchSpecialist())

        # Execute!
        logger.info(f"Starting task execution: {task_id}")
        result = await orchestrator.execute_task(manifest)
        logger.info(f"Task {task_id} completed with status: {result.status.value}")

    except Exception as e:
        logger.exception(f"Task {task_id} execution failed: {e}")
        await db.update_task(
            task_id,
            status=TaskStatus.FAILED.value,
            result=json.dumps({"error": str(e)}),
        )
    finally:
        await redis.disconnect()
        await db.disconnect()


@celery_app.task(name="workers.task_worker.check_approval_timeouts")
def check_approval_timeouts():
    """
    Periodic task: check for expired approval requests and cancel them.
    Runs every minute via Celery Beat.
    """
    loop = get_event_loop()
    loop.run_until_complete(_check_timeouts())


async def _check_timeouts():
    """Check and timeout expired approvals."""
    from core.memory.postgres_store import PostgresStore

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://agent:agent@localhost:5432/agentdb"
    )
    db = PostgresStore(db_url)
    await db.connect()

    try:
        timed_out_tasks = await db.timeout_expired_approvals()
        for task_id in timed_out_tasks:
            await db.update_task(
                task_id,
                status=TaskStatus.CANCELLED.value,
                result=json.dumps({
                    "error": "Approval timed out — no response within 30 minutes"
                }),
            )
            logger.warning(f"Task {task_id} cancelled due to approval timeout")
    except Exception as e:
        logger.error(f"Approval timeout check failed: {e}")
    finally:
        await db.disconnect()
