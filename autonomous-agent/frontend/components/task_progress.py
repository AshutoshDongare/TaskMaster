"""
Task Progress Component — Real-time task timeline display.
"""

import chainlit as cl
from typing import List, Dict, Any


async def create_progress_timeline(
    task_id: str,
    sub_tasks: List[Dict[str, Any]],
) -> cl.Message:
    """
    Create a visual timeline of task progress.

    Args:
        task_id: The task ID
        sub_tasks: List of sub-task dicts with status info

    Returns:
        The sent Chainlit Message
    """
    status_emoji = {
        "completed": "✅",
        "failed": "❌",
        "executing": "🔄",
        "pending": "⏳",
        "retrying": "🔁",
        "awaiting_approval": "🛡️",
        "cancelled": "⏹️",
    }

    lines = [f"## 📊 Task Progress — `{task_id[:8]}...`\n"]

    for i, st in enumerate(sub_tasks, 1):
        status = st.get("status", "pending")
        emoji = status_emoji.get(status, "❓")
        team = st.get("specialist_team", "")
        risk = st.get("risk_level", "low")

        line = f"{emoji} **{i}.** {st.get('description', 'Unknown')}"
        line += f" — `{team}` (risk: {risk})"

        if st.get("error"):
            line += f"\n   ⚠️ Error: {st['error'][:80]}"

        if st.get("attempts", 0) > 1:
            line += f" (attempt {st['attempts']})"

        lines.append(line)

    # Summary
    completed = sum(1 for st in sub_tasks if st.get("status") == "completed")
    total = len(sub_tasks)
    lines.append(f"\n**Progress:** {completed}/{total} sub-tasks completed")

    msg = cl.Message(content="\n".join(lines))
    await msg.send()

    return msg


async def update_progress(
    task_id: str,
    event_type: str,
    data: Dict[str, Any],
) -> cl.Message:
    """
    Send a progress update message.

    Args:
        task_id: The task ID
        event_type: Type of event
        data: Event data

    Returns:
        The sent Chainlit Message
    """
    messages = {
        "sub_task_started": (
            f"▶️ Starting: {data.get('description', '')}\n"
            f"   Team: `{data.get('team', '')}` | "
            f"Attempt: {data.get('attempt', 1)}"
        ),
        "sub_task_completed": (
            f"✅ Completed (confidence: "
            f"{data.get('confidence', 0):.0%})"
        ),
        "sub_task_failed": (
            f"{'🔁' if data.get('will_retry') else '❌'} "
            f"{'Retrying' if data.get('will_retry') else 'Failed'}: "
            f"{data.get('error', 'unknown')}"
        ),
        "browser_action": (
            f"🌐 Browser: {data.get('action', '')} on "
            f"{data.get('url', '')}"
        ),
        "planning_started": "📋 Planning sub-tasks...",
        "task_started": (
            f"🚀 Task started (intent: {data.get('intent', '')}, "
            f"risk: {data.get('risk_level', '')})"
        ),
    }

    content = messages.get(event_type, f"📌 {event_type}: {data}")

    msg = cl.Message(content=content)
    await msg.send()

    return msg
