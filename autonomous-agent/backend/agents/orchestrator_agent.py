"""
Orchestrator Agent — Master coordinator of the autonomous agent system.

Implements the ReAct (Reasoning + Acting) loop:
  Think → Act → Observe → Repeat

Responsibilities:
- Receive TaskManifest from IntakeAgent
- Invoke PlannerAgent for sub-task decomposition
- Dispatch specialist agents for each sub-task
- Handle verification results (from VerifierAgent)
- Manage retries and replanning on failures
- Enforce safety gate for HIGH risk actions
- Escalate to human when needed
- Track task state across the full lifecycle
"""

import os
import json
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

import google.generativeai as genai

from core.task_manifest import (
    TaskManifest, SubTask, TaskStatus, RiskLevel, TaskIntent
)
from core.memory.redis_store import RedisStore
from core.memory.chroma_store import LongTermMemory
from core.memory.postgres_store import PostgresStore
from core.memory.credential_vault import CredentialVault
from core.safety.safety_gate import SafetyGate
from core.safety.audit_logger import AuditLogger

from agents.planner_agent import PlannerAgent
from agents.verifier_agent import VerifierAgent
from agents.output_agent import OutputAgent

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM_PROMPT = """
You are the Orchestrator — the master coordinator of an autonomous agent system.
You receive structured task manifests and are responsible for completing them safely.

YOUR DECISION LOOP — run continuously until task is COMPLETED or FAILED:

STEP 1 — ASSESS
  Read the current task state. What has been verified complete? What remains?
  What context from memory is relevant? Any failures to account for in replanning?

STEP 2 — PLAN (if sub_tasks not yet generated)
  Decompose the task into atomic sub-tasks.

STEP 3 — DISPATCH
  Select the next executable sub-task (all dependencies verified complete).

STEP 4 — OBSERVE
  Receive result from specialist. Check Verifier's independent assessment.
  Only Verifier confirmation counts as success.

STEP 5 — HANDLE FAILURE
  Attempt 1: Retry same approach with more specific instructions
  Attempt 2: Alternative approach
  Attempt 3: Escalate to user with specific question

STEP 6 — ESCALATE TO HUMAN immediately when:
  - Any HIGH risk action is about to execute
  - 3 retries failed on any sub-task
  - New information contradicts the task
  - Uncertain about which path to take on HIGH risk

STEP 7 — COMPLETE
  All sub-tasks verified complete. Call output_agent.assemble(task_manifest).

MANDATORY RULES:
  1. Never assume credentials exist. Always check first.
  2. Never execute HIGH risk actions without human approval returning True.
  3. Always dispatch Verifier after each sub-task completes.
  4. Log every significant decision with reasoning.
  5. Never pass credential values in specialist instructions.
  6. A paused task is always better than a wrong irreversible action.
"""

ORCHESTRATOR_REPLAN_PROMPT = """
Sub-task {sub_task_id} has failed {attempts} times.
Description: {description}
Error: {error}
Evidence: {evidence}

Analyze why this failed and decide:

A) ALTERNATIVE APPROACH — describe a different way to accomplish this sub-task
B) SKIP AND REROUTE — if not critical, describe how to reach the goal differently
C) ESCALATE — what specific question would you ask the user to unblock this?

Current task state:
{task_state_json}

Respond with JSON: {"choice": "A|B|C", "action": "specific next step description"}
"""


class OrchestratorAgent:
    """
    Master coordinator implementing the ReAct execution loop.

    Manages the full task lifecycle from planning through verified completion.
    """

    MAX_RETRIES = 3  # Hard ceiling per sub-task

    def __init__(
        self,
        db: PostgresStore,
        redis: RedisStore,
        memory: LongTermMemory,
        vault: CredentialVault,
        safety_gate: SafetyGate,
        audit_logger: AuditLogger,
    ):
        self.db = db
        self.redis = redis
        self.memory = memory
        self.vault = vault
        self.safety_gate = safety_gate
        self.audit_logger = audit_logger

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is required")
        genai.configure(api_key=api_key)
        self.model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

        self.planner = PlannerAgent(memory=memory)
        self.verifier = VerifierAgent()
        self.output_agent = OutputAgent()

        # Specialist dispatch registry — populated by specialist imports
        self._specialists: Dict[str, Any] = {}

    def register_specialist(self, team: str, specialist):
        """Register a specialist agent for a team."""
        self._specialists[team] = specialist
        logger.info(f"Registered specialist for team: {team}")

    async def execute_task(self, manifest: TaskManifest) -> TaskManifest:
        """
        Execute the full ReAct loop for a task.

        Args:
            manifest: TaskManifest from IntakeAgent

        Returns:
            Updated TaskManifest with results and status
        """
        task_id = manifest.id

        # Log task start
        await self.audit_logger.log(
            action="task_started",
            context={"intent": manifest.intent.value,
                      "risk_level": manifest.risk_level.value},
            risk_level=manifest.risk_level,
            task_id=task_id,
            agent_name="orchestrator",
        )

        # Publish task start event
        await self.redis.publish_event(task_id, "task_started", {
            "intent": manifest.intent.value,
            "risk_level": manifest.risk_level.value,
        })

        try:
            # STEP 1: Check for ambiguities
            if manifest.ambiguities:
                logger.info(f"Task {task_id} has ambiguities — returning for clarification")
                manifest.status = TaskStatus.AWAITING_APPROVAL
                await self.db.update_task(task_id, status=TaskStatus.AWAITING_APPROVAL.value)
                return manifest

            # STEP 2: Plan — decompose into sub-tasks
            manifest.status = TaskStatus.PLANNING
            await self.db.update_task(task_id, status=TaskStatus.PLANNING.value)
            await self.redis.publish_event(task_id, "planning_started", {})

            manifest.sub_tasks = await self.planner.plan(manifest)
            await self.db.update_sub_tasks(task_id, manifest.sub_tasks)

            await self.audit_logger.log(
                action="planning_complete",
                context={"num_sub_tasks": len(manifest.sub_tasks),
                          "sub_task_ids": [st.id for st in manifest.sub_tasks]},
                risk_level="low",
                task_id=task_id,
                agent_name="orchestrator",
                reasoning=f"Decomposed into {len(manifest.sub_tasks)} sub-tasks",
            )

            # STEP 3-6: Execute sub-tasks
            manifest.status = TaskStatus.EXECUTING
            await self.db.update_task(task_id, status=TaskStatus.EXECUTING.value)

            await self._execute_sub_tasks(manifest)

            # Check if all sub-tasks completed
            all_complete = all(
                st.status == TaskStatus.COMPLETED
                for st in manifest.sub_tasks
            )

            if all_complete:
                # STEP 7: Output
                result = await self.output_agent.assemble(manifest)
                manifest.result = result
                manifest.status = TaskStatus.COMPLETED
                manifest.completed_at = datetime.utcnow()

                await self.db.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED.value,
                    result=json.dumps(result, default=str),
                    completed_at=manifest.completed_at,
                )

                # Store successful patterns in memory
                await self._store_success_memories(manifest)

                await self.redis.publish_event(task_id, "task_completed", {
                    "result": result,
                })

                await self.audit_logger.log(
                    action="task_completed",
                    context={"result_summary": str(result)[:500]},
                    risk_level="low",
                    task_id=task_id,
                    agent_name="orchestrator",
                    result="success",
                )
            else:
                # Some sub-tasks failed
                failed = [st for st in manifest.sub_tasks
                          if st.status == TaskStatus.FAILED]
                manifest.status = TaskStatus.FAILED
                manifest.result = {
                    "error": f"{len(failed)} sub-tasks failed",
                    "failed_sub_tasks": [
                        {"id": st.id, "description": st.description,
                         "error": st.error}
                        for st in failed
                    ],
                }
                await self.db.update_task(
                    task_id,
                    status=TaskStatus.FAILED.value,
                    result=json.dumps(manifest.result, default=str),
                )

                await self.redis.publish_event(task_id, "task_failed", {
                    "error": manifest.result["error"],
                })

        except Exception as e:
            logger.exception(f"Orchestrator error for task {task_id}: {e}")
            manifest.status = TaskStatus.FAILED
            manifest.result = {"error": str(e)}
            await self.db.update_task(
                task_id,
                status=TaskStatus.FAILED.value,
                result=json.dumps({"error": str(e)}),
            )
            await self.audit_logger.log_error(
                task_id=task_id,
                agent_name="orchestrator",
                action="orchestrator_error",
                error=str(e),
            )

        return manifest

    async def _execute_sub_tasks(self, manifest: TaskManifest):
        """
        Execute sub-tasks respecting dependencies.
        Parallel sub-tasks (no shared dependencies) run concurrently.
        """
        task_id = manifest.id
        completed_ids = set()

        while True:
            # Find executable sub-tasks (all deps completed, not yet started)
            executable = []
            for st in manifest.sub_tasks:
                if st.status != TaskStatus.PENDING:
                    continue
                deps_met = all(dep_id in completed_ids for dep_id in st.depends_on)
                if deps_met:
                    executable.append(st)

            if not executable:
                # No more executable tasks
                # Check if we're stuck (pending tasks exist but deps not met)
                pending = [st for st in manifest.sub_tasks
                           if st.status == TaskStatus.PENDING]
                if pending:
                    # Stuck — some deps will never be met (failed upstream)
                    for st in pending:
                        st.status = TaskStatus.FAILED
                        st.error = "Upstream dependency failed"
                break

            # Execute in parallel where possible
            if len(executable) > 1:
                logger.info(f"Executing {len(executable)} sub-tasks in parallel")
                tasks = [
                    self._execute_single_sub_task(manifest, st)
                    for st in executable
                ]
                await asyncio.gather(*tasks)
            else:
                await self._execute_single_sub_task(manifest, executable[0])

            # Update completed set
            for st in manifest.sub_tasks:
                if st.status == TaskStatus.COMPLETED:
                    completed_ids.add(st.id)

            # Update DB
            await self.db.update_sub_tasks(task_id, manifest.sub_tasks)

    async def _execute_single_sub_task(
        self,
        manifest: TaskManifest,
        sub_task: SubTask,
    ):
        """Execute a single sub-task with retries and verification."""
        task_id = manifest.id
        sub_task.status = TaskStatus.EXECUTING
        sub_task.attempts += 1

        await self.redis.publish_event(task_id, "sub_task_started", {
            "sub_task_id": sub_task.id,
            "description": sub_task.description,
            "team": sub_task.specialist_team,
            "attempt": sub_task.attempts,
        })

        await self.audit_logger.log(
            action="sub_task_started",
            context={"description": sub_task.description,
                      "team": sub_task.specialist_team,
                      "attempt": sub_task.attempts},
            risk_level=sub_task.risk_level,
            task_id=task_id,
            agent_name="orchestrator",
            sub_task_id=sub_task.id,
        )

        # Safety gate check for HIGH risk sub-tasks
        if sub_task.risk_level == RiskLevel.HIGH:
            approved = await self.safety_gate.check(
                action=sub_task.description,
                context={
                    "sub_task_id": sub_task.id,
                    "agent_name": "orchestrator",
                    "description": sub_task.description,
                },
                task=manifest,
            )
            if not approved:
                sub_task.status = TaskStatus.CANCELLED
                sub_task.error = "User rejected HIGH risk action"
                logger.info(f"Sub-task {sub_task.id} cancelled — user rejected")
                return

        # Dispatch to specialist
        specialist = self._specialists.get(sub_task.specialist_team)
        if not specialist:
            sub_task.status = TaskStatus.FAILED
            sub_task.error = f"No specialist registered for team: {sub_task.specialist_team}"
            logger.error(sub_task.error)
            return

        try:
            # Recall relevant memories for this sub-task
            memory_context = await self._recall_for_subtask(sub_task)

            # Build specialist instructions
            instructions = self._build_specialist_instructions(
                sub_task, manifest, memory_context
            )

            # Execute specialist
            result = await specialist.execute(
                instructions=instructions,
                sub_task_id=sub_task.id,
                task_id=task_id,
            )

            # Verify the result
            verification = await self.verifier.verify(
                intended_goal=sub_task.description,
                claimed_result=result,
                success_criteria=sub_task.success_criteria,
                evidence=result.get("evidence", {}),
            )

            await self.redis.publish_event(task_id, "sub_task_verification", {
                "sub_task_id": sub_task.id,
                "verified": verification.verified,
                "confidence": verification.confidence,
            })

            if verification.verified and verification.confidence >= 0.75:
                sub_task.status = TaskStatus.COMPLETED
                sub_task.result = result
                logger.info(
                    f"Sub-task {sub_task.id} VERIFIED "
                    f"(confidence={verification.confidence:.2f})"
                )

                await self.redis.publish_event(task_id, "sub_task_completed", {
                    "sub_task_id": sub_task.id,
                    "verified": True,
                    "confidence": verification.confidence,
                })
            else:
                # Verification failed — retry or replan
                await self._handle_failure(
                    manifest, sub_task,
                    error=verification.evidence_summary,
                    issues=verification.issues,
                )

        except Exception as e:
            logger.error(f"Sub-task {sub_task.id} execution error: {e}")
            await self._handle_failure(
                manifest, sub_task,
                error=str(e),
                issues=[str(e)],
            )

    async def _handle_failure(
        self,
        manifest: TaskManifest,
        sub_task: SubTask,
        error: str,
        issues: List[str],
    ):
        """Handle sub-task failure with retry/replan/escalate logic."""
        task_id = manifest.id

        await self.audit_logger.log(
            action="sub_task_failed",
            context={"error": error, "issues": issues,
                      "attempt": sub_task.attempts},
            risk_level=sub_task.risk_level,
            task_id=task_id,
            agent_name="orchestrator",
            sub_task_id=sub_task.id,
            result="failure",
        )

        if sub_task.attempts < self.MAX_RETRIES:
            # Retry with replanning
            sub_task.status = TaskStatus.RETRYING
            sub_task.error = error

            await self.redis.publish_event(task_id, "sub_task_failed", {
                "sub_task_id": sub_task.id,
                "error": error,
                "attempts": sub_task.attempts,
                "will_retry": True,
            })

            # Ask LLM for alternative approach
            if sub_task.attempts >= 2:
                replan_result = await self._replan(manifest, sub_task, error)
                if replan_result.get("choice") == "C":
                    # Escalate
                    sub_task.status = TaskStatus.FAILED
                    sub_task.error = f"Escalated: {replan_result.get('action', error)}"
                    return
                # Update description with alternative approach
                sub_task.description = replan_result.get("action", sub_task.description)

            # Reset to pending for retry
            sub_task.status = TaskStatus.PENDING

        else:
            # Max retries exceeded — escalate
            sub_task.status = TaskStatus.FAILED
            sub_task.error = f"Failed after {self.MAX_RETRIES} attempts: {error}"

            await self.redis.publish_event(task_id, "sub_task_failed", {
                "sub_task_id": sub_task.id,
                "error": sub_task.error,
                "attempts": sub_task.attempts,
                "will_retry": False,
            })

            logger.warning(f"Sub-task {sub_task.id} FAILED after "
                           f"{self.MAX_RETRIES} retries — escalating")

            # Store failure pattern in memory
            try:
                self.memory.remember(
                    content=f"FAILURE: {sub_task.description}. Error: {error}. "
                            f"After {self.MAX_RETRIES} attempts.",
                    memory_type="failure_pattern",
                    site=manifest.entities.get("urls", [None])[0] if manifest.entities.get("urls") else None,
                    metadata={"task_intent": manifest.intent.value,
                              "sub_task_id": sub_task.id},
                )
            except Exception as e:
                logger.warning(f"Failed to store failure memory: {e}")

    async def _replan(
        self,
        manifest: TaskManifest,
        sub_task: SubTask,
        error: str,
    ) -> Dict[str, Any]:
        """Ask LLM for an alternative approach after failed retries."""
        try:
            prompt = ORCHESTRATOR_REPLAN_PROMPT.format(
                sub_task_id=sub_task.id,
                attempts=sub_task.attempts,
                description=sub_task.description,
                error=error,
                evidence=sub_task.error or "",
                task_state_json=json.dumps({
                    "intent": manifest.intent.value,
                    "entities": manifest.entities,
                    "sub_tasks": [
                        {"id": st.id, "description": st.description,
                         "status": st.status.value}
                        for st in manifest.sub_tasks
                    ],
                }, indent=2),
            )

            model = genai.GenerativeModel(self.model_name)
            response = await model.generate_content_async(prompt)
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            return json.loads(text)

        except Exception as e:
            logger.error(f"Replanning failed: {e}")
            return {"choice": "C", "action": f"Replanning failed: {e}"}

    async def _recall_for_subtask(self, sub_task: SubTask) -> str:
        """Recall relevant memories for a sub-task."""
        try:
            memories = self.memory.recall(
                query=sub_task.description,
                n_results=3,
            )
            if memories:
                parts = ["Relevant memories from past tasks:"]
                for m in memories:
                    parts.append(f"  [{m['memory_type']}] {m['content']}")
                return "\n".join(parts)
        except Exception as e:
            logger.warning(f"Memory recall failed: {e}")
        return ""

    def _build_specialist_instructions(
        self,
        sub_task: SubTask,
        manifest: TaskManifest,
        memory_context: str,
    ) -> str:
        """Build instructions for the specialist agent."""
        instructions = (
            f"TASK: {sub_task.description}\n"
            f"SUCCESS CRITERIA: {sub_task.success_criteria}\n"
            f"OVERALL CONTEXT: {manifest.raw_input}\n"
        )

        # Add entity context
        if manifest.entities:
            instructions += f"ENTITIES: {json.dumps(manifest.entities)}\n"

        # Add memory context
        if memory_context:
            instructions += f"\n{memory_context}\n"

        # CRITICAL: Never pass credentials
        instructions += (
            "\nCREDENTIAL RULE: If you need credentials, retrieve them from "
            "the vault using vault.retrieve(site). NEVER hardcode credentials."
        )

        return instructions

    async def _store_success_memories(self, manifest: TaskManifest):
        """Store successful navigation patterns in long-term memory."""
        try:
            for st in manifest.sub_tasks:
                if st.status == TaskStatus.COMPLETED and st.result:
                    site = None
                    urls = manifest.entities.get("urls", [])
                    if urls:
                        site = urls[0].replace("https://", "").replace("http://", "").split("/")[0]

                    self.memory.remember(
                        content=f"Successful: {st.description}. "
                                f"Result: {json.dumps(st.result)[:200]}",
                        memory_type="navigation_path",
                        site=site,
                        metadata={"task_intent": manifest.intent.value},
                    )
        except Exception as e:
            logger.warning(f"Failed to store success memories: {e}")

    # ── Orchestrator Tools (available to the LLM reasoning layer) ────────────

    async def credential_check(self, site: str) -> bool:
        """Check if credentials exist. Safe to call from LLM context."""
        return self.vault.exists(site)
