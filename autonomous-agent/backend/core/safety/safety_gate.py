"""
Safety Gate — Every agent action passes through here before execution.

LOW / MEDIUM risk: log and pass through automatically.
HIGH risk: block, generate approval request, wait for user decision.

This is the single enforcement point for human-in-the-loop safety.
No HIGH risk action can bypass this gate.
"""

import os
import logging
from typing import Dict, Any, Tuple, Optional
from datetime import datetime

import google.generativeai as genai

from core.task_manifest import (
    TaskManifest, ApprovalRequest, RiskLevel
)
from core.safety.risk_scorer import RiskScorer
from core.safety.audit_logger import AuditLogger
from core.memory.postgres_store import PostgresStore
from core.memory.redis_store import RedisStore

logger = logging.getLogger(__name__)


class SafetyGate:
    """
    Central safety enforcement for all agent actions.

    Every action — browser click, email send, file download — passes
    through check() before execution.

    LOW:    Log and auto-pass.
    MEDIUM: Log and auto-pass. Send async notification.
    HIGH:   BLOCK. Generate ApprovalRequest. Notify user. Wait up to 30min.
    """

    def __init__(
        self,
        db: PostgresStore,
        redis: RedisStore,
        audit_logger: AuditLogger,
        risk_scorer: Optional[RiskScorer] = None,
    ):
        self.db = db
        self.redis = redis
        self.audit_logger = audit_logger
        self.risk_scorer = risk_scorer or RiskScorer()
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self.model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
            self.has_llm = True
        else:
            self.has_llm = False

    async def check(
        self,
        action: str,
        context: Dict[str, Any],
        task: TaskManifest,
    ) -> bool:
        """
        Check if an action can proceed.

        Returns True if action can proceed, False if blocked/rejected.
        Suspends execution on HIGH risk until human responds.

        Args:
            action: The action name (e.g., "click_pay", "navigate")
            context: Action context (element, url, amount, etc.)
            task: The current TaskManifest

        Returns:
            True if action is approved to proceed, False otherwise
        """
        risk = self.risk_scorer.classify(action, context)

        # Log every action regardless of risk
        await self.audit_logger.log(
            action=action,
            context=context,
            risk_level=risk,
            task_id=task.id,
            agent_name=context.get("agent_name", "unknown"),
            sub_task_id=context.get("sub_task_id"),
            result="checking",
        )

        if risk == RiskLevel.HIGH:
            return await self._handle_high_risk(action, context, task)

        if risk == RiskLevel.MEDIUM:
            # Log and proceed, send async notification
            await self.redis.publish_event(
                task_id=task.id,
                event_type="medium_risk_action",
                data={"action": action, "context": self._sanitize_context(context)},
            )

        # LOW / MEDIUM: proceed automatically
        return True

    async def _handle_high_risk(
        self,
        action: str,
        context: Dict[str, Any],
        task: TaskManifest,
    ) -> bool:
        """
        Handle HIGH risk action — block and wait for human approval.

        1. Generate plain English summary via LLM
        2. Create ApprovalRequest in database
        3. Notify user via Chainlit + optional Slack
        4. Wait for user decision (up to 30 minutes)
        5. Return True on approval, False on rejection/timeout
        """
        # Generate human-readable summary
        summary = await self._generate_summary(action, context)

        # Ensure screenshot is available for HIGH risk
        screenshot = context.get("screenshot")
        if not screenshot:
            logger.warning(f"HIGH risk action '{action}' has no screenshot. "
                           "Approval request created without visual evidence.")

        # Create approval request
        request = ApprovalRequest(
            task_id=task.id,
            sub_task_id=context.get("sub_task_id", "unknown"),
            action=action,
            context=self._sanitize_context(context),
            summary=summary,
            screenshot=screenshot,
            risk_level=RiskLevel.HIGH,
        )

        # Save to database
        await self.db.save_approval_request(request)

        # Notify user immediately via WebSocket/Chainlit
        await self.redis.publish_event(
            task_id=task.id,
            event_type="approval_required",
            data={
                "approval_id": request.id,
                "summary": summary,
                "screenshot": screenshot,
                "risk_level": "high",
                "action": action,
            },
        )

        # Optional Slack notification
        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if slack_url:
            await self._send_slack_notification(summary, request.id, slack_url)

        logger.info(f"HIGH RISK action blocked — awaiting approval: {summary}")

        # Log the block
        await self.audit_logger.log(
            action=f"approval_requested:{action}",
            context={"approval_id": request.id, "summary": summary},
            risk_level=RiskLevel.HIGH,
            task_id=task.id,
            agent_name="safety_gate",
            sub_task_id=context.get("sub_task_id"),
            result="blocked",
            reasoning=f"HIGH risk action requires human approval: {summary}",
        )

        # Wait for user decision via Redis pub/sub
        timeout_mins = int(os.environ.get("APPROVAL_TIMEOUT_MINS", "30"))
        decision = await self.redis.wait_for_approval(
            request.id,
            timeout_seconds=timeout_mins * 60,
        )

        if not decision:
            # Timeout — cancel
            await self.db.resolve_approval(request.id, "timed_out",
                                           resolved_by="system_timeout")
            await self.audit_logger.log(
                action="approval_timeout",
                context={"approval_id": request.id, "summary": summary},
                risk_level=RiskLevel.HIGH,
                task_id=task.id,
                agent_name="safety_gate",
                result="timeout",
                reasoning=f"No response received within {timeout_mins} minutes",
            )
            return False

        status = decision.get("status")
        modification = decision.get("modification")

        if status == "rejected":
            await self.audit_logger.log(
                action="approval_rejected",
                context={"approval_id": request.id, "summary": summary},
                risk_level=RiskLevel.HIGH,
                task_id=task.id,
                agent_name="safety_gate",
                result="rejected",
            )
            return False

        # Approved (possibly with modification)
        if modification:
            context["user_modification"] = modification

        await self.audit_logger.log(
            action="approval_granted",
            context={"approval_id": request.id,
                      "modified": modification is not None},
            risk_level=RiskLevel.HIGH,
            task_id=task.id,
            agent_name="safety_gate",
            result="approved",
        )
        return True

    async def _generate_summary(self, action: str, context: Dict[str, Any]) -> str:
        """
        Uses LLM to generate a clear, specific plain-English description.

        Examples of required output quality:
          "About to pay ₹1,499.00 to Namecheap to renew domain example.com for 1 year."
          "About to send an email to priya@acme.com with subject 'Re: Refund policy'."
          "About to submit your income tax return for FY 2024-25 on incometax.gov.in."
          "About to delete 47 files from the Downloads folder."
        """
        sanitized = self._sanitize_context(context)

        # Try LLM-generated summary
        if self.has_llm:
            try:
                prompt = (
                    f"Generate a single, clear, specific plain-English sentence "
                    f"describing this action for a human approver. Be precise about "
                    f"amounts, recipients, and consequences.\n\n"
                    f"Action: {action}\n"
                    f"Context: {sanitized}\n\n"
                    f"Response format: A single sentence starting with 'About to...'"
                )
                model = genai.GenerativeModel(self.model_name)
                response = await model.generate_content_async(prompt)
                return response.text.strip()
            except Exception as e:
                logger.warning(f"LLM summary generation failed: {e}")

        # Fallback: construct from available context
        parts = [f"About to {action.replace('_', ' ')}"]
        if context.get("amount"):
            parts.append(f"for {context['amount']}")
        if context.get("recipient") or context.get("to_email"):
            parts.append(f"to {context.get('recipient') or context.get('to_email')}")
        if context.get("site") or context.get("url"):
            parts.append(f"on {context.get('site') or context.get('url')}")
        return ". ".join(parts) + "."

    def _sanitize_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Remove any credential values before storing in database."""
        FORBIDDEN_KEYS = {"password", "secret", "token", "api_key", "credential"}
        return {
            k: "[REDACTED]" if any(f in k.lower() for f in FORBIDDEN_KEYS)
            else v
            for k, v in context.items()
        }

    async def _send_slack_notification(self, summary: str, approval_id: str,
                                        webhook_url: str):
        """Send Slack notification for approval request."""
        try:
            import httpx
            payload = {
                "text": (
                    f"⚠️ *Action Requires Approval*\n"
                    f"{summary}\n"
                    f"Approval ID: `{approval_id}`\n"
                    f"Respond in the agent UI or via API."
                ),
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json=payload)
        except Exception as e:
            logger.warning(f"Slack notification failed: {e}")
