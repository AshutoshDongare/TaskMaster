"""
Approval Card Component — Approval UI with screenshot + edit capability.
"""

import chainlit as cl
from typing import Optional, Dict, Any


async def create_approval_card(
    approval_id: str,
    summary: str,
    risk_level: str = "high",
    screenshot_path: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> cl.Message:
    """
    Create and send an approval card with action buttons.

    Args:
        approval_id: The approval request ID
        summary: Plain English summary of the action
        risk_level: Risk level (high)
        screenshot_path: Path to screenshot file
        context: Additional context dict

    Returns:
        The sent Chainlit Message
    """
    elements = []

    # Add screenshot if available
    if screenshot_path:
        try:
            elements.append(
                cl.Image(
                    name="action_screenshot",
                    path=screenshot_path,
                    display="inline",
                )
            )
        except Exception:
            pass

    # Build card content
    content = (
        f"## 🛡️ Action Requires Approval\n\n"
        f"**Risk Level:** `{risk_level.upper()}`\n\n"
        f"**Action:** {summary}\n\n"
    )

    if context:
        content += "**Details:**\n"
        for key, value in context.items():
            if key not in ("screenshot", "password", "secret", "token"):
                content += f"- {key}: {value}\n"
        content += "\n"

    content += (
        f"---\n\n"
        f"**To respond:**\n"
        f"- `approve` — Proceed with this action\n"
        f"- `reject` — Cancel this action\n"
        f"- `modify <changes>` — Edit and approve\n"
    )

    msg = cl.Message(content=content, elements=elements)
    await msg.send()

    return msg
