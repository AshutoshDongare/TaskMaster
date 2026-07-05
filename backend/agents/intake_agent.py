"""
Intake Agent — Parses natural language task input into a structured TaskManifest.

Responsibilities:
- Intent classification (browse_and_act, download_document, aggregate_data, etc.)
- Entity extraction (URLs, accounts, dates, amounts, document types)
- Risk scoring (highest risk of any required action)
- Ambiguity detection (missing credentials, unclear accounts, unknown prices)

Uses Gemini API for reasoning. Outputs ONLY valid JSON matching TaskManifest schema.
"""

import os
import json
import logging
from typing import Optional, Dict, Any

import google.generativeai as genai

from core.task_manifest import TaskManifest, TaskIntent, RiskLevel, TaskStatus
from core.memory.credential_vault import CredentialVault
from core.memory.chroma_store import LongTermMemory

logger = logging.getLogger(__name__)

INTAKE_SYSTEM_PROMPT = """
You are a Task Intake Specialist. Your only job is to read a user's task
description and extract a precise, structured understanding of what they want.

Output ONLY valid JSON matching the TaskManifest schema. No prose. No markdown.

INTENT CLASSIFICATION:
  browse_and_act:       Must log into website and take action (pay, renew, submit form)
  download_document:    Must log in and download a specific file
  aggregate_data:       Must collect data from multiple sources or portals
  research_and_reply:   Must find information and communicate it (draft/send email)
  generic:              Cannot be clearly classified — Orchestrator will decide

ENTITY EXTRACTION — populate the entities dict with all of:
  urls:           Any website URLs mentioned or implied by context
  accounts:       Account names, usernames, portal names mentioned
  document_types: Specific form names, file types, document identifiers
  amounts:        Dollar/rupee amounts, budgets, expected costs
  dates:          Deadlines, fiscal years, specific dates
  email_refs:     Sender name/email, subject line, thread context if forwarded email

RISK LEVEL (set to highest risk of any required action):
  low:    Read-only, view, download — no side effects on external systems
  medium: Save drafts, write to local files — reversible
  high:   Send emails, make payments, submit forms — irreversible

AMBIGUITIES — list any question that MUST be answered before starting:
  - "Which account on [site]? You have multiple."
  - "No credentials stored for [site] — please add them first."
  - "Renewal price is unknown — should I proceed if it exceeds $X?"
  - "Which mutual fund portal specifically? You mentioned three."

CRITICAL RULE: If ambiguities exist that would prevent safe execution of a HIGH risk
task, populate the ambiguities list and leave sub_tasks empty. Return for clarification.
Do NOT make assumptions on irreversible actions.

OUTPUT JSON SCHEMA:
{
  "raw_input": "original user input verbatim",
  "intent": "browse_and_act | download_document | aggregate_data | research_and_reply | generic",
  "risk_level": "low | medium | high",
  "entities": {
    "urls": [],
    "accounts": [],
    "document_types": [],
    "amounts": [],
    "dates": [],
    "email_refs": []
  },
  "constraints": [],
  "ambiguities": []
}
"""


class IntakeAgent:
    """
    Parses natural language input into a structured TaskManifest.
    First agent in the pipeline — runs before any execution begins.
    """

    def __init__(
        self,
        vault: Optional[CredentialVault] = None,
        memory: Optional[LongTermMemory] = None,
    ):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is required for IntakeAgent")
        genai.configure(api_key=api_key)
        self.model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
        self.vault = vault
        self.memory = memory

    async def process(self, user_input: str,
                      email_context: Optional[str] = None) -> TaskManifest:
        """
        Process natural language input into a TaskManifest.

        Args:
            user_input: The raw task description from the user
            email_context: Optional forwarded email content

        Returns:
            A structured TaskManifest ready for the Orchestrator
        """
        # Combine input with email context if present
        full_input = user_input
        if email_context:
            full_input += f"\n\n--- Forwarded Email ---\n{email_context}"

        # Get available sites from vault for credential checking
        available_sites = []
        if self.vault:
            try:
                available_sites = self.vault.list_sites()
            except Exception as e:
                logger.warning(f"Could not list vault sites: {e}")

        # Build the user message with additional context
        user_message = (
            f"Parse this task:\n\n{full_input}\n\n"
            f"Sites with stored credentials: {available_sites}\n"
            f"If the task requires logging into a site not in this list, "
            f"add an ambiguity about missing credentials."
        )

        # Call LLM
        try:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=INTAKE_SYSTEM_PROMPT
            )
            response = await model.generate_content_async(user_message)
            response_text = response.text.strip()

            # Parse JSON response
            # Handle potential markdown code blocks
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            parsed = json.loads(response_text)

        except json.JSONDecodeError as e:
            logger.error(f"IntakeAgent LLM returned invalid JSON: {e}")
            # Fallback: create a generic manifest
            parsed = {
                "raw_input": user_input,
                "intent": "generic",
                "risk_level": "medium",
                "entities": {},
                "constraints": [],
                "ambiguities": [
                    "Could not automatically parse this task. "
                    "Please provide more specific instructions."
                ],
            }
        except Exception as e:
            logger.error(f"IntakeAgent LLM call failed: {e}")
            raise

        # Build TaskManifest
        manifest = TaskManifest(
            raw_input=user_input,
            intent=TaskIntent(parsed.get("intent", "generic")),
            risk_level=RiskLevel(parsed.get("risk_level", "medium")),
            entities=parsed.get("entities", {}),
            constraints=parsed.get("constraints", []),
            ambiguities=parsed.get("ambiguities", []),
            status=TaskStatus.PENDING,
        )

        # Credential check — add ambiguities for missing credentials
        if self.vault:
            await self._check_credentials(manifest)

        logger.info(
            f"IntakeAgent parsed: intent={manifest.intent.value}, "
            f"risk={manifest.risk_level.value}, "
            f"ambiguities={len(manifest.ambiguities)}"
        )

        return manifest

    async def _check_credentials(self, manifest: TaskManifest):
        """
        Cross-reference extracted URLs/sites against stored credentials.
        Add ambiguities for any required but missing credentials.
        """
        urls = manifest.entities.get("urls", [])
        accounts = manifest.entities.get("accounts", [])

        sites_to_check = set()
        for url in urls:
            # Extract domain from URL
            domain = url.replace("https://", "").replace("http://", "")
            domain = domain.split("/")[0].replace("www.", "")
            sites_to_check.add(domain)

        for account in accounts:
            sites_to_check.add(account.lower())

        for site in sites_to_check:
            if not self.vault.exists(site):
                # Check if any stored site is a substring match
                stored = self.vault.list_sites()
                matches = [s for s in stored if site in s or s in site]
                if not matches:
                    manifest.ambiguities.append(
                        f"No credentials stored for '{site}' — "
                        f"please add them first via POST /credentials."
                    )
