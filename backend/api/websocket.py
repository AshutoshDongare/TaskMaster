"""
WebSocket — Real-time task event stream.

WS /ws/tasks/{task_id}

Events emitted:
  sub_task_started:    {sub_task_id, description, team}
  browser_action:      {action, element, url, screenshot}
  sub_task_completed:  {sub_task_id, verified, confidence}
  approval_required:   {approval_id, summary, screenshot, risk_level}
  approval_resolved:   {approval_id, decision, modification}
  sub_task_failed:     {sub_task_id, error, attempts, will_retry}
  task_completed:      {result, files, report_markdown}
  task_failed:         {error, last_sub_task, audit_trail_id}
"""

import json
import logging
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections per task."""

    def __init__(self):
        # task_id -> list of WebSocket connections
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, task_id: str):
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = []
        self.active_connections[task_id].append(websocket)
        logger.info(f"WebSocket connected for task {task_id}")

    def disconnect(self, websocket: WebSocket, task_id: str):
        if task_id in self.active_connections:
            self.active_connections[task_id].remove(websocket)
            if not self.active_connections[task_id]:
                del self.active_connections[task_id]
        logger.info(f"WebSocket disconnected for task {task_id}")

    async def send_event(self, task_id: str, event: dict):
        """Send event to all connected clients for a task."""
        if task_id in self.active_connections:
            disconnected = []
            for ws in self.active_connections[task_id]:
                try:
                    await ws.send_json(event)
                except Exception:
                    disconnected.append(ws)
            for ws in disconnected:
                self.disconnect(ws, task_id)


manager = ConnectionManager()


@router.websocket("/ws/tasks/{task_id}")
async def websocket_task_events(websocket: WebSocket, task_id: str):
    """
    Real-time task event stream via WebSocket.

    Subscribes to Redis pub/sub channel for the task and forwards
    all events to the connected client.

    On connect, immediately checks DB state so fast-completing tasks
    are never silently dropped due to the pub/sub race condition.
    """
    await manager.connect(websocket, task_id)

    try:
        # Get Redis + DB from app state
        state = websocket.app.state.app_state
        redis_client = state.redis.client
        db = state.db  # PostgresStore

        # ── Race-condition guard ─────────────────────────────────────
        # If the task already reached a terminal state before the WS
        # was established, the Redis pub/sub event fired into the void.
        # Replay it now so the UI never hangs.
        try:
            task_data = await db.get_task(task_id)
            if task_data:
                status = task_data.get("status", "")
                if status == "completed":
                    import json as _json
                    result = task_data.get("result", {})
                    if isinstance(result, str):
                        try:
                            result = _json.loads(result)
                        except Exception:
                            result = {"report_markdown": result}
                    await websocket.send_json({
                        "type": "task_completed",
                        "data": {"result": result},
                    })
                    return
                elif status == "failed":
                    import json as _json
                    result = task_data.get("result", {})
                    if isinstance(result, str):
                        try:
                            result = _json.loads(result)
                        except Exception:
                            result = {}
                    await websocket.send_json({
                        "type": "task_failed",
                        "data": {"error": result.get("error", "Task failed")},
                    })
                    return
                elif status == "cancelled":
                    await websocket.send_json({
                        "type": "task_failed",
                        "data": {"error": "Task was cancelled"},
                    })
                    return
        except Exception as e:
            logger.warning(f"Could not pre-check task state on WS connect: {e}")
        # ────────────────────────────────────────────────────────────

        # Subscribe to task events channel
        pubsub = redis_client.pubsub()
        channel = f"task_events:{task_id}"
        await pubsub.subscribe(channel)

        # Listen for events and forward to WebSocket
        async def listen_redis():
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        event = json.loads(message["data"])
                        await websocket.send_json(event)
            except Exception as e:
                logger.debug(f"Redis listener stopped: {e}")

        # Start Redis listener in background
        redis_task = asyncio.create_task(listen_redis())

        try:
            # Keep connection alive, listen for client messages
            while True:
                data = await websocket.receive_text()
                # Client can send ping/pong or other control messages
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            redis_task.cancel()
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    except Exception as e:
        logger.error(f"WebSocket error for task {task_id}: {e}")
    finally:
        manager.disconnect(websocket, task_id)

