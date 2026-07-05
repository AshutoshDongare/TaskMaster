"""
Audit API Routes.

GET /audit/log — Full audit trail with filters
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/log")
async def get_audit_log(
    request: Request,
    task_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    risk_level: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """
    Query the immutable audit trail.

    Filters:
    - task_id: Filter by specific task
    - agent_name: Filter by agent (orchestrator, browser, verifier, etc.)
    - risk_level: Filter by risk level (low, medium, high)
    """
    state = request.app.state.app_state

    entries = await state.db.get_audit_log(
        task_id=task_id,
        agent_name=agent_name,
        risk_level=risk_level,
        limit=limit,
        offset=offset,
    )

    return {
        "entries": entries,
        "count": len(entries),
        "limit": limit,
        "offset": offset,
    }
