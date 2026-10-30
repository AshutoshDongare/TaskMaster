"""
Chainlit UI Entry Point — Chat interface for the Autonomous Agent System.

Features:
- Task submission via natural language chat
- Real-time task progress display
- Approval cards with screenshot display and inline editing
- Task history and audit trail viewing
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path

# Load .env from project root (two levels up from frontend/)
from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=False)  # won't override vars already set by Docker

import chainlit as cl
import httpx

logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
logger.info(f"🔗 Chainlit UI connecting to backend: {BACKEND_URL}")


@cl.on_chat_start
async def on_chat_start():
    """Welcome message when user opens the chat."""
    await cl.Message(
        content=(
            "# 🤖 Autonomous Agent System\n\n"
            "Welcome! I can execute tasks autonomously on your behalf.\n\n"
            "**What I can do:**\n"
            "- 🌐 Browse websites, log in, and take actions\n"
            "- 📄 Download and process documents\n"
            "- 📧 Read emails, research topics, and draft replies\n"
            "- 📊 Aggregate data from multiple portals\n\n"
            "**Safety:** I'll always ask for your approval before:\n"
            "- Making payments\n"
            "- Sending emails\n"
            "- Submitting forms\n\n"
            "**Try:** *\"Go to httpbin.org/get and tell me my IP address\"*\n\n"
            "---\n"
            "Type your task below to get started."
        ),
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle user messages — submit as tasks."""
    user_input = message.content.strip()

    if not user_input:
        await cl.Message(content="Please describe a task for me to execute.").send()
        return

    # Check for special commands
    if user_input.lower().startswith("/status"):
        await show_task_status(user_input)
        return
    elif user_input.lower().startswith("/credentials"):
        await show_credentials()
        return
    elif user_input.lower().startswith("/history"):
        await show_task_history()
        return

    # Submit task to backend
    thinking_msg = cl.Message(content="🔄 Analyzing your task...")
    await thinking_msg.send()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{BACKEND_URL}/tasks",
                json={"description": user_input},
            )

        # Surface non-2xx responses with body detail
        if response.status_code >= 400:
            try:
                err_body = response.json()
            except Exception:
                err_body = response.text
            await cl.Message(
                content=(
                    f"❌ **Backend error {response.status_code}**\n\n"
                    f"```\n{err_body}\n```"
                ),
            ).send()
            return

        result = response.json()
        task_id = result.get("task_id")
        status = result.get("status")
        ambiguities = result.get("ambiguities", [])

        if ambiguities:
            # Task needs clarification
            amb_text = "\n".join(f"- {a}" for a in ambiguities)
            await cl.Message(
                content=(
                    f"⚠️ **Clarification Needed**\n\n"
                    f"Before I can proceed, I need answers to:\n\n"
                    f"{amb_text}\n\n"
                    f"Please provide the missing information and resubmit."
                ),
            ).send()
        else:
            # Task accepted — start monitoring
            await cl.Message(
                content=(
                    f"✅ **Task Accepted** — ID: `{task_id[:8]}...`\n\n"
                    f"I'm working on: *{user_input}*\n\n"
                    f"I'll update you on progress and ask for approval "
                    f"if needed for any irreversible actions."
                ),
            ).send()

            # Start monitoring task progress
            asyncio.create_task(monitor_task(task_id))

    except httpx.ConnectError as e:
        logger.error(f"ConnectError to {BACKEND_URL}: {e}")
        await cl.Message(
            content=(
                f"❌ **Cannot connect to backend at `{BACKEND_URL}`**\n\n"
                "Make sure the backend is running.\n"
                "Start it with: `uvicorn main:app --reload`"
            ),
        ).send()
    except httpx.TimeoutException as e:
        logger.error(f"Timeout posting task: {e}")
        await cl.Message(
            content=(
                "❌ **Request timed out** — the backend took too long to respond.\n\n"
                f"Backend URL: `{BACKEND_URL}`"
            ),
        ).send()
    except Exception as e:
        logger.exception(f"Unexpected error submitting task: {e}")
        await cl.Message(
            content=(
                f"❌ **Unexpected error submitting task**\n\n"
                f"```\n{type(e).__name__}: {e}\n```\n\n"
                f"Backend URL: `{BACKEND_URL}`"
            )
        ).send()


async def monitor_task(task_id: str):
    """Monitor task progress via WebSocket and polling fallback."""
    ws_url = BACKEND_URL.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/ws/tasks/{task_id}"

    try:
        import websockets
        async with websockets.connect(ws_url) as ws:
            async for message in ws:
                event = json.loads(message)
                await handle_task_event(task_id, event)
    except Exception as e:
        logger.warning(f"WebSocket monitoring failed: {e}, falling back to polling")
        await poll_task_status(task_id)


async def poll_task_status(task_id: str):
    """Fallback: poll task status via REST API."""
    terminal_states = {"completed", "failed", "cancelled"}

    for _ in range(180):  # Max 30 minutes of polling
        await asyncio.sleep(10)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{BACKEND_URL}/tasks/{task_id}")
                if response.status_code != 200:
                    continue
                task = response.json()

            status = task.get("status", "")

            if status in terminal_states:
                if status == "completed":
                    result = task.get("result", {})
                    if isinstance(result, str):
                        result = json.loads(result)
                    report = result.get("report_markdown", "Task completed.")
                    await cl.Message(content=f"✅ **Task Complete**\n\n{report}").send()
                elif status == "failed":
                    result = task.get("result", {})
                    if isinstance(result, str):
                        result = json.loads(result)
                    error = result.get("error", "Unknown error")
                    await cl.Message(
                        content=f"❌ **Task Failed**\n\n{error}"
                    ).send()
                elif status == "cancelled":
                    await cl.Message(
                        content="⏹️ **Task Cancelled**"
                    ).send()
                break

            # Check for pending approvals
            if status == "awaiting_approval":
                await check_pending_approvals(task_id)

        except Exception as e:
            logger.debug(f"Poll error: {e}")
            continue


async def handle_task_event(task_id: str, event: dict):
    """Handle a real-time task event from WebSocket."""
    event_type = event.get("type", "")
    data = event.get("data", {})

    if event_type == "sub_task_started":
        await cl.Message(
            content=(
                f"▶️ **Sub-task started:** {data.get('description', '')}\n"
                f"Team: `{data.get('team', '')}`"
            ),
        ).send()

    elif event_type == "sub_task_completed":
        confidence = data.get("confidence", 0)
        emoji = "✅" if confidence > 0.9 else "☑️"
        await cl.Message(
            content=(
                f"{emoji} **Sub-task completed** "
                f"(confidence: {confidence:.0%})"
            ),
        ).send()

    elif event_type == "sub_task_failed":
        will_retry = data.get("will_retry", False)
        retry_text = " — will retry" if will_retry else " — max retries exceeded"
        await cl.Message(
            content=(
                f"⚠️ **Sub-task failed:** {data.get('error', 'unknown')}"
                f"{retry_text}"
            ),
        ).send()

    elif event_type == "approval_required":
        await show_approval_request(data)

    elif event_type == "task_completed":
        result = data.get("result", {})
        report = result.get("report_markdown", "Task completed successfully.")
        await cl.Message(content=f"✅ **Task Complete**\n\n{report}").send()

    elif event_type == "task_failed":
        error = data.get("error", "Unknown error")
        await cl.Message(content=f"❌ **Task Failed**\n\n{error}").send()


async def show_approval_request(data: dict):
    """Display an approval request with action buttons."""
    approval_id = data.get("approval_id", "")
    summary = data.get("summary", "Action requires approval")
    risk_level = data.get("risk_level", "high")

    content = (
        f"🛡️ **APPROVAL REQUIRED** (Risk: {risk_level.upper()})\n\n"
        f"{summary}\n\n"
        f"---\n\n"
    )

    # Include screenshot if available
    screenshot = data.get("screenshot")
    if screenshot and not screenshot.startswith("data:"):
        content += f"📸 Screenshot saved: `{screenshot}`\n\n"

    content += (
        f"**Actions:**\n"
        f"- Reply `approve {approval_id[:8]}` to approve\n"
        f"- Reply `reject {approval_id[:8]}` to reject\n"
        f"- Reply `modify {approval_id[:8]} <your changes>` to modify and approve"
    )

    await cl.Message(content=content).send()

    # Store approval ID for easy reference
    cl.user_session.set("pending_approval", approval_id)


async def check_pending_approvals(task_id: str):
    """Check for pending approvals and display them."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{BACKEND_URL}/approvals/pending")
            if response.status_code == 200:
                result = response.json()
                for approval in result.get("approvals", []):
                    if str(approval.get("task_id", "")) == task_id:
                        await show_approval_request({
                            "approval_id": str(approval["id"]),
                            "summary": approval.get("summary", ""),
                            "risk_level": approval.get("risk_level", "high"),
                            "screenshot": approval.get("screenshot_path"),
                        })
    except Exception as e:
        logger.debug(f"Approval check error: {e}")


async def show_credentials():
    """Show stored credential sites."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{BACKEND_URL}/credentials")
            if response.status_code == 200:
                result = response.json()
                sites = result.get("sites", [])
                if sites:
                    site_list = "\n".join(f"- `{s}`" for s in sites)
                    await cl.Message(
                        content=f"🔐 **Stored Credentials:**\n\n{site_list}"
                    ).send()
                else:
                    await cl.Message(
                        content="No credentials stored. Add via API: "
                                "`POST /credentials`"
                    ).send()
    except Exception as e:
        await cl.Message(content=f"❌ Error: {e}").send()


async def show_task_history():
    """Show recent task history."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{BACKEND_URL}/tasks", params={"limit": 10}
            )
            if response.status_code == 200:
                result = response.json()
                tasks = result.get("tasks", [])
                if tasks:
                    lines = ["📋 **Recent Tasks:**\n"]
                    for t in tasks:
                        status_emoji = {
                            "completed": "✅", "failed": "❌",
                            "executing": "🔄", "pending": "⏳",
                            "cancelled": "⏹️",
                        }.get(t.get("status", ""), "❓")
                        lines.append(
                            f"- {status_emoji} `{str(t['id'])[:8]}` — "
                            f"{t.get('raw_input', '')[:60]}..."
                        )
                    await cl.Message(content="\n".join(lines)).send()
                else:
                    await cl.Message(content="No tasks found.").send()
    except Exception as e:
        await cl.Message(content=f"❌ Error: {e}").send()


async def show_task_status(command: str):
    """Show status of a specific task."""
    parts = command.strip().split()
    if len(parts) < 2:
        await cl.Message(
            content="Usage: `/status <task_id>`"
        ).send()
        return

    task_id = parts[1]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{BACKEND_URL}/tasks/{task_id}")
            if response.status_code == 200:
                task = response.json()
                sub_tasks = task.get("sub_tasks", [])
                if isinstance(sub_tasks, str):
                    sub_tasks = json.loads(sub_tasks)

                lines = [
                    f"📊 **Task Status:** `{task_id[:8]}...`\n",
                    f"- **Input:** {task.get('raw_input', '')}",
                    f"- **Status:** {task.get('status', 'unknown')}",
                    f"- **Intent:** {task.get('intent', '')}",
                    f"- **Risk:** {task.get('risk_level', '')}",
                    "",
                ]

                if sub_tasks:
                    lines.append("**Sub-tasks:**")
                    for st in sub_tasks:
                        status_emoji = {
                            "completed": "✅", "failed": "❌",
                            "executing": "🔄", "pending": "⏳",
                        }.get(st.get("status", ""), "❓")
                        lines.append(
                            f"  {status_emoji} `{st.get('id', '')}` — "
                            f"{st.get('description', '')[:50]}..."
                        )

                await cl.Message(content="\n".join(lines)).send()
            elif response.status_code == 404:
                await cl.Message(content=f"Task `{task_id}` not found.").send()
    except Exception as e:
        await cl.Message(content=f"❌ Error: {e}").send()
