"""
Audit Logger — Append-only action audit log.

Every significant action, LLM decision, and approval is logged with
timestamp, reasoning, and evidence. The PostgreSQL table has a trigger
that prevents UPDATE and DELETE — this is the application-level complement.

NEVER log credential values. Use _sanitize() before any write.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime

from core.memory.postgres_store import PostgresStore
from core.task_manifest import RiskLevel

logger = logging.getLogger(__name__)

# Keys that must be redacted from all logged context
FORBIDDEN_KEYS = {"password", "secret", "token", "api_key", "credential",
                  "auth", "key", "passwd"}


class AuditLogger:
    """
    Append-only audit logger backed by PostgreSQL.

    Every action is logged with:
    - task_id, sub_task_id: task context
    - agent_name: which agent took the action
    - action: what was done
    - action_detail: structured details (sanitized)
    - risk_level: LOW, MEDIUM, HIGH
    - result: success, failure, blocked, approved, rejected
    - result_detail: structured result (sanitized)
    - screenshot_path: evidence file path
    - reasoning: LLM's stated reasoning
    - timestamp: auto-generated
    """

    def __init__(self, db: PostgresStore):
        self.db = db

    async def log(
        self,
        action: str,
        context: Dict[str, Any],
        risk_level: str | RiskLevel,
        task_id: str,
        agent_name: str = "system",
        sub_task_id: Optional[str] = None,
        result: Optional[str] = None,
        result_detail: Optional[Dict] = None,
        screenshot_path: Optional[str] = None,
        reasoning: Optional[str] = None,
    ) -> int:
        """
        Append an audit entry. Returns the log entry ID.

        All context and result_detail dicts are sanitized to remove
        any credential values before storage.
        """
        risk_str = risk_level.value if isinstance(risk_level, RiskLevel) else risk_level
        sanitized_context = self._sanitize(context)
        sanitized_result = self._sanitize(result_detail) if result_detail else None

        log_id = await self.db.append_audit_log(
            task_id=task_id,
            agent_name=agent_name,
            action=action,
            risk_level=risk_str,
            sub_task_id=sub_task_id,
            action_detail=sanitized_context,
            result=result,
            result_detail=sanitized_result,
            screenshot_path=screenshot_path,
            reasoning=reasoning,
        )

        logger.info(
            f"Audit #{log_id} | [{agent_name}] {action} | "
            f"risk={risk_str} result={result or 'pending'} | "
            f"task={task_id[:8]}..."
        )
        return log_id

    async def log_decision(
        self,
        task_id: str,
        agent_name: str,
        decision: str,
        reasoning: str,
        risk_level: str = "low",
        sub_task_id: Optional[str] = None,
    ) -> int:
        """Log an LLM decision with reasoning."""
        return await self.log(
            action=f"decision:{decision}",
            context={"decision": decision},
            risk_level=risk_level,
            task_id=task_id,
            agent_name=agent_name,
            sub_task_id=sub_task_id,
            result="logged",
            reasoning=reasoning,
        )

    async def log_error(
        self,
        task_id: str,
        agent_name: str,
        action: str,
        error: str,
        sub_task_id: Optional[str] = None,
    ) -> int:
        """Log an error."""
        return await self.log(
            action=action,
            context={"error": error},
            risk_level="medium",
            task_id=task_id,
            agent_name=agent_name,
            sub_task_id=sub_task_id,
            result="failure",
            result_detail={"error": error},
        )

    def _sanitize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove any credential values before storing in database.
        Recursively sanitizes nested dicts.
        """
        if not isinstance(data, dict):
            return data

        sanitized = {}
        for k, v in data.items():
            if any(forbidden in k.lower() for forbidden in FORBIDDEN_KEYS):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = self._sanitize(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    self._sanitize(item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized
