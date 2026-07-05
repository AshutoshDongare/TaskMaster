"""
Redis Short-Term Memory Store.

Manages current task state, session context, and agent working memory.
Data is transient — TTL-based expiry for session data.
"""

import json
import logging
from typing import Optional, Any, Dict
from datetime import timedelta

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class RedisStore:
    """
    Short-term memory backed by Redis.

    Stores:
    - Task state (current TaskManifest serialized as JSON)
    - Session context (browser session IDs, intermediate results)
    - Agent working memory (current step, observations, thoughts)
    - Approval request status polling
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self._client: Optional[aioredis.Redis] = None

    async def connect(self):
        """Initialize Redis connection."""
        self._client = aioredis.from_url(
            self.redis_url,
            decode_responses=True,
            encoding="utf-8",
        )
        logger.info(f"Connected to Redis at {self.redis_url}")

    async def disconnect(self):
        """Close Redis connection."""
        if self._client:
            await self._client.close()
            logger.info("Redis connection closed")

    @property
    def client(self) -> aioredis.Redis:
        if not self._client:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    # ── Task State ──────────────────────────────────────────────────────────

    async def store_task_state(self, task_id: str, state: Dict[str, Any],
                               ttl: int = 86400):
        """Store task state with 24-hour default TTL."""
        key = f"task:{task_id}:state"
        await self.client.setex(key, ttl, json.dumps(state, default=str))

    async def get_task_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve task state."""
        key = f"task:{task_id}:state"
        data = await self.client.get(key)
        return json.loads(data) if data else None

    async def delete_task_state(self, task_id: str):
        """Remove task state (after completion)."""
        key = f"task:{task_id}:state"
        await self.client.delete(key)

    # ── Session Context ─────────────────────────────────────────────────────

    async def store_session(self, task_id: str, key: str, value: Any,
                            ttl: int = 3600):
        """Store session data (e.g., browser session ID) with 1-hour TTL."""
        redis_key = f"task:{task_id}:session:{key}"
        await self.client.setex(redis_key, ttl, json.dumps(value, default=str))

    async def get_session(self, task_id: str, key: str) -> Optional[Any]:
        """Retrieve session data."""
        redis_key = f"task:{task_id}:session:{key}"
        data = await self.client.get(redis_key)
        return json.loads(data) if data else None

    # ── Agent Working Memory ────────────────────────────────────────────────

    async def push_thought(self, task_id: str, sub_task_id: str,
                           thought: Dict[str, Any]):
        """Append a thought/observation to the agent's working memory."""
        key = f"task:{task_id}:subtask:{sub_task_id}:thoughts"
        await self.client.rpush(key, json.dumps(thought, default=str))
        # Keep only last 50 thoughts to prevent unbounded growth
        await self.client.ltrim(key, -50, -1)

    async def get_thoughts(self, task_id: str, sub_task_id: str,
                           last_n: int = 10) -> list:
        """Get recent thoughts from agent working memory."""
        key = f"task:{task_id}:subtask:{sub_task_id}:thoughts"
        items = await self.client.lrange(key, -last_n, -1)
        return [json.loads(item) for item in items]

    # ── Approval Polling ────────────────────────────────────────────────────

    async def set_approval_status(self, approval_id: str, status: str,
                                  modification: Optional[str] = None):
        """Set approval decision (called by API when user responds)."""
        key = f"approval:{approval_id}"
        data = {"status": status, "modification": modification}
        await self.client.set(key, json.dumps(data))
        # Also publish to channel for real-time notification
        await self.client.publish(f"approval_channel:{approval_id}",
                                  json.dumps(data))

    async def get_approval_status(self, approval_id: str) -> Optional[Dict]:
        """Check approval status."""
        key = f"approval:{approval_id}"
        data = await self.client.get(key)
        return json.loads(data) if data else None

    async def wait_for_approval(self, approval_id: str,
                                timeout_seconds: int = 1800) -> Optional[Dict]:
        """
        Block until approval decision arrives via pub/sub.

        Args:
            approval_id: The approval request ID
            timeout_seconds: Max wait time (default 30 minutes)

        Returns:
            Dict with {"status": "approved"|"rejected"|"modified", "modification": ...}
            or None on timeout
        """
        # First check if already resolved
        existing = await self.get_approval_status(approval_id)
        if existing and existing.get("status") in ("approved", "rejected", "modified"):
            return existing

        # Subscribe and wait
        pubsub = self.client.pubsub()
        channel = f"approval_channel:{approval_id}"
        await pubsub.subscribe(channel)

        try:
            # Wait for message with timeout
            import asyncio
            end_time = asyncio.get_event_loop().time() + timeout_seconds

            async for message in pubsub.listen():
                if message["type"] == "message":
                    return json.loads(message["data"])

                # Check timeout
                if asyncio.get_event_loop().time() > end_time:
                    logger.warning(f"Approval {approval_id} timed out after "
                                   f"{timeout_seconds}s")
                    return None
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

        return None

    # ── WebSocket Event Broadcast ───────────────────────────────────────────

    async def publish_event(self, task_id: str, event_type: str,
                            data: Dict[str, Any]):
        """Publish task event for WebSocket consumers."""
        channel = f"task_events:{task_id}"
        event = {"type": event_type, "data": data}
        await self.client.publish(channel, json.dumps(event, default=str))

    # ── Health Check ────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check Redis connectivity."""
        try:
            await self.client.ping()
            return True
        except Exception:
            return False
