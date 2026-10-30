"""
PostgreSQL Structured Store.

Task history, audit log, approval records, user preferences, and memory metadata.
Uses asyncpg for async database operations.
"""

import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

import asyncpg

from core.task_manifest import (
    TaskManifest, SubTask, ApprovalRequest, MemoryEntry,
    TaskStatus, RiskLevel
)

logger = logging.getLogger(__name__)


class PostgresStore:
    """
    Structured storage backed by PostgreSQL.

    Handles:
    - Task CRUD operations
    - Immutable audit log (append-only, enforced by DB trigger)
    - Approval request lifecycle
    - Memory metadata (complements ChromaDB vectors)
    - User preferences
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Create connection pool."""
        try:
            # Parse database_url robustly to handle passwords with '@'
            dsn = self.database_url
            if dsn.startswith("postgresql://"):
                dsn = dsn[len("postgresql://"):]
            elif dsn.startswith("postgres://"):
                dsn = dsn[len("postgres://"):]
                
            # Split on the last '@' to separate host from credentials
            creds, host_part = dsn.rsplit("@", 1)
            user, password = creds.split(":", 1)
            
            # Split host and database
            host_port, database = host_part.split("/", 1)
            if ":" in host_port:
                host, port = host_port.split(":", 1)
                port = int(port)
            else:
                host = host_port
                port = 5432
                
            self._pool = await asyncpg.create_pool(
                user=user,
                password=password,
                host=host,
                port=port,
                database=database,
                min_size=2,
                max_size=10,
            )
        except Exception as e:
            logger.warning(f"Robust parsing failed or not applicable, trying direct connect: {e}")
            # Fallback to direct DSN string
            self._pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
            )
        logger.info("PostgreSQL connection pool created")

    async def disconnect(self):
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("PostgreSQL connection pool closed")

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("PostgreSQL not connected. Call connect() first.")
        return self._pool

    # ── Task Operations ─────────────────────────────────────────────────────

    async def create_task(self, manifest: TaskManifest) -> str:
        """Insert a new task and return its ID."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tasks (id, raw_input, intent, risk_level, status,
                                   entities, constraints, ambiguities, sub_tasks)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                manifest.id,
                manifest.raw_input,
                manifest.intent.value,
                manifest.risk_level.value,
                manifest.status.value,
                json.dumps(manifest.entities),
                json.dumps(manifest.constraints),
                json.dumps(manifest.ambiguities),
                json.dumps([st.model_dump() for st in manifest.sub_tasks],
                           default=str),
            )
        logger.info(f"Created task {manifest.id}")
        return manifest.id

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task by ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id = $1", task_id
            )
        if row:
            return dict(row)
        return None

    async def update_task(self, task_id: str, **updates):
        """Update task fields."""
        if not updates:
            return

        set_clauses = []
        values = []
        for i, (key, value) in enumerate(updates.items(), 1):
            set_clauses.append(f"{key} = ${i + 1}")
            if isinstance(value, (dict, list)):
                values.append(json.dumps(value, default=str))
            elif hasattr(value, "value"):  # Enum
                values.append(value.value)
            else:
                values.append(value)

        query = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = $1"
        async with self.pool.acquire() as conn:
            await conn.execute(query, task_id, *values)
        logger.info(f"Updated task {task_id}: {list(updates.keys())}")

    async def update_sub_tasks(self, task_id: str, sub_tasks: List[SubTask]):
        """Update the sub_tasks JSONB field for a task."""
        data = json.dumps([st.model_dump() for st in sub_tasks], default=str)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET sub_tasks = $1 WHERE id = $2",
                data, task_id
            )

    async def list_tasks(
        self,
        status: Optional[str] = None,
        intent: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List tasks with optional filters."""
        conditions = []
        values = []
        param_idx = 1

        if status:
            conditions.append(f"status = ${param_idx}")
            values.append(status)
            param_idx += 1

        if intent:
            conditions.append(f"intent = ${param_idx}")
            values.append(intent)
            param_idx += 1

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = (f"SELECT * FROM tasks{where} "
                 f"ORDER BY created_at DESC "
                 f"LIMIT ${param_idx} OFFSET ${param_idx + 1}")
        values.extend([limit, offset])

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *values)
        return [dict(row) for row in rows]

    # ── Audit Log (Append Only) ─────────────────────────────────────────────

    async def append_audit_log(
        self,
        task_id: str,
        agent_name: str,
        action: str,
        risk_level: str = "low",
        sub_task_id: Optional[str] = None,
        action_detail: Optional[Dict] = None,
        result: Optional[str] = None,
        result_detail: Optional[Dict] = None,
        screenshot_path: Optional[str] = None,
        reasoning: Optional[str] = None,
    ) -> int:
        """
        Append an entry to the immutable audit log.
        Returns the log entry ID.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO audit_log
                    (task_id, sub_task_id, agent_name, action, action_detail,
                     risk_level, result, result_detail, screenshot_path, reasoning)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                task_id,
                sub_task_id,
                agent_name,
                action,
                json.dumps(action_detail or {}),
                risk_level,
                result,
                json.dumps(result_detail or {}),
                screenshot_path,
                reasoning,
            )
        log_id = row["id"]
        logger.debug(f"Audit log #{log_id}: [{agent_name}] {action} "
                     f"(risk={risk_level})")
        return log_id

    async def get_audit_log(
        self,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        risk_level: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query audit log with filters."""
        conditions = []
        values = []
        param_idx = 1

        if task_id:
            conditions.append(f"task_id = ${param_idx}")
            values.append(task_id)
            param_idx += 1

        if agent_name:
            conditions.append(f"agent_name = ${param_idx}")
            values.append(agent_name)
            param_idx += 1

        if risk_level:
            conditions.append(f"risk_level = ${param_idx}")
            values.append(risk_level)
            param_idx += 1

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = (f"SELECT * FROM audit_log{where} "
                 f"ORDER BY timestamp DESC "
                 f"LIMIT ${param_idx} OFFSET ${param_idx + 1}")
        values.extend([limit, offset])

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *values)
        return [dict(row) for row in rows]

    # ── Approval Requests ───────────────────────────────────────────────────

    async def save_approval_request(self, request: ApprovalRequest) -> str:
        """Save a new approval request."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO approvals
                    (id, task_id, sub_task_id, action, context, summary,
                     screenshot_path, risk_level, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                request.id,
                request.task_id,
                request.sub_task_id,
                request.action,
                json.dumps(request.context, default=str),
                request.summary,
                request.screenshot,
                request.risk_level.value,
                request.status,
            )
        logger.info(f"Saved approval request {request.id} for task {request.task_id}")
        return request.id

    async def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Get all pending approval requests."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM approvals
                   WHERE status = 'pending'
                   ORDER BY requested_at DESC"""
            )
        return [dict(row) for row in rows]

    async def resolve_approval(
        self,
        approval_id: str,
        status: str,
        resolved_by: str = "user",
        modification: Optional[str] = None,
    ):
        """Resolve an approval request (approve/reject/modify)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE approvals
                SET status = $1, resolved_at = NOW(), resolved_by = $2,
                    modification = $3
                WHERE id = $4
                """,
                status, resolved_by, modification, approval_id
            )
        logger.info(f"Approval {approval_id} resolved: {status}")

    async def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific approval request."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM approvals WHERE id = $1", approval_id
            )
        return dict(row) if row else None

    async def timeout_expired_approvals(self) -> List[str]:
        """Find and timeout expired approval requests. Returns list of task IDs."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE approvals
                SET status = 'timed_out', resolved_at = NOW(),
                    resolved_by = 'system_timeout'
                WHERE status = 'pending' AND timeout_at < NOW()
                RETURNING task_id
                """
            )
        task_ids = [str(row["task_id"]) for row in rows]
        if task_ids:
            logger.warning(f"Timed out approvals for tasks: {task_ids}")
        return task_ids

    # ── Memory Metadata ─────────────────────────────────────────────────────

    async def save_memory_metadata(self, entry: MemoryEntry, chroma_id: str):
        """Save memory metadata alongside ChromaDB vector."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memories
                    (chroma_id, site, memory_type, content, metadata,
                     usefulness_score, times_recalled)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                chroma_id,
                entry.site,
                entry.memory_type,
                entry.content,
                json.dumps(entry.metadata),
                entry.usefulness_score,
                entry.times_recalled,
            )

    # ── User Preferences ────────────────────────────────────────────────────

    async def get_preference(self, key: str) -> Optional[str]:
        """Get a user preference value."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM user_preferences WHERE key = $1", key
            )
        return row["value"] if row else None

    async def set_preference(self, key: str, value: str):
        """Set a user preference (upsert)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_preferences (key, value, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
                """,
                key, value
            )

    async def get_all_preferences(self) -> Dict[str, str]:
        """Get all user preferences as a dict."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value FROM user_preferences")
        return {row["key"]: row["value"] for row in rows}

    # ── Health Check ────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check PostgreSQL connectivity."""
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False
