"""
Planner Agent — Decomposes TaskManifest into ordered sub-tasks.

Responsibilities:
- Break down high-level task into atomic, executable sub-tasks
- Assign each sub-task to a specialist team (browser, document, email, research)
- Define dependencies between sub-tasks
- Identify sub-tasks that can run in parallel
- Assign risk levels and success criteria per sub-task
"""

import os
import json
import logging
from typing import List, Optional

import google.generativeai as genai

from core.task_manifest import (
    TaskManifest, SubTask, TaskIntent, RiskLevel, TaskStatus
)
from core.memory.chroma_store import LongTermMemory

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """
You are a Task Planner. You receive a structured TaskManifest and decompose it
into atomic sub-tasks that specialist agents can execute.

For each sub-task, define:
- description: precise instruction for the specialist agent
- specialist_team: exactly one of: browser | document | email | research
- tools_required: specific tools the agent will need (navigate, click, type,
  screenshot, extract_text, download_file, search_web, read_email, etc.)
- depends_on: IDs of sub-tasks that must succeed before this one can start
- risk_level: low | medium | high
- success_criteria: exact description of what verified completion looks like

CRITICAL RULES:
1. HIGH risk actions (payments, email sends, form submissions) MUST be their own
   sub-task. Never combine a HIGH risk action with a LOW risk one.
2. Login is always its own sub-task. Never combine login with post-login actions.
3. Mark sub-tasks that can run in PARALLEL (no shared dependencies).
4. Each sub-task should be atomic — completable in one specialist session.
5. Always include a final "output" sub-task to deliver results.
6. For payment flows: create a sub-task that STOPS at the confirmation page
   (before clicking pay), and a separate sub-task that completes payment AFTER approval.

OUTPUT: JSON array of sub-tasks matching this schema:
[
  {
    "id": "st-01",
    "description": "Login to namecheap.com using stored credentials",
    "specialist_team": "browser",
    "tools_required": ["navigate", "type", "click", "screenshot"],
    "depends_on": [],
    "risk_level": "low",
    "success_criteria": "Dashboard or account page is visible after login"
  },
  ...
]
"""


class PlannerAgent:
    """
    Decomposes a TaskManifest into ordered, executable sub-tasks.
    Called by the Orchestrator after intake but before execution.
    """

    def __init__(self, memory: Optional[LongTermMemory] = None):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is required for PlannerAgent")
        genai.configure(api_key=api_key)
        self.model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
        self.memory = memory

    async def plan(self, manifest: TaskManifest) -> List[SubTask]:
        """
        Generate sub-tasks for a TaskManifest.

        Args:
            manifest: The structured task manifest from IntakeAgent

        Returns:
            List of SubTask objects with dependencies defined
        """
        # Recall relevant memories for planning
        memory_context = ""
        if self.memory:
            try:
                sites = manifest.entities.get("urls", [])
                for site in sites:
                    memories = self.memory.recall(
                        query=f"{manifest.intent.value} on {site}",
                        n_results=3,
                        site_filter=site.replace("https://", "").replace("http://", "").split("/")[0],
                    )
                    if memories:
                        memory_context += f"\nKnown patterns for {site}:\n"
                        for m in memories:
                            memory_context += f"  - [{m['memory_type']}] {m['content']}\n"
            except Exception as e:
                logger.warning(f"Memory recall failed during planning: {e}")

        # Build planning prompt
        user_message = (
            f"Decompose this task into sub-tasks:\n\n"
            f"Task: {manifest.raw_input}\n"
            f"Intent: {manifest.intent.value}\n"
            f"Overall Risk: {manifest.risk_level.value}\n"
            f"Entities: {json.dumps(manifest.entities)}\n"
            f"Constraints: {manifest.constraints}\n"
        )

        if memory_context:
            user_message += f"\nRelevant memories from past tasks:\n{memory_context}"

        # Call LLM for planning
        try:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=PLANNER_SYSTEM_PROMPT
            )
            response = await model.generate_content_async(user_message)
            response_text = response.text.strip()

            # Parse JSON response
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            sub_task_dicts = json.loads(response_text)

        except json.JSONDecodeError as e:
            logger.error(f"PlannerAgent returned invalid JSON: {e}")
            # Fallback: create a single generic sub-task
            sub_task_dicts = [
                {
                    "id": "st-01",
                    "description": manifest.raw_input,
                    "specialist_team": self._guess_team(manifest.intent),
                    "tools_required": ["navigate", "screenshot", "extract_text"],
                    "depends_on": [],
                    "risk_level": manifest.risk_level.value,
                    "success_criteria": "Task completed as described",
                }
            ]
        except Exception as e:
            logger.error(f"PlannerAgent LLM call failed: {e}")
            raise

        # Convert dicts to SubTask objects
        sub_tasks = []
        for st_dict in sub_task_dicts:
            sub_task = SubTask(
                id=st_dict.get("id", f"st-{len(sub_tasks) + 1:02d}"),
                description=st_dict["description"],
                specialist_team=st_dict.get("specialist_team", "browser"),
                tools_required=st_dict.get("tools_required", []),
                depends_on=st_dict.get("depends_on", []),
                risk_level=RiskLevel(st_dict.get("risk_level", "low")),
                status=TaskStatus.PENDING,
                success_criteria=st_dict.get("success_criteria", ""),
            )
            sub_tasks.append(sub_task)

        logger.info(f"PlannerAgent created {len(sub_tasks)} sub-tasks "
                     f"for task intent={manifest.intent.value}")

        return sub_tasks

    def _guess_team(self, intent: TaskIntent) -> str:
        """Fallback specialist team guess based on intent."""
        mapping = {
            TaskIntent.BROWSE_AND_ACT: "browser",
            TaskIntent.DOWNLOAD_DOCUMENT: "browser",
            TaskIntent.AGGREGATE_DATA: "browser",
            TaskIntent.RESEARCH_AND_REPLY: "research",
            TaskIntent.GENERIC: "browser",
        }
        return mapping.get(intent, "browser")
