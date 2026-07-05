"""
Output Agent — Assembles final reports from completed task results.

Receives the completed TaskManifest with all verified sub-task results
and assembles a clear, useful final report for the user.

Only the Output Agent sets task status to COMPLETED.
"""

import os
import json
import logging
from typing import Dict, Any, Optional

import google.generativeai as genai

from core.task_manifest import TaskManifest, TaskIntent, TaskStatus

logger = logging.getLogger(__name__)

OUTPUT_AGENT_SYSTEM_PROMPT = """
You are the Output Agent. You receive the completed TaskManifest with all
verified sub-task results and assemble a clear, useful final report for the user.

YOUR RESPONSIBILITIES:
  1. Synthesize all sub-task results into a coherent narrative
  2. Present the most important information first
  3. Include all file paths, confirmation numbers, receipt details
  4. Flag anything that was partially successful or uncertain
  5. List any follow-up actions the user should be aware of
  6. Format in clean markdown

REPORT STRUCTURE:
  ✅ COMPLETED: [one-line task summary]

  [Main result — the answer to what was asked]

  Details:
  - [Specific data point 1]
  - [Specific data point 2]

  Files saved:
  - [file path and description]

  Confirmation:
  - [receipt number, confirmation code, or verification]

  ⚠️ Note: [anything incomplete, uncertain, or requiring follow-up]

For AGGREGATE DATA tasks:
  Present: total value, per-platform breakdown, top holdings,
  day's change, and any extraction warnings.

For RESEARCH AND REPLY tasks:
  Present: summary of findings, sources used, draft email status,
  and any caveats on information accuracy.

For BROWSE AND ACT tasks:
  Present: action taken, confirmation received, screenshot evidence,
  and next expected status.

For DOWNLOAD tasks:
  Present: file name, save location, file size, and key data extracted.

OUTPUT: Return a JSON object with:
{
  "summary": "one-line summary",
  "report_markdown": "full markdown report",
  "files": ["list of file paths"],
  "key_data": {"structured key findings"},
  "follow_up_actions": ["list of any follow-ups needed"]
}
"""


class OutputAgent:
    """
    Assembles and formats the final task report.
    Only this agent sets task.status = COMPLETED after successful delivery.
    """

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is required for OutputAgent")
        genai.configure(api_key=api_key)
        self.model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

    async def assemble(self, manifest: TaskManifest) -> Dict[str, Any]:
        """
        Assemble the final report from all verified sub-task results.

        Args:
            manifest: TaskManifest with completed sub-tasks

        Returns:
            Dict with summary, report_markdown, files, key_data, follow_up_actions
        """
        # Collect all results
        results_summary = []
        files = []
        for st in manifest.sub_tasks:
            result_info = {
                "sub_task_id": st.id,
                "description": st.description,
                "status": st.status.value,
                "result": st.result,
            }
            if st.error:
                result_info["error"] = st.error
            results_summary.append(result_info)

            # Collect file paths from results
            if st.result and isinstance(st.result, dict):
                if st.result.get("file_path"):
                    files.append(st.result["file_path"])
                if st.result.get("files"):
                    files.extend(st.result["files"])

        # Build prompt for LLM
        user_message = (
            f"Assemble a final report for this task:\n\n"
            f"ORIGINAL REQUEST: {manifest.raw_input}\n"
            f"INTENT: {manifest.intent.value}\n\n"
            f"SUB-TASK RESULTS:\n{json.dumps(results_summary, indent=2, default=str)}\n\n"
            f"FILES: {files}\n"
        )

        try:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=OUTPUT_AGENT_SYSTEM_PROMPT
            )
            response = await model.generate_content_async(user_message)
            response_text = response.text.strip()

            # Parse JSON
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            result = json.loads(response_text)

            # Ensure required keys exist
            result.setdefault("summary", manifest.raw_input)
            result.setdefault("report_markdown", "Task completed.")
            result.setdefault("files", files)
            result.setdefault("key_data", {})
            result.setdefault("follow_up_actions", [])

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"OutputAgent failed to generate report: {e}")
            # Fallback: simple structured report
            result = self._fallback_report(manifest, results_summary, files)

        logger.info(f"OutputAgent assembled report: {result.get('summary', '')[:80]}")
        return result

    def _fallback_report(
        self,
        manifest: TaskManifest,
        results: list,
        files: list,
    ) -> Dict[str, Any]:
        """Generate a basic report without LLM when the call fails."""
        completed = [r for r in results if r["status"] == "completed"]
        failed = [r for r in results if r["status"] == "failed"]

        report_lines = []
        if failed:
            report_lines.append(
                f"⚠️ PARTIALLY COMPLETED: {manifest.raw_input}"
            )
        else:
            report_lines.append(f"✅ COMPLETED: {manifest.raw_input}")

        report_lines.append("")

        for r in completed:
            report_lines.append(f"- ✅ {r['description']}")
            if r.get("result"):
                for k, v in r["result"].items():
                    if k not in ("evidence", "screenshot"):
                        report_lines.append(f"  - {k}: {v}")

        for r in failed:
            report_lines.append(f"- ❌ {r['description']}: {r.get('error', 'unknown')}")

        if files:
            report_lines.append("\nFiles saved:")
            for f in files:
                report_lines.append(f"  - {f}")

        return {
            "summary": manifest.raw_input,
            "report_markdown": "\n".join(report_lines),
            "files": files,
            "key_data": {},
            "follow_up_actions": (
                [f"Review failed sub-tasks: {[r['description'] for r in failed]}"]
                if failed else []
            ),
        }
