"""
Risk Scorer — Classify action risk level from action name and context.

Risk levels determine Safety Gate behavior:
  LOW:    Log and proceed automatically
  MEDIUM: Log and proceed, send async notification
  HIGH:   BLOCK — create ApprovalRequest, notify user, wait for decision
"""

import logging
from typing import Dict, Any, Optional

from core.task_manifest import RiskLevel

logger = logging.getLogger(__name__)


class RiskScorer:
    """
    Classifies the risk level of agent actions based on action name and context.
    """

    # Actions always classified HIGH risk regardless of context
    HIGH_RISK_ACTIONS = {
        "make_payment", "confirm_payment", "click_pay",
        "click_confirm", "click_submit", "click_send",
        "send_email", "send_message",
        "submit_form", "submit_application",
        "delete_file", "delete_account",
        "wire_transfer", "bank_transfer",
        "renew_subscription",   # involves payment
        "book_appointment",     # if involves payment
        "place_order",
    }

    # Actions always LOW risk
    LOW_RISK_ACTIONS = {
        "navigate", "screenshot", "scroll", "wait",
        "extract_text", "get_page_elements",
        "read_email", "search_web",
        "retrieve_credential",  # vault retrieval, not the value itself
        "get_balance", "get_portfolio_value",
        "download_file",
    }

    # Actions that are MEDIUM risk
    MEDIUM_RISK_ACTIONS = {
        "save_draft", "write_file", "save_to_vault",
        "create_report", "stage_email",
        "type", "select", "click",  # Generic browser actions — medium by default
        "fill_form",
    }

    # Context keywords that elevate risk to HIGH
    HIGH_RISK_CONTEXT_KEYWORDS = {
        "payment", "pay", "checkout", "purchase", "buy",
        "send", "submit", "confirm", "delete", "remove",
        "transfer", "wire", "refund",
    }

    def classify(self, action: str, context: Optional[Dict[str, Any]] = None) -> RiskLevel:
        """
        Classify the risk level of an action.

        Args:
            action: The action name (e.g., "click_pay", "navigate", "save_draft")
            context: Additional context about the action

        Returns:
            RiskLevel enum value
        """
        action_lower = action.lower().strip()
        context = context or {}

        # 1. Check explicit HIGH risk actions
        if action_lower in self.HIGH_RISK_ACTIONS:
            logger.info(f"Action '{action}' classified as HIGH risk (explicit match)")
            return RiskLevel.HIGH

        # 2. Check explicit LOW risk actions
        if action_lower in self.LOW_RISK_ACTIONS:
            return RiskLevel.LOW

        # 3. Check explicit MEDIUM risk actions
        if action_lower in self.MEDIUM_RISK_ACTIONS:
            # But elevate to HIGH if context suggests irreversible action
            if self._context_suggests_high_risk(context):
                logger.info(f"Action '{action}' elevated to HIGH risk due to context")
                return RiskLevel.HIGH
            return RiskLevel.MEDIUM

        # 4. Check if action name contains HIGH risk keywords
        for keyword in self.HIGH_RISK_CONTEXT_KEYWORDS:
            if keyword in action_lower:
                logger.info(f"Action '{action}' classified as HIGH risk "
                            f"(contains keyword '{keyword}')")
                return RiskLevel.HIGH

        # 5. Check context for risk indicators
        if self._context_suggests_high_risk(context):
            logger.info(f"Action '{action}' classified as HIGH risk due to context")
            return RiskLevel.HIGH

        # 6. Default: unknown actions are MEDIUM (not LOW — safe default)
        logger.info(f"Action '{action}' classified as MEDIUM risk (default)")
        return RiskLevel.MEDIUM

    def _context_suggests_high_risk(self, context: Dict[str, Any]) -> bool:
        """Check if context metadata suggests a high-risk operation."""
        # Check for monetary amounts
        if context.get("amount") or context.get("price") or context.get("cost"):
            return True

        # Check for recipient information (sending something)
        if context.get("recipient") or context.get("to_email"):
            return True

        # Check for confirmation/submission context
        if context.get("is_final_step") or context.get("irreversible"):
            return True

        # Check description for risk keywords
        description = str(context.get("description", "")).lower()
        for keyword in self.HIGH_RISK_CONTEXT_KEYWORDS:
            if keyword in description:
                return True

        return False

    def is_always_high_risk(self, action: str) -> bool:
        """Check if an action is unconditionally HIGH risk."""
        return action.lower().strip() in self.HIGH_RISK_ACTIONS

    def is_always_low_risk(self, action: str) -> bool:
        """Check if an action is unconditionally LOW risk."""
        return action.lower().strip() in self.LOW_RISK_ACTIONS
