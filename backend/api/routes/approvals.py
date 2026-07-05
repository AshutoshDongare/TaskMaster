"""
Approval API Routes.

GET  /approvals/pending           — All pending approval requests
POST /approvals/{id}/approve      — Approve a pending action
POST /approvals/{id}/reject       — Reject a pending action
POST /approvals/{id}/modify       — Modify content then approve
"""

import logging
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class ModifyRequest(BaseModel):
    modification: str


@router.get("/pending")
async def get_pending_approvals(request: Request):
    """Get all tasks currently awaiting human decision."""
    state = request.app.state.app_state
    approvals = await state.db.get_pending_approvals()
    return {"approvals": approvals, "count": len(approvals)}


@router.get("/{approval_id}")
async def get_approval(request: Request, approval_id: str):
    """Get a specific approval request with full context."""
    state = request.app.state.app_state
    approval = await state.db.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404,
                            detail=f"Approval {approval_id} not found")
    return approval


@router.post("/{approval_id}/approve")
async def approve_action(request: Request, approval_id: str):
    """
    Approve the pending action. Task execution resumes immediately.
    """
    state = request.app.state.app_state

    approval = await state.db.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404,
                            detail=f"Approval {approval_id} not found")

    if approval.get("status") != "pending":
        raise HTTPException(status_code=400,
                            detail=f"Approval is already {approval.get('status')}")

    # Update database
    await state.db.resolve_approval(approval_id, "approved")

    # Notify via Redis pub/sub — unblocks the waiting SafetyGate
    await state.redis.set_approval_status(
        approval_id,
        status="approved",
    )

    # Log to audit
    await state.audit_logger.log(
        action="user_approved",
        context={"approval_id": approval_id},
        risk_level="high",
        task_id=str(approval.get("task_id", "")),
        agent_name="user",
        result="approved",
    )

    return {"status": "approved", "approval_id": approval_id}


@router.post("/{approval_id}/reject")
async def reject_action(request: Request, approval_id: str):
    """
    Reject the pending action. Task is cancelled.
    """
    state = request.app.state.app_state

    approval = await state.db.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404,
                            detail=f"Approval {approval_id} not found")

    if approval.get("status") != "pending":
        raise HTTPException(status_code=400,
                            detail=f"Approval is already {approval.get('status')}")

    await state.db.resolve_approval(approval_id, "rejected")

    await state.redis.set_approval_status(
        approval_id,
        status="rejected",
    )

    await state.audit_logger.log(
        action="user_rejected",
        context={"approval_id": approval_id},
        risk_level="high",
        task_id=str(approval.get("task_id", "")),
        agent_name="user",
        result="rejected",
    )

    return {"status": "rejected", "approval_id": approval_id}


@router.post("/{approval_id}/modify")
async def modify_and_approve(
    request: Request,
    approval_id: str,
    body: ModifyRequest,
):
    """
    Modify the content then approve (e.g., edit email draft before sending).
    """
    state = request.app.state.app_state

    approval = await state.db.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404,
                            detail=f"Approval {approval_id} not found")

    if approval.get("status") != "pending":
        raise HTTPException(status_code=400,
                            detail=f"Approval is already {approval.get('status')}")

    await state.db.resolve_approval(
        approval_id,
        "modified",
        modification=body.modification,
    )

    await state.redis.set_approval_status(
        approval_id,
        status="approved",
        modification=body.modification,
    )

    await state.audit_logger.log(
        action="user_modified_and_approved",
        context={"approval_id": approval_id,
                  "modification_length": len(body.modification)},
        risk_level="high",
        task_id=str(approval.get("task_id", "")),
        agent_name="user",
        result="modified",
    )

    return {
        "status": "modified",
        "approval_id": approval_id,
        "modification": body.modification,
    }
