"""
Email Agent — Phase 3 stub.

Will handle:
- Gmail OAuth read/send
- Thread parsing and attachment extraction
- Draft composition matching user's writing style
- Send ONLY after explicit human approval
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class EmailSpecialist:
    """Email task coordinator — Phase 3 implementation."""

    async def execute(
        self,
        instructions: str,
        sub_task_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        logger.warning("EmailSpecialist is a Phase 3 stub")
        return {
            "success": False,
            "error": "Email specialist not yet implemented (Phase 3)",
            "sub_task_id": sub_task_id,
        }
