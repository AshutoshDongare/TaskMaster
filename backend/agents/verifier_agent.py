"""
Verifier Agent — Independently confirms that sub-tasks actually succeeded.

Professionally skeptical. Never trusts specialist self-reporting alone.
Uses LLM reasoning to analyze evidence (screenshots, file paths, extracted text)
against success criteria.

A false negative (unnecessary retry) is ALWAYS preferred over a false positive
(treating a failed action as successful).
"""

import os
import json
import logging
from typing import Dict, Any, Optional

import google.generativeai as genai

from core.task_manifest import VerificationResult

logger = logging.getLogger(__name__)

VERIFIER_SYSTEM_PROMPT = """
You are the Verifier. Your only job is to independently confirm that a completed
sub-task actually succeeded. You are professionally skeptical.

You receive:
  - intended_goal: what the sub-task was supposed to accomplish
  - claimed_result: what the specialist agent reported
  - evidence: screenshots (base64), file paths, extracted text, API responses
  - success_criteria: what "done" looks like

VERIFICATION METHODS BY TYPE:

Browser / web action tasks:
  - Compare before/after screenshots — did the page actually change as expected?
  - Look for explicit confirmation messages, success banners, receipt numbers
  - Look for error messages the agent may have ignored or missed
  - For downloads: verify file exists at claimed path, is not empty (0 bytes)
  - For logins: verify account name or dashboard is visible, not the login page again
  - For payments: verify confirmation number exists on receipt screenshot

Document processing tasks:
  - Are extracted numerical values plausible? (portfolio value in expected range?)
  - If multiple values extracted: do the parts sum to the reported total?
  - Are dates in valid format and plausible for context?
  - Flag any extraction with clear OCR artifacts or garbled text

Email tasks:
  - For drafts: verify status is DRAFT, not SENT
  - For sent: verify sent folder confirmation received
  - Does draft content match what was requested?

Research tasks:
  - Do the cited sources actually say what the agent claims?
  - Are there contradictions between sources?
  - Is the information current?

OUTPUT — respond with ONLY this JSON:
{
  "verified": true | false,
  "confidence": 0.0 to 1.0,
  "evidence_summary": "specific proof of success or failure",
  "issues": ["list of specific problems found if any"],
  "recommendation": "proceed | retry | replan | escalate"
}

CONFIDENCE THRESHOLDS:
  > 0.90 : high confidence, proceed
  0.75–0.90: acceptable, proceed with note
  0.50–0.75: uncertain, recommend retry
  < 0.50 : low confidence, recommend replan

When in doubt, recommend retry. False negatives are always preferred.
"""


class VerifierAgent:
    """
    Independent verification of sub-task results.
    Called by the Orchestrator after each specialist completes.
    """

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is required for VerifierAgent")
        genai.configure(api_key=api_key)
        self.model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

    async def verify(
        self,
        intended_goal: str,
        claimed_result: Dict[str, Any],
        success_criteria: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """
        Verify a sub-task result.

        Args:
            intended_goal: What the sub-task was supposed to accomplish
            claimed_result: What the specialist agent reported
            success_criteria: What "done" looks like
            evidence: Screenshots, file paths, extracted text

        Returns:
            VerificationResult with verified/confidence/recommendation
        """
        evidence = evidence or {}

        # Build verification prompt
        user_message = (
            f"Verify this sub-task result:\n\n"
            f"INTENDED GOAL: {intended_goal}\n\n"
            f"SUCCESS CRITERIA: {success_criteria}\n\n"
            f"CLAIMED RESULT:\n{json.dumps(claimed_result, indent=2, default=str)}\n\n"
            f"EVIDENCE:\n{json.dumps(evidence, indent=2, default=str)}\n"
        )

        # Include file-based evidence checks
        file_checks = await self._check_file_evidence(evidence)
        if file_checks:
            user_message += f"\nFILE VERIFICATION:\n{file_checks}\n"

        try:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=VERIFIER_SYSTEM_PROMPT
            )
            response = await model.generate_content_async(user_message)
            response_text = response.text.strip()

            # Parse JSON response
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            parsed = json.loads(response_text)

            result = VerificationResult(
                verified=parsed.get("verified", False),
                confidence=parsed.get("confidence", 0.5),
                evidence_summary=parsed.get("evidence_summary", ""),
                issues=parsed.get("issues", []),
                recommendation=parsed.get("recommendation", "retry"),
            )

        except json.JSONDecodeError as e:
            logger.error(f"Verifier returned invalid JSON: {e}")
            result = VerificationResult(
                verified=False,
                confidence=0.3,
                evidence_summary="Verification inconclusive — JSON parse error",
                issues=["Verifier response was not valid JSON"],
                recommendation="retry",
            )
        except Exception as e:
            logger.error(f"Verifier LLM call failed: {e}")
            result = VerificationResult(
                verified=False,
                confidence=0.0,
                evidence_summary=f"Verification failed: {e}",
                issues=[str(e)],
                recommendation="retry",
            )

        logger.info(
            f"Verification: verified={result.verified} "
            f"confidence={result.confidence:.2f} "
            f"recommendation={result.recommendation}"
        )

        return result

    async def _check_file_evidence(self, evidence: Dict[str, Any]) -> str:
        """
        Perform concrete file-based checks on evidence.
        Returns a string describing the check results.
        """
        checks = []

        # Check file paths
        file_path = evidence.get("file_path")
        if file_path:
            import os as _os
            if _os.path.exists(file_path):
                size = _os.path.getsize(file_path)
                checks.append(f"File exists: {file_path} ({size} bytes)")
                if size == 0:
                    checks.append("WARNING: File is empty (0 bytes)")
            else:
                checks.append(f"WARNING: File NOT found at: {file_path}")

        # Check screenshot paths
        screenshot = evidence.get("screenshot_path") or evidence.get("screenshot")
        if screenshot and isinstance(screenshot, str) and not screenshot.startswith("data:"):
            import os as _os
            if _os.path.exists(screenshot):
                checks.append(f"Screenshot exists: {screenshot}")
            else:
                checks.append(f"Screenshot NOT found: {screenshot}")

        return "\n".join(checks) if checks else ""
