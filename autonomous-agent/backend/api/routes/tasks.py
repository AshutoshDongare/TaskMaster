"""
Task API Routes.

POST /tasks        — Submit a new task (async execution via Celery)
GET  /tasks/{id}   — Get full TaskManifest including sub-tasks and results
GET  /tasks        — List tasks with optional filters
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from core.task_manifest import TaskManifest, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskCreateRequest(BaseModel):
    description: str
    email_context: Optional[str] = None


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    ambiguities: list = []


@router.post("", response_model=TaskCreateResponse)
async def create_task(request: Request, body: TaskCreateRequest):
    """
    Submit a new task. Execution is asynchronous via Celery.

    If ambiguities exist (e.g., missing credentials), they are returned
    for the user to answer before execution starts.
    """
    state = request.app.state.app_state

    # Run intake agent
    from agents.intake_agent import IntakeAgent
    intake = IntakeAgent(vault=state.vault, memory=state.memory)
    manifest = await intake.process(body.description, body.email_context)

    # Save task to database
    await state.db.create_task(manifest)

    # If there are ambiguities, return them without starting execution
    if manifest.ambiguities:
        return TaskCreateResponse(
            task_id=manifest.id,
            status="awaiting_clarification",
            ambiguities=manifest.ambiguities,
        )

    # Dispatch to Celery for async execution
    try:
        from workers.task_worker import execute_task_async
        execute_task_async.delay(manifest.id)
        logger.info(f"Task {manifest.id} dispatched to Celery")
    except Exception as e:
        logger.warning(f"Celery dispatch failed, running inline: {e}")
        # Fallback: run inline (for development without Celery)
        import asyncio
        asyncio.create_task(_run_task_inline(state, manifest))

    return TaskCreateResponse(
        task_id=manifest.id,
        status="pending",
        ambiguities=[],
    )


async def _run_task_inline(state, manifest: TaskManifest):
    """Fallback: run task inline when Celery is not available."""
    try:
        from agents.orchestrator_agent import OrchestratorAgent
        from agents.specialists.browser.browser_agent import BrowserSpecialist

        orchestrator = OrchestratorAgent(
            db=state.db,
            redis=state.redis,
            memory=state.memory,
            vault=state.vault,
            safety_gate=state.safety_gate,
            audit_logger=state.audit_logger,
        )

        # Register browser specialist
        browser = BrowserSpecialist(vault=state.vault)
        orchestrator.register_specialist("browser", browser)

        await orchestrator.execute_task(manifest)
    except Exception as e:
        logger.error(f"Inline task execution failed: {e}")
        await state.db.update_task(
            manifest.id,
            status=TaskStatus.FAILED.value,
            result=json.dumps({"error": str(e)}),
        )


@router.get("/{task_id}")
async def get_task(request: Request, task_id: str):
    """Get full TaskManifest including all sub-tasks, statuses, and results."""
    state = request.app.state.app_state

    task = await state.db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Parse JSONB fields
    for field in ["entities", "constraints", "ambiguities", "sub_tasks", "result"]:
        if task.get(field) and isinstance(task[field], str):
            try:
                task[field] = json.loads(task[field])
            except (json.JSONDecodeError, TypeError):
                pass

    return task


@router.get("")
async def list_tasks(
    request: Request,
    status: Optional[str] = None,
    intent: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List tasks with optional filters."""
    state = request.app.state.app_state

    tasks = await state.db.list_tasks(
        status=status,
        intent=intent,
        limit=limit,
        offset=offset,
    )

    # Parse JSONB fields
    for task in tasks:
        for field in ["entities", "constraints", "ambiguities", "sub_tasks", "result"]:
            if task.get(field) and isinstance(task[field], str):
                try:
                    task[field] = json.loads(task[field])
                except (json.JSONDecodeError, TypeError):
                    pass

    return {"tasks": tasks, "count": len(tasks), "limit": limit, "offset": offset}
