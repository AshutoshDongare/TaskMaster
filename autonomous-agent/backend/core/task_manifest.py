"""
Core data models for the autonomous agent system.

Every agent communicates using these Pydantic schemas — they are the contract
between all system layers: Intake, Orchestrator, Specialists, Verifier, Output.
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class RiskLevel(str, Enum):
    LOW    = "low"     # Read-only: browse, scrape, download, read email
    MEDIUM = "medium"  # Write but reversible: save draft, write file
    HIGH   = "high"    # Irreversible: send email, make payment, submit form


class TaskIntent(str, Enum):
    BROWSE_AND_ACT     = "browse_and_act"      # Login + take action on website
    DOWNLOAD_DOCUMENT  = "download_document"   # Login + locate + download file
    AGGREGATE_DATA     = "aggregate_data"      # Multi-portal data collection
    RESEARCH_AND_REPLY = "research_and_reply"  # Research + draft + send response
    GENERIC            = "generic"             # Unknown — Orchestrator decides


class TaskStatus(str, Enum):
    PENDING           = "pending"
    PLANNING          = "planning"
    EXECUTING         = "executing"
    AWAITING_APPROVAL = "awaiting_approval"
    RETRYING          = "retrying"
    COMPLETED         = "completed"
    FAILED            = "failed"
    CANCELLED         = "cancelled"


class SubTask(BaseModel):
    id:               str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description:      str
    specialist_team:  str          # "browser" | "document" | "email" | "research"
    tools_required:   List[str]
    depends_on:       List[str] = []    # IDs of sub-tasks that must complete first
    risk_level:       RiskLevel
    status:           TaskStatus = TaskStatus.PENDING
    result:           Optional[Dict[str, Any]] = None
    error:            Optional[str] = None
    attempts:         int = 0
    success_criteria: str = ""          # What "done" looks like — used by Verifier


class TaskManifest(BaseModel):
    id:           str = Field(default_factory=lambda: str(uuid.uuid4()))
    raw_input:    str                   # Original user input, verbatim
    intent:       TaskIntent
    risk_level:   RiskLevel             # Highest risk level across all sub-tasks
    entities:     Dict[str, Any] = {}   # urls, account_names, dates, amounts, doc_types
    constraints:  List[str] = []        # Deadlines, budget limits, conditions
    ambiguities:  List[str] = []        # Questions to ask before starting
    sub_tasks:    List[SubTask] = []
    status:       TaskStatus = TaskStatus.PENDING
    created_at:   datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    result:       Optional[Dict[str, Any]] = None
    audit_trail:  List[Dict[str, Any]] = []


class ApprovalRequest(BaseModel):
    id:           str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id:      str
    sub_task_id:  str
    action:       str
    context:      Dict[str, Any]
    summary:      str               # Plain English: "About to pay $149 to Namecheap..."
    screenshot:   Optional[str] = None   # base64 encoded or file path
    risk_level:   RiskLevel
    status:       str = "pending"   # pending | approved | rejected | modified
    modification: Optional[str] = None  # User's edited version (e.g. revised email draft)
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at:  Optional[datetime] = None
    resolved_by:  Optional[str] = None


class MemoryEntry(BaseModel):
    id:              Optional[int] = None
    site:            Optional[str] = None
    memory_type:     str   # navigation | login_pattern | data_location | failure | preference
    content:         str
    metadata:        Dict[str, Any] = {}
    usefulness_score: float = 1.0
    times_recalled:  int = 0
    created_at:      datetime = Field(default_factory=datetime.utcnow)


class VerificationResult(BaseModel):
    """Result from the Verifier agent."""
    verified:         bool
    confidence:       float = Field(ge=0.0, le=1.0)
    evidence_summary: str
    issues:           List[str] = []
    recommendation:   str = "proceed"  # proceed | retry | replan | escalate


class AgentMessage(BaseModel):
    """Standardized message between agents."""
    sender:      str
    receiver:    str
    msg_type:    str   # instruction | result | error | approval_request | approval_response
    content:     Dict[str, Any]
    task_id:     Optional[str] = None
    sub_task_id: Optional[str] = None
    timestamp:   datetime = Field(default_factory=datetime.utcnow)
