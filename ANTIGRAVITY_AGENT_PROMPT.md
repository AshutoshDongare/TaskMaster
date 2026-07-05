# AUTONOMOUS PERSONAL AGENT SYSTEM
## Complete Implementation Prompt — Paste this entire document to start

> **Instructions for the coding agent:** Read this document fully before writing a single line of code. All architectural decisions are pre-made. Your job is implementation, not design. Build Phase 1 completely and test it on a real task before touching Phase 2.

---

## MISSION

Build a fully autonomous, personal LLM-powered agent system that accepts open-ended tasks in natural language and executes them end-to-end with minimal human intervention.

**Real task examples this system must handle:**

1. "I got an email from Namecheap — my domain expires in 7 days. Log in and renew it."
2. "Download my Form 26AS from the income tax portal for FY 2024-25."
3. "Give me my total portfolio balance across my Zerodha, Groww, and HDFC Securities accounts."
4. "I got an email from Ravi asking about our refund policy. Research it and draft a reply."

**System must be:**
- **Safe** — never take an irreversible action (payment, send email, form submit) without explicit human approval
- **Auditable** — every action, every LLM decision, every approval logged with timestamp and reasoning
- **Extensible** — new task types and specialist agents added without redesigning the core
- **Credential-secure** — passwords never passed to LLMs, never logged, always encrypted at rest
- **Self-improving** — successful navigation paths stored in memory, recalled on future tasks

---

## ARCHITECTURE — TWO-LEVEL DESIGN

**Core principle:** Generalization lives at the Orchestrator level. Specialization lives at the execution level.

```
USER TASK (natural language)
         │
         ▼
┌─────────────────────┐
│    INTAKE AGENT     │  Parses intent, extracts entities, scores risk,
│                     │  normalizes to TaskManifest schema
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────┐
│                  ORCHESTRATOR AGENT                     │
│                                                         │
│  ┌─────────────┐    ReAct Loop:                        │
│  │   PLANNER   │    Think → Act → Observe → Repeat     │
│  └─────────────┘                                        │
│                                                         │
│  Receives TaskManifest → Decomposes into sub-tasks      │
│  → Spawns specialist agents → Handles failures          │
│  → Manages dependencies → Aggregates results            │
└──────────────────────────┬──────────────────────────────┘
                           │  ◄──────────────── MEMORY SYSTEM
                           │                    (Redis + ChromaDB
                           │                     + Credential Vault)
                ┌──────────┴──────────┐
                │  SPECIALIST TEAMS   │  (spawned dynamically)
                └──────────┬──────────┘
         ┌─────────────────┼─────────────────────┐
         ▼                 ▼                      ▼                    ▼
  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐   ┌──────────────────┐
  │BROWSER TEAM │  │  DOC TEAM   │  │  EMAIL TEAM  │   │  RESEARCH TEAM   │
  │             │  │             │  │              │   │                  │
  │ Login flows │  │PDF/Excel    │  │Read threads  │   │Web search        │
  │ Navigation  │  │OCR parsing  │  │Draft replies │   │Source synthesis  │
  │ Form fill   │  │Report build │  │Send (gated)  │   │Fact checking     │
  │ Downloads   │  │Consolidate  │  │              │   │                  │
  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘   └────────┬─────────┘
         └────────────────┴─────────────────┴────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │    SAFETY GATE       │
                           │                      │
                           │  LOW/MEDIUM → pass   │
                           │  HIGH → block, show  │
                           │  user, await approval│
                           └──────────┬───────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │   VERIFIER AGENT     │
                           │                      │
                           │ Independently checks │
                           │ each sub-task result │
                           │ Triggers retry/replan│
                           └──────────┬───────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │   OUTPUT AGENT       │
                           │                      │
                           │ Assembles final      │
                           │ report, delivers     │
                           │ files, sends drafts  │
                           └─────────────────────┘
```

### The ReAct Execution Loop

```python
while task_not_complete:
    thought = llm.reason(task_state, memory_context, available_tools)

    if thought.requires_human_approval:
        screenshot = browser.capture()
        approved = safety_gate.request_approval(thought.action, screenshot)
        if not approved:
            task.cancel("User rejected action")
            break

    result = tool_registry.execute(thought.action, thought.params)
    verified = verifier.check(thought.action, result, success_criteria)

    audit_log.append(thought, result, verified)

    if not verified and task.attempts < 3:
        memory.store_failure(thought.action, result.error)
        orchestrator.replan(task, result.error)
        continue

    elif not verified:
        escalate_to_user(task, result.error, "3 retries failed")
        break

    memory.store_success(thought.action, result)
    task_state.update(result)
```

---

## TECH STACK — ALL OPEN SOURCE

Implement using exactly this stack. Flag any substitution before making it.

| Layer | Technology | Reason |
|---|---|---|
| **Agent Framework** | AutoGen (Microsoft) | Dynamic multi-agent conversations. No pre-wired DAG — Orchestrator decides routing at runtime based on the task |
| **LLM — Reasoning** | Claude API (`claude-sonnet-4-6`) | Orchestration, planning, complex reasoning requiring high accuracy |
| **LLM — Online as well as Local/Fast** | Gemini 3.5 Flash API as well as Ollama + Qwen 3 Reasoning 8B | Classification, formatting, simple extraction. Reduces API cost |
| **Browser Automation** | Playwright + browser-use | LLM-controlled browser. Handles SPAs, login flows, MFA, downloads |
| **Short-term Memory** | Redis | Current task state, session context, agent working memory |
| **Long-term Memory** | ChromaDB | Vector store: navigation patterns, site knowledge, past task outcomes |
| **Structured Store** | PostgreSQL | Task history, audit log, approval records, user preferences |
| **Credential Store** | Fernet (cryptography lib) | Per-site credentials, encrypted at rest. Key from env var only |
| **Document Processing** | PyMuPDF + Unstructured | PDF text and table extraction, OCR on scanned docs, Excel parsing |
| **Task Queue** | Celery + Redis | Async task execution, retries, parallel sub-agent spawning |
| **API Backend** | FastAPI | REST endpoints, webhook ingestion, WebSocket streaming |
| **Email Integration** | Gmail API + imap-tools | Read threads, parse attachments, draft and send after approval |
| **Web Search** | Tavily API | Research tasks. Built for LLM agents, returns clean structured results |
| **Agent UI** | Chainlit | Chat interface with native support for approval flows and agent streaming |
| **Observability** | LangFuse (self-hosted) | Traces every LLM call, tool invocation, agent decision, latency |
| **Containerization** | Docker + Docker Compose | All services run locally in one command |

> **Do NOT use LangGraph.** It requires pre-defined DAGs. Task types are unpredictable at design time — the Orchestrator must decide execution structure at runtime. AutoGen's conversational multi-agent model is the right abstraction.

---

## PROJECT STRUCTURE

Create this exact directory layout. File names are referenced by other sections.

```
autonomous-agent/
├── docker-compose.yml
├── .env.example
├── README.md
│
├── backend/
│   ├── main.py                          # FastAPI app entry point
│   ├── requirements.txt
│   │
│   ├── core/
│   │   ├── task_manifest.py             # Pydantic schemas: TaskManifest, SubTask, enums
│   │   ├── tool_registry.py             # Central registry mapping tool names to callables
│   │   │
│   │   ├── memory/
│   │   │   ├── redis_store.py           # Short-term: task state, session context
│   │   │   ├── chroma_store.py          # Long-term: vector memory, navigation patterns
│   │   │   ├── postgres_store.py        # Structured: task history, audit records
│   │   │   └── credential_vault.py      # Encrypted per-site credential storage
│   │   │
│   │   └── safety/
│   │       ├── safety_gate.py           # Approval gate — blocks HIGH risk actions
│   │       ├── risk_scorer.py           # Classify action risk level from action + context
│   │       └── audit_logger.py          # Append-only action audit log
│   │
│   ├── agents/
│   │   ├── intake_agent.py              # Raw input → structured TaskManifest
│   │   ├── orchestrator_agent.py        # Master coordinator + ReAct loop
│   │   ├── planner_agent.py             # Task decomposition into ordered sub-tasks
│   │   ├── verifier_agent.py            # Independent success verification per sub-task
│   │   ├── output_agent.py              # Assemble + format + deliver final result
│   │   │
│   │   └── specialists/
│   │       │
│   │       ├── browser/
│   │       │   ├── browser_agent.py     # LLM-controlled Playwright browser (via browser-use)
│   │       │   ├── login_handler.py     # Login flows, MFA handling, session management
│   │       │   ├── navigator.py         # Page navigation, element finding, scroll/wait
│   │       │   └── scraper.py           # Structured data extraction from pages
│   │       │
│   │       ├── document/
│   │       │   ├── doc_agent.py         # Document processing coordinator
│   │       │   ├── pdf_extractor.py     # PyMuPDF: text, tables, metadata extraction
│   │       │   ├── excel_extractor.py   # openpyxl/pandas: Excel and CSV parsing
│   │       │   └── report_generator.py  # Consolidate multi-source data → structured report
│   │       │
│   │       ├── email/
│   │       │   ├── email_agent.py       # Email task coordinator
│   │       │   ├── gmail_reader.py      # OAuth, read threads, extract attachments
│   │       │   ├── draft_composer.py    # Generate reply matching user's writing style
│   │       │   └── sender.py            # Send ONLY after explicit human approval
│   │       │
│   │       └── research/
│   │           ├── research_agent.py    # Research coordinator
│   │           ├── web_searcher.py      # Multi-query Tavily strategy
│   │           └── synthesizer.py       # Consolidate findings, detect contradictions
│   │
│   ├── api/
│   │   ├── routes/
│   │   │   ├── tasks.py                 # POST/GET /tasks
│   │   │   ├── approvals.py             # GET/POST /approvals
│   │   │   ├── credentials.py           # CRUD /credentials
│   │   │   └── audit.py                 # GET /audit/log
│   │   └── websocket.py                 # Real-time task progress stream
│   │
│   └── workers/
│       ├── celery_app.py                # Celery + Redis configuration
│       └── task_worker.py               # Async task execution entry point
│
├── frontend/
│   ├── chainlit_app.py                  # Chainlit UI entry point
│   └── components/
│       ├── approval_card.py             # Approval UI with screenshot + edit capability
│       └── task_progress.py             # Real-time task timeline display
│
└── infra/
    ├── postgres/
    │   └── init.sql                     # Full database schema
    └── redis/
        └── redis.conf
```

---

## CORE DATA MODELS

Implement these Pydantic models exactly as specified. Every agent communicates using these schemas — they are the contract between all system layers.

```python
# backend/core/task_manifest.py

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
    entities:     Dict[str, Any] = {}  # urls, account_names, dates, amounts, doc_types
    constraints:  List[str] = []       # Deadlines, budget limits, conditions
    ambiguities:  List[str] = []       # Questions to ask before starting
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
```

---

## AGENT SYSTEM PROMPTS

### Intake Agent

```python
# backend/agents/intake_agent.py

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
"""
```

### Orchestrator Agent

```python
# backend/agents/orchestrator_agent.py

ORCHESTRATOR_SYSTEM_PROMPT = """
You are the Orchestrator — the master coordinator of an autonomous agent system.
You receive structured task manifests and are responsible for completing them safely.

YOUR DECISION LOOP — run continuously until task is COMPLETED or FAILED:

STEP 1 — ASSESS
  Read the current task state. What has been verified complete? What remains?
  What context from memory is relevant? Any failures to account for in replanning?

STEP 2 — PLAN (if sub_tasks not yet generated)
  Decompose the task into atomic sub-tasks. For each sub-task define:
  - description: precise instruction for the specialist agent
  - specialist_team: exactly one of browser | document | email | research
  - tools_required: specific tools this agent will need
  - depends_on: IDs of sub-tasks that must succeed before this can run
  - risk_level: low | medium | high
  - success_criteria: exact description of what verified completion looks like

  Mark sub-tasks that can run in PARALLEL (no shared dependencies).
  The Orchestrator can spawn multiple browser agents simultaneously.

STEP 3 — DISPATCH
  Select the next executable sub-task (all dependencies verified complete).
  Call spawn_specialist(team, instructions, sub_task_id) with precise instructions.
  Do not dispatch a sub-task whose dependencies have not been verified.

STEP 4 — OBSERVE
  Receive result from specialist. Check Verifier's independent assessment.
  Do NOT mark a sub-task complete based on specialist self-reporting alone.
  Only Verifier confirmation counts as success.

STEP 5 — HANDLE FAILURE
  Attempt 1: Retry same approach with more specific instructions
  Attempt 2: Alternative approach (different navigation path, different search terms,
             different element selectors, try mobile site instead of desktop)
  Attempt 3: Escalate to user with specific question:
             "I failed to [action] 3 times. Error: [error]. Can you [specific ask]?"

STEP 6 — ESCALATE TO HUMAN immediately when:
  - Any HIGH risk action is about to execute (always, no exceptions)
  - 3 retries failed on any sub-task
  - New information contradicts the task (price different, form not found, account different)
  - You encounter a decision where being wrong has irreversible consequences
  - You are uncertain about which of two paths to take on a HIGH risk path

STEP 7 — COMPLETE
  All sub-tasks verified complete. Call output_agent.assemble(task_manifest).
  Only then mark task status COMPLETED.

TOOLS AVAILABLE TO YOU DIRECTLY:
  memory_retrieve(query, site=None)          → relevant past navigation patterns
  credential_check(site)                     → True/False, never returns the value
  spawn_specialist(team, instructions, id)   → launch specialist sub-agent
  human_approval_request(action, summary, screenshot, context) → True/False
  audit_log(event, detail, risk_level)       → append to audit trail
  update_sub_task(id, status, result, error) → update task state

MANDATORY RULES — never violate these:
  1. Never assume credentials exist. Always call credential_check() first.
     If not found, stop and tell the user which credentials to add.
  2. Never execute HIGH risk actions without human_approval_request() returning True.
  3. Always dispatch Verifier after each sub-task completes.
  4. Log every significant decision with your reasoning via audit_log().
  5. Never pass credentials, passwords, or API keys in specialist instructions.
     Say "retrieve credentials for [site] from the vault" — never the actual value.
  6. A paused task is always better than a wrong irreversible action.
"""

ORCHESTRATOR_REPLAN_PROMPT = """
Sub-task {sub_task_id} has failed {attempts} times.
Description: {description}
Error: {error}
Evidence: {evidence}

Analyze why this failed and decide:

A) ALTERNATIVE APPROACH — describe a different way to accomplish this sub-task
   (different navigation path, different search terms, different page element)

B) SKIP AND REROUTE — if this sub-task is not critical, describe how to reach the
   goal through a different sequence of steps

C) ESCALATE — if you cannot determine an alternative, what specific question would
   you ask the user to unblock this? Be precise — not "I need help" but
   "I cannot find the renewal button. Can you provide the direct URL to the renewal page?"

Current task state:
{task_state_json}

Respond with your choice (A, B, or C) and the specific next action.
"""
```

### Browser Specialist Agent

```python
# backend/agents/specialists/browser/browser_agent.py
# Implementation: uses browser-use library wrapping Playwright
# from browser_use import Agent, Browser
# browser = Browser()
# agent = Agent(task=instructions, llm=llm, browser=browser)

BROWSER_AGENT_SYSTEM_PROMPT = """
You are a Browser Specialist. You control a real web browser to complete tasks
on websites exactly as a human would. You see the current page state (screenshot
+ DOM summary) and decide what action to take next.

AVAILABLE BROWSER ACTIONS:
  navigate(url)                         Go to a specific URL
  click(element_description)            Click an element by description
  type(element_description, text)       Fill in a text field
  select(element_description, value)    Choose a dropdown option
  scroll(direction, pixels)             Scroll the page
  wait(seconds)                         Wait for page load or element to appear
  screenshot()                          Capture current page state
  extract_text(element_description)     Read text from a specific element
  get_page_elements()                   Get structured list of interactive elements
  download_file(element_description)    Click download link and save file
  get_downloads_dir()                   Return path to downloads directory

EXECUTION PATTERN for every action:
  1. Look at current page state (screenshot + element list)
  2. Identify the target element by its visible text or position
  3. Take the action
  4. Verify the action had the expected effect
  5. If unexpected result: describe exactly what you see, try alternative

LOGIN PROCEDURE — follow exactly, no shortcuts:
  1. navigate(site_url)
  2. Retrieve credentials: call vault.get_credentials(site) — NEVER hardcode
  3. Locate username/email field — type(field, username)
  4. Locate password field — type(field, password)
  5. Click submit/login button
  6. Wait for page response
  7. Check for MFA prompt:
     - SMS code: PAUSE — request code from user via orchestrator
     - TOTP (Google Auth): PAUSE — request current code from user
     - Email OTP: PAUSE — tell user to check email and provide code
  8. Verify login success: look for account name, dashboard, or user menu
  9. If login failed after 1 attempt: STOP — escalate to orchestrator
     Do NOT retry login more than once (account lockout risk)

PAYMENT / CHECKOUT PROCEDURE — never skip any step:
  1. Navigate to cart/checkout as instructed
  2. Verify the item, amount, and recipient on screen
  3. screenshot() — capture the checkout summary
  4. STOP HERE — do not click pay/confirm/submit
  5. Return to orchestrator:
     { action: "payment_pending_approval",
       amount: extracted_amount,
       recipient: extracted_recipient,
       item: extracted_item,
       screenshot: screenshot_base64 }
  6. Wait for APPROVED signal from Safety Gate
  7. Only after approval: click the pay/confirm button
  8. screenshot() — capture the confirmation/receipt page
  9. Extract: confirmation number, receipt details, new status
  10. Return all evidence as result

DOWNLOAD PROCEDURE:
  1. Navigate to documents/statements section
  2. Use filters or search to locate the specific document
  3. screenshot() — confirm correct document is identified
  4. click(download_button) or download_file(link)
  5. wait(3) — allow download to complete
  6. Verify file exists in downloads directory and is not empty
  7. Return: { file_path, file_size_bytes, document_name, screenshot }

DATA EXTRACTION PROCEDURE (portfolio values, balances, etc.):
  1. Navigate to the page containing the data
  2. screenshot() — capture the data as displayed
  3. extract_text() for each data point required
  4. Return structured data: { field_name: extracted_value, ... }
  5. Include confidence level if any extraction was uncertain

IMPORTANT RULES:
  - You are operating in a real browser on real websites with real consequences
  - When uncertain about any action, screenshot and report back — never guess
  - Never proceed past a payment confirmation screen without approval signal
  - If a page looks unexpected (wrong site, error page, CAPTCHA), report immediately
  - Do not close the browser between steps — session must persist across the task
"""
```

### Verifier Agent

```python
# backend/agents/verifier_agent.py

VERIFIER_SYSTEM_PROMPT = """
You are the Verifier. Your only job is to independently confirm that a completed
sub-task actually succeeded. You are professionally skeptical.

You receive:
  - intended_goal: what the sub-task was supposed to accomplish
  - claimed_result: what the specialist agent reported
  - evidence: screenshots (base64), file paths, extracted text, API responses

VERIFICATION METHODS BY TYPE:

Browser / web action tasks:
  - Compare before/after screenshots — did the page actually change as expected?
  - Look for explicit confirmation messages, success banners, receipt numbers
  - Look for error messages the agent may have ignored or missed
  - For downloads: verify file exists at claimed path, is not empty (0 bytes),
    and is the correct file type (PDF not HTML error page)
  - For logins: verify account name or dashboard is visible, not the login page again
  - For payments: verify confirmation number exists on receipt screenshot

Document processing tasks:
  - Are extracted numerical values plausible? (portfolio value in expected range?)
  - If multiple values extracted: do the parts sum to the reported total?
  - Are dates in valid format and plausible for context (FY 2024-25 not FY 2019-20)?
  - Flag any extraction with clear OCR artifacts or garbled text

Email tasks:
  - For drafts: verify status is DRAFT, not SENT
  - For sent: verify sent folder confirmation received, not just "send clicked"
  - Does draft content match what was requested?

Research tasks:
  - Do the cited sources actually say what the agent claims?
  - Are there contradictions between sources that need flagging?
  - Is the information current? Check publication dates.

OUTPUT — respond with only this JSON:
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

A false negative (unnecessary retry) is always preferred over a false positive
(treating a failed action as successful). When in doubt, recommend retry.
"""
```

### Output Agent

```python
# backend/agents/output_agent.py

OUTPUT_AGENT_SYSTEM_PROMPT = """
You are the Output Agent. You receive the completed TaskManifest with all
verified sub-task results and assemble a clear, useful final report for the user.

YOUR RESPONSIBILITIES:
  1. Synthesize all sub-task results into a coherent narrative
  2. Present the most important information first
  3. Include all file paths, confirmation numbers, receipt details
  4. Flag anything that was partially successful or uncertain
  5. List any follow-up actions the user should be aware of
  6. Format according to user preferences from memory

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

For AGGREGATE DATA tasks (portfolio reports):
  Present: total value, per-platform breakdown, top holdings,
  day's change, and any extraction warnings per platform.

For RESEARCH AND REPLY tasks:
  Present: summary of findings, sources used, draft email status
  (sent/pending approval), and any caveats on information accuracy.

For BROWSE AND ACT tasks:
  Present: action taken, confirmation received, screenshot evidence,
  and next expected status (e.g. "domain now renewed until [date]").

For DOWNLOAD tasks:
  Present: file name, save location, file size, and key data extracted
  from the document (e.g. total TDS from Form 26AS).
"""
```

---

## MEMORY SYSTEM

```python
# backend/core/memory/credential_vault.py

from cryptography.fernet import Fernet
import json, os

class CredentialVault:
    """
    Credentials encrypted at rest using Fernet symmetric encryption.
    Encryption key loaded from VAULT_KEY environment variable ONLY.
    Never stored in database. Never in code. Never in logs.

    CRITICAL: The LLM never receives credential values. It instructs the
    browser to "use stored credentials for site X". The vault decrypts
    and passes to Playwright internals only.

    Usage:
        vault = CredentialVault()
        vault.store("namecheap.com", {"username": "user@email.com", "password": "..."})
        creds = vault.retrieve("namecheap.com")  # Returns to Playwright only
        exists = vault.exists("namecheap.com")    # Safe to return to LLM
        sites = vault.list_sites()                # Site names only, no passwords
    """

    def __init__(self):
        key = os.environ["VAULT_KEY"].encode()
        self.fernet = Fernet(key)
        self.vault_path = os.environ.get("VAULT_PATH", "./vault/credentials.enc")

    def store(self, site: str, credentials: dict): ...
    def retrieve(self, site: str) -> dict | None: ...
    def exists(self, site: str) -> bool: ...
    def list_sites(self) -> list[str]: ...
    def delete(self, site: str): ...


# backend/core/memory/chroma_store.py

class LongTermMemory:
    """
    Semantic vector memory using ChromaDB.

    WHAT GETS STORED (only after Verifier confirms success):

    Type: navigation_path
      Content: "To renew domain on Namecheap: Login > Domain List >
                Find domain > Click Renew > Add to Cart > Checkout > Confirm"
      Site: "namecheap.com"

    Type: login_pattern
      Content: "Zerodha login: zerodha.com/login > Enter user_id (not email) >
                Password > TOTP required via Google Authenticator app.
                Dashboard shows 'Holdings' tab in left nav."
      Site: "zerodha.com"

    Type: data_location
      Content: "Portfolio value on Groww: After login, Dashboard page shows
                'Current Value' in bold at top. Stocks tab shows individual holdings."
      Site: "groww.in"

    Type: failure_pattern
      Content: "FAILURE: Searching 'tax documents' on HDFC Bank portal returns no results.
                CORRECT: Use 'eStatements' in the Accounts menu.
                Path: Accounts > eStatements > Year dropdown > Download PDF"
      Site: "hdfcbank.com"

    Type: user_preference
      Content: "User prefers portfolio values in INR with comma formatting (₹1,23,456).
                Reports should include % change from previous month if available.
                Deliver results in Chainlit chat, not by email."
      Site: None (global)

    RECALL: Automatically search and inject relevant memories into agent
    context at the start of each sub-task involving a known site.
    """

    def remember(self, content: str, memory_type: str,
                 site: str = None, metadata: dict = {}): ...
    def recall(self, query: str, n_results: int = 5,
               site_filter: str = None) -> list[dict]: ...
    def update_usefulness(self, memory_id: str, was_helpful: bool): ...
```

---

## SAFETY GATE — COMPLETE IMPLEMENTATION

```python
# backend/core/safety/safety_gate.py

class SafetyGate:
    """
    Every agent action passes through here before execution.
    LOW / MEDIUM risk: log and pass through automatically.
    HIGH risk: block, generate approval request, wait for user decision.
    """

    # Actions always classified HIGH risk regardless of context
    HIGH_RISK_ACTIONS = {
        "make_payment", "confirm_payment", "click_pay",
        "click_confirm", "click_submit", "click_send",
        "send_email", "send_message",
        "submit_form", "submit_application",
        "delete_file", "delete_account",
        "wire_transfer", "bank_transfer",
        "renew_subscription",  # involves payment
        "book_appointment",    # if involves payment
        "place_order",
    }

    # Actions always LOW risk
    LOW_RISK_ACTIONS = {
        "navigate", "screenshot", "scroll", "wait",
        "extract_text", "get_page_elements",
        "read_email", "search_web",
        "retrieve_credential",  # vault retrieval, not the value itself
        "get_balance", "get_portfolio_value",
    }

    async def check(self, action: str, context: dict,
                    task: TaskManifest) -> bool:
        """
        Returns True if action can proceed, False if blocked/rejected.
        Suspends execution on HIGH risk until human responds.
        """
        risk = self.classify_risk(action, context)
        self.audit_logger.log(action, context, risk, task.id)

        if risk == RiskLevel.HIGH:
            # Generate human-readable plain English summary
            summary = await self._generate_summary(action, context)

            request = ApprovalRequest(
                task_id      = task.id,
                sub_task_id  = context.get("sub_task_id", "unknown"),
                action       = action,
                context      = self._sanitize_context(context),  # remove any creds
                summary      = summary,
                screenshot   = context.get("screenshot"),
                risk_level   = risk,
            )

            # Save to database
            await self.db.save_approval_request(request)

            # Notify user immediately — don't make them poll
            await self.notifier.send(
                channel  = "chainlit",
                message  = f"⚠️ Action requires your approval: {summary}",
                approval_id = request.id,
            )
            if os.environ.get("SLACK_WEBHOOK_URL"):
                await self.notifier.send_slack(summary, request.id)

            # Suspend here — wait for user decision via API
            approved, modification = await self._await_decision(
                request.id,
                timeout_minutes=30
            )

            if not approved:
                self.audit_logger.log("approval_rejected", {"request_id": request.id,
                                      "summary": summary}, RiskLevel.HIGH, task.id)
                return False

            if modification:
                context["user_modification"] = modification

            self.audit_logger.log("approval_granted", {"request_id": request.id},
                                  RiskLevel.HIGH, task.id)
            return True

        # LOW / MEDIUM: proceed automatically
        return True

    async def _generate_summary(self, action: str, context: dict) -> str:
        """
        Uses LLM to generate a clear, specific plain-English description.
        Examples of required output quality:
          "About to pay ₹1,499.00 to Namecheap to renew domain example.com for 1 year."
          "About to send an email to priya@acme.com with subject 'Re: Refund policy'."
          "About to submit your income tax return for FY 2024-25 on incometax.gov.in."
          "About to delete 47 files from the Downloads folder."
        """
        pass

    def _sanitize_context(self, context: dict) -> dict:
        """Remove any credential values before storing in database."""
        FORBIDDEN_KEYS = {"password", "secret", "token", "api_key", "credential"}
        return {k: "[REDACTED]" if any(f in k.lower() for f in FORBIDDEN_KEYS)
                else v for k, v in context.items()}
```

### Risk Classification Rules

| Risk Level | Action Types | System Behavior |
|---|---|---|
| **LOW** | navigate, read, screenshot, extract, download, search, read_email, get_balance | Log and proceed automatically. No user notification. |
| **MEDIUM** | save_draft, write_file, save_to_vault, create_report, stage_email | Log and proceed. Send async notification: "Agent saved a draft to your vault." |
| **HIGH** | make_payment, send_email, submit_form, click_confirm, click_delete | BLOCK. Generate ApprovalRequest. Notify user. Wait up to 30 minutes. Cancel on timeout. |

---

## API ENDPOINTS

```python
# backend/api/routes/tasks.py

# POST /tasks
# Submit a new task. Execution is asynchronous via Celery.
# Request body: { "description": str, "email_context": str | null }
# Response: { "task_id": uuid, "status": "pending", "ambiguities": list }
# If ambiguities exist: return them for user to answer before execution starts

# GET /tasks/{task_id}
# Get full TaskManifest including all sub-tasks, statuses, and results.

# GET /tasks
# List tasks. Query params: status, intent, date_from, date_to, limit, offset

# GET /approvals/pending
# All tasks currently awaiting human decision, with full context and screenshots.

# POST /approvals/{approval_id}/approve
# Approve the pending action. Task execution resumes immediately.

# POST /approvals/{approval_id}/reject
# Reject. Task is cancelled. Reason stored in audit log.

# POST /approvals/{approval_id}/modify
# Modify the content then approve (e.g. edit email draft before sending).
# Request body: { "modification": str }

# GET /credentials
# List sites with stored credentials. Returns site names ONLY — no usernames, no passwords.

# POST /credentials
# Store credentials for a site.
# Request body: { "site": str, "username": str, "password": str }

# DELETE /credentials/{site}
# Remove stored credentials for a site.

# GET /audit/log
# Full audit trail. Query params: task_id, agent_name, risk_level, date_from, date_to

# WebSocket: WS /ws/tasks/{task_id}
# Real-time event stream. Events emitted:

WEBSOCKET_EVENTS = {
    "sub_task_started":    {"sub_task_id", "description", "team"},
    "browser_action":      {"action", "element", "url", "screenshot"},
    "sub_task_completed":  {"sub_task_id", "verified", "confidence"},
    "approval_required":   {"approval_id", "summary", "screenshot", "risk_level"},
    "approval_resolved":   {"approval_id", "decision", "modification"},
    "sub_task_failed":     {"sub_task_id", "error", "attempts", "will_retry"},
    "task_completed":      {"result", "files", "report_markdown"},
    "task_failed":         {"error", "last_sub_task", "audit_trail_id"},
}
```

---

## DATABASE SCHEMA

```sql
-- infra/postgres/init.sql

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Core task record
CREATE TABLE tasks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_input    TEXT NOT NULL,
    intent       VARCHAR(50),
    risk_level   VARCHAR(20),
    status       VARCHAR(30) NOT NULL DEFAULT 'pending',
    entities     JSONB DEFAULT '{}',
    constraints  JSONB DEFAULT '[]',
    ambiguities  JSONB DEFAULT '[]',
    sub_tasks    JSONB DEFAULT '[]',
    result       JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    user_id      VARCHAR(100) DEFAULT 'default'
);

-- Immutable audit trail — append only, enforce at application AND trigger level
CREATE TABLE audit_log (
    id             BIGSERIAL PRIMARY KEY,
    task_id        UUID REFERENCES tasks(id),
    sub_task_id    VARCHAR(50),
    agent_name     VARCHAR(100) NOT NULL,
    action         VARCHAR(200) NOT NULL,
    action_detail  JSONB DEFAULT '{}',
    risk_level     VARCHAR(20),
    result         VARCHAR(20),   -- success | failure | blocked | approved | rejected
    result_detail  JSONB DEFAULT '{}',
    screenshot_path TEXT,
    reasoning      TEXT,          -- LLM's stated reasoning for this action
    timestamp      TIMESTAMPTZ DEFAULT NOW()
);

-- Prevent any modification to audit records
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only. UPDATE and DELETE are forbidden.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_immutability
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

-- Human approval requests
CREATE TABLE approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID REFERENCES tasks(id),
    sub_task_id     VARCHAR(50),
    action          VARCHAR(200) NOT NULL,
    context         JSONB NOT NULL,
    summary         TEXT NOT NULL,
    screenshot_path TEXT,
    risk_level      VARCHAR(20),
    status          VARCHAR(20) DEFAULT 'pending',  -- pending|approved|rejected|modified|timed_out
    modification    TEXT,
    requested_at    TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     VARCHAR(100),
    timeout_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '30 minutes'
);

-- Long-term memory (structured metadata; actual vectors in ChromaDB)
CREATE TABLE memories (
    id               BIGSERIAL PRIMARY KEY,
    chroma_id        VARCHAR(100) UNIQUE,
    site             VARCHAR(200),
    memory_type      VARCHAR(50),  -- navigation|login_pattern|data_location|failure|preference
    content          TEXT NOT NULL,
    metadata         JSONB DEFAULT '{}',
    usefulness_score FLOAT DEFAULT 1.0,
    times_recalled   INT DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    last_recalled_at TIMESTAMPTZ
);

-- User preferences
CREATE TABLE user_preferences (
    id         BIGSERIAL PRIMARY KEY,
    key        VARCHAR(100) UNIQUE NOT NULL,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_tasks_status       ON tasks(status);
CREATE INDEX idx_tasks_created      ON tasks(created_at DESC);
CREATE INDEX idx_tasks_user         ON tasks(user_id);
CREATE INDEX idx_audit_task         ON audit_log(task_id);
CREATE INDEX idx_audit_timestamp    ON audit_log(timestamp DESC);
CREATE INDEX idx_approvals_status   ON approvals(status);
CREATE INDEX idx_approvals_timeout  ON approvals(timeout_at) WHERE status = 'pending';
CREATE INDEX idx_memories_site      ON memories(site);
CREATE INDEX idx_memories_type      ON memories(memory_type);

-- Default preferences
INSERT INTO user_preferences (key, value) VALUES
    ('currency_format',       'INR'),
    ('number_locale',         'en-IN'),
    ('report_format',         'markdown'),
    ('notification_channel',  'chainlit'),
    ('approval_timeout_mins', '30'),
    ('parallel_browsers_max', '3');
```

---

## DOCKER COMPOSE

```yaml
# docker-compose.yml

version: '3.9'

services:

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB:       agentdb
      POSTGRES_USER:     agent
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./infra/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent -d agentdb"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: [redis_data:/data]

  chromadb:
    image: chromadb/chroma:latest
    ports: ["8001:8000"]
    volumes: [chroma_data:/chroma/chroma]

  langfuse:
    image: langfuse/langfuse:latest
    environment:
      DATABASE_URL:    postgresql://agent:${POSTGRES_PASSWORD}@postgres:5432/agentdb
      NEXTAUTH_SECRET: ${LANGFUSE_SECRET}
      NEXTAUTH_URL:    http://localhost:3000
    ports: ["3000:3000"]
    depends_on:
      postgres:
        condition: service_healthy

  backend:
    build: ./backend
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://agent:${POSTGRES_PASSWORD}@postgres:5432/agentdb
      - REDIS_URL=redis://redis:6379
      - CHROMA_URL=http://chromadb:8001
    volumes:
      - ./downloads:/app/downloads        # All downloaded files
      - ./screenshots:/app/screenshots    # Action evidence screenshots
      - ./vault:/app/vault                # Encrypted credential storage
    ports: ["8000:8000"]
    depends_on:
      postgres:
        condition: service_healthy
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

  celery_worker:
    build: ./backend
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://agent:${POSTGRES_PASSWORD}@postgres:5432/agentdb
      - REDIS_URL=redis://redis:6379
    volumes:
      - ./downloads:/app/downloads
      - ./screenshots:/app/screenshots
      - ./vault:/app/vault
    depends_on: [postgres, redis]
    command: celery -A workers.celery_app worker --loglevel=info --concurrency=4

  chainlit_ui:
    build: ./frontend
    environment:
      - BACKEND_URL=http://backend:8000
    ports: ["8080:8080"]
    depends_on: [backend]
    command: chainlit run chainlit_app.py --host 0.0.0.0 --port 8080

volumes:
  postgres_data:
  redis_data:
  chroma_data:
```

---

## ENVIRONMENT VARIABLES

```bash
# .env.example — copy to .env and populate all values before running

# === LLM ===
ANTHROPIC_API_KEY=sk-ant-...

# === Database ===
POSTGRES_PASSWORD=choose_a_strong_password

# === Credential Vault Encryption ===
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
VAULT_KEY=
VAULT_PATH=./vault/credentials.enc

# === LangFuse Observability ===
# Generate secret: openssl rand -hex 32
LANGFUSE_SECRET=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# === External APIs ===
TAVILY_API_KEY=tvly-...           # https://tavily.com — web search for research tasks

# === Gmail OAuth (for email agent) ===
# Setup: https://developers.google.com/gmail/api/quickstart/python
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=

# === Notifications (optional but recommended) ===
# When an approval is required, the agent sends a Slack message so you don't have to poll
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# === Local LLM fallback (optional) ===
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.3
```

---

## COMPLETE TASK FLOW WALKTHROUGHS

### Flow 1: Domain Renewal (HIGH risk — payment)

```
User input: "I got an email from Namecheap. My domain example.com expires in 7 days. Renew it."

── INTAKE AGENT ──────────────────────────────────────────────────────────────
  Extracts:
    intent:      browse_and_act
    risk_level:  HIGH (payment required)
    entities:    { site: "namecheap.com", domain: "example.com", action: "renew" }
  Checks:
    credential_check("namecheap.com") → True (credentials exist)
  Outputs: TaskManifest with no ambiguities — proceeds to Orchestrator

── ORCHESTRATOR ──────────────────────────────────────────────────────────────
  Recalls from memory: "Namecheap renewal: Domain List > find domain > Add to Cart"
  Creates sub-tasks:
    ST-01: [browser] Login to namecheap.com [LOW]
    ST-02: [browser] Navigate to Domain List, find example.com [LOW] ← depends ST-01
    ST-03: [browser] Click Renew, reach checkout page [MEDIUM] ← depends ST-02
    ST-04: [browser] STOP at payment confirmation, return screenshot [HIGH] ← depends ST-03
    ST-05: [browser] Complete payment after approval [HIGH] ← depends ST-04 + APPROVAL
    ST-06: [browser] Screenshot receipt, extract confirmation number [LOW] ← depends ST-05
    ST-07: [output] Deliver renewal confirmation report [LOW] ← depends ST-06

── ST-01 through ST-03: Browser Agent executes ──────────────────────────────
  Login: retrieves credentials from vault, types into fields, handles MFA if needed
  Navigation: uses recalled path → Domain List → finds example.com → clicks Renew
  Verifier confirms: domain name matches, renewal option visible, cart shows 1 item

── ST-04: Safety Gate BLOCKS ─────────────────────────────────────────────────
  Screenshot shows: "$15.98 for example.com — 1 Year Extension"
  Safety Gate generates summary: "About to pay $15.98 to Namecheap to renew
    domain example.com for 1 year. Renewal extends expiry to [date]."
  Approval UI shows: summary + checkout screenshot
  Slack notification sent to user

── USER APPROVES ─────────────────────────────────────────────────────────────

── ST-05: Browser Agent clicks Confirm ──────────────────────────────────────
  Clicks pay button, waits for confirmation page
  Verifier: confirmation page screenshot shows "Order Confirmed #12345678"

── Memory update ─────────────────────────────────────────────────────────────
  Stores: "Namecheap renewal flow confirmed working: Domain List > Add to Cart >
           Checkout > Confirm. Takes approx 4 minutes total."

── OUTPUT AGENT ──────────────────────────────────────────────────────────────
  ✅ COMPLETED: Domain example.com renewed successfully

  Renewed: example.com
  New expiry: [date +1 year]
  Amount charged: $15.98
  Confirmation #: 12345678

  Receipt screenshot saved to: ./downloads/namecheap-receipt-[date].png
```

---

### Flow 2: Tax Form Download (LOW risk)

```
User input: "Download my Form 26AS from the income tax portal for FY 2024-25."

── INTAKE AGENT ──────────────────────────────────────────────────────────────
  intent: download_document, risk_level: LOW
  entities: { site: "incometax.gov.in", document: "Form 26AS", year: "FY 2024-25" }

── ORCHESTRATOR ──────────────────────────────────────────────────────────────
  Recalls from memory: "Form 26AS: e-File > Income Tax Returns > View Form 26AS"
  Sub-tasks (all LOW risk, no approval needed):
    ST-01: [browser] Login to incometax.gov.in
    ST-02: [browser] Navigate to Form 26AS, select FY 2024-25, download PDF
    ST-03: [document] Verify PDF, extract total TDS summary
    ST-04: [output] Deliver file + TDS summary

── EXECUTION ─────────────────────────────────────────────────────────────────
  Browser logs in with PAN credentials
  Uses recalled navigation path directly
  Selects FY 2024-25 from dropdown
  Clicks "View as HTML" then "Export as PDF"
  File saved: ./downloads/Form26AS_FY2024-25_[PAN].pdf

── Document Agent extracts ───────────────────────────────────────────────────
  Total TDS deducted: ₹89,450
  Number of deductors: 3
  Assessment Year: 2024-25

── OUTPUT AGENT ──────────────────────────────────────────────────────────────
  ✅ COMPLETED: Form 26AS downloaded for FY 2024-25

  File saved: ./downloads/Form26AS_FY2024-25.pdf (247 KB)

  Summary from document:
  - Total TDS deducted: ₹89,450
  - Number of deductors: 3 (Salary, Bank FD, Consulting)
  - Assessment Year: 2024-25
```

---

### Flow 3: Portfolio Aggregation (PARALLEL execution)

```
User input: "Give me total portfolio value across my Zerodha, Groww, and HDFC Securities."

── ORCHESTRATOR creates PARALLEL sub-tasks ───────────────────────────────────
  ST-01a: [browser] Login Zerodha → extract portfolio value + holdings  ─┐
  ST-01b: [browser] Login Groww → extract portfolio value + holdings     ├─ PARALLEL
  ST-01c: [browser] Login HDFC Securities → extract value + holdings    ─┘
  ST-02:  [document] Consolidate 3 results → report  ← depends ALL of above
  ST-03:  [output] Deliver consolidated report

── Three Browser Agents run simultaneously ───────────────────────────────────
  Each has isolated browser context (no session sharing)
  Each retrieves its own credentials from vault
  Each extracts: current_value, day_change, top_holdings[]

── Document Agent consolidates ──────────────────────────────────────────────
  Zerodha:          ₹4,23,180  (42.1%)
  Groww:            ₹2,98,440  (29.7%)
  HDFC Securities:  ₹2,83,920  (28.2%)
  ─────────────────────────────────────
  TOTAL:            ₹10,05,540

  Top holdings across all: Reliance (8.2%), HDFC Bank (6.7%), Infosys (5.1%)
  Day's change: +₹3,240 (+0.32%)

── Verifier cross-checks ─────────────────────────────────────────────────────
  4,23,180 + 2,98,440 + 2,83,920 = 10,05,540 ✓
  All three extractions: confidence > 0.90 ✓

── OUTPUT AGENT ──────────────────────────────────────────────────────────────
  ✅ COMPLETED: Portfolio report as of [date time]

  Total Portfolio Value: ₹10,05,540

  Platform Breakdown:
  - Zerodha:          ₹4,23,180  (42.1%)
  - Groww:            ₹2,98,440  (29.7%)
  - HDFC Securities:  ₹2,83,920  (28.2%)

  Today's Change: +₹3,240 (+0.32%)

  Top 5 Holdings (across all platforms):
  1. Reliance Industries    ₹82,450  (8.2%)
  2. HDFC Bank              ₹67,370  (6.7%)
  ...
```

---

### Flow 4: Research and Email Reply (HIGH risk — send email)

```
User input: "I got an email from Ravi asking about our data privacy policy. Reply to it."

── INTAKE AGENT ──────────────────────────────────────────────────────────────
  intent: research_and_reply, risk_level: HIGH (will send email)
  entities: { sender: "Ravi", topic: "data privacy policy", action: "reply" }

── EMAIL AGENT reads the thread ──────────────────────────────────────────────
  Extracts Ravi's exact question:
  "Do you store customer data on Indian servers? Are you DPDP Act compliant?"

── RESEARCH AGENT: 4 parallel searches ──────────────────────────────────────
  Search 1: "[company] data storage location India servers"
  Search 2: "[company] DPDP Act compliance 2024"
  Search 3: "[company] privacy policy data retention"
  Search 4: "Digital Personal Data Protection Act 2023 requirements"

── SYNTHESIZER consolidates findings ────────────────────────────────────────
  Fact 1: Data stored on AWS Mumbai region (ap-south-1) — from privacy policy
  Fact 2: DPDP Act compliance in progress — from company blog post
  Fact 3: Retention period: 3 years after account closure — from privacy policy
  Sources: [company privacy policy URL], [company blog URL], [dpdpact.gov.in]

── DRAFT COMPOSER writes reply in user's voice ───────────────────────────────
  Analyzes past emails for style: professional, concise, uses "Hi" not "Dear"

  Draft:
  "Hi Ravi, thanks for reaching out.

  Our customer data is stored on AWS servers located in the Mumbai region
  (ap-south-1), ensuring data residency within India.

  Regarding DPDP Act compliance — we are actively working toward full
  compliance and expect to complete the implementation by Q2. I am happy
  to share our compliance roadmap if that would be helpful.

  Data is retained for 3 years after account closure per our privacy policy.

  Let me know if you have any other questions.

  [Name]"

── SAFETY GATE BLOCKS ────────────────────────────────────────────────────────
  "About to send email to ravi@company.com. Review the draft below."
  Shows full draft in Chainlit with inline editing enabled

── USER EDITS one line, clicks Approve ──────────────────────────────────────

── EMAIL AGENT sends modified draft ─────────────────────────────────────────
  Sends via Gmail API with edited content
  Archives thread with label "agent-handled"

── OUTPUT AGENT ──────────────────────────────────────────────────────────────
  ✅ COMPLETED: Reply sent to Ravi

  Sent to: ravi@company.com
  Subject: Re: Data privacy policy question
  Sent at: [timestamp]

  Key facts cited: AWS Mumbai region, DPDP compliance in progress, 3-year retention
  Sources verified: [list of URLs]
```

---

## BUILD PHASES — IMPLEMENT IN THIS ORDER

### Phase 1 — Foundation (Weeks 1–2)
**Goal:** One complete end-to-end task working before building anything else.
**Test task:** `"Go to namecheap.com and tell me when my domain expires"` (low risk, read-only)

Build in this exact sequence:
1. Docker Compose: Postgres + Redis + ChromaDB running and healthy
2. Core data models: TaskManifest, SubTask, all Enums
3. Credential Vault: Fernet encryption, store/retrieve/list
4. Intake Agent: natural language → TaskManifest (with ambiguity detection)
5. Orchestrator Agent: basic ReAct loop, no replanning yet
6. Browser Agent: Playwright + browser-use, navigate + screenshot + extract
7. Login Handler: form login with MFA pause flow
8. Safety Gate: HIGH risk blocks and creates ApprovalRequest in DB
9. Audit Logger: every action → database row
10. Verifier Agent: screenshot comparison, basic success checks
11. Output Agent: simple result formatting
12. FastAPI: POST /tasks, GET /tasks/{id}, POST /approvals/{id}/approve
13. WebSocket: real-time event stream
14. Chainlit UI: task input + approval card with screenshot display

### Phase 2 — Document Processing & Memory (Week 3)
**Goal:** Portfolio report across multiple portals simultaneously.
**Test task:** `"Log into my Zerodha and Groww accounts and give me total portfolio value"`

1. Document Agent: PyMuPDF, pandas, Excel parsing, table extraction
2. Long-term Memory (ChromaDB): store navigation patterns, recall before each sub-task
3. Parallel sub-task execution: Orchestrator spawns multiple Browser Agents simultaneously
4. Each browser agent gets isolated Playwright context (no session sharing)
5. Report Generator: multi-source data consolidation with cross-checking
6. Verifier upgrade: numerical cross-validation, sum checks

### Phase 3 — Email Agent (Week 4)
**Goal:** Research and reply to an email with full approval flow.
**Test task:** `"I got an email asking about our return policy. Research it and draft a reply."`

1. Gmail OAuth setup: read-only scope first, send scope only after Phase 1 is validated
2. Email Reader: thread parsing, attachment extraction, sender context
3. Research Agent: Tavily multi-query strategy, credibility scoring, deduplication
4. Synthesizer: contradiction detection, source ranking, fact consolidation
5. Draft Composer: style matching from past emails, preference memory injection
6. Approval flow: full draft displayed inline in Chainlit with editing before send
7. Sender: Gmail API send, only after ApprovalRequest.status = "approved"

### Phase 4 — Robustness & Self-Improvement (Weeks 5–6)
**Goal:** System handles failures gracefully and measurably improves over time.

1. Orchestrator replanning: REPLAN_PROMPT invoked after 2nd failed retry
2. LangFuse integration: trace every LLM call, latency, token count, tool invocations
3. Memory quality loop: track which recalled memories actually helped vs. didn't
4. Anomaly detection in Safety Gate: alert if price/form data differs from expectation
5. Celery Beat: scheduled recurring tasks (weekly portfolio report)
6. Approval timeout: auto-cancel after 30 minutes with user notification
7. Task history UI in Chainlit: searchable log, full audit trail per task
8. Push notifications: Slack webhook + optional SMS for approval requests

---

## CRITICAL IMPLEMENTATION RULES

Follow without exception:

**1. Credentials never touch LLM context**
Passwords are never in any prompt, log, screenshot, or audit record. The LLM says "retrieve credentials for site X from the vault." The Playwright layer retrieves the decrypted value and types it into the browser input field. The LLM never sees the value. Enforce with `_sanitize_context()` before any DB write.

**2. Every irreversible action requires a screenshot**
Before the Safety Gate blocks any HIGH risk action, the browser agent must capture a screenshot of the current state. This screenshot is shown in the approval UI and saved to the audit log. No screenshot = no approval request = action not permitted.

**3. Orchestrator plans — specialists execute**
The Orchestrator calls `spawn_specialist()`. It never directly calls Playwright, Gmail API, or file system tools. Specialists execute. This keeps the Orchestrator focused on global task state and able to replan across failures.

**4. Memory writes happen only after Verifier confirmation**
If the Verifier says confidence < 0.75 or recommendation = retry/replan, do NOT write to long-term memory. Only verified successful outcomes are stored. Bad memories make the system worse over time.

**5. Task is not COMPLETED until Output Agent delivers**
Setting task.status = COMPLETED is the Output Agent's job, after it has assembled and delivered the result. The Orchestrator marks it COMPLETED only after output_agent.deliver() returns success.

**6. Audit log is immutable — enforce at the database trigger level**
The PostgreSQL trigger `prevent_audit_modification()` raises an exception on any UPDATE or DELETE on `audit_log`. The application layer should never attempt these operations, but the database enforces it as a hard constraint.

**7. Parallel browser agents get isolated contexts**
`playwright.chromium.launch_persistent_context()` with unique `user_data_dir` per agent. Never share a browser context between two agents. Session bleeding causes authentication failures and race conditions.

**8. 3-retry limit is a hard ceiling**
If `sub_task.attempts >= 3` and the Verifier still says failed, the Orchestrator must escalate to the user with a specific, actionable question — not a generic "it failed" message. Never retry infinitely.

**9. Approval timeout cancels the task**
Create a Celery beat task that checks `approvals` where `status = 'pending'` and `timeout_at < NOW()`. Cancel the associated task, notify the user: "Task cancelled — no response received within 30 minutes. Resubmit when ready."

**10. Test LOW risk before HIGH risk — always**
Before testing any payment or email-send flow, complete at least 10 successful low-risk tasks (read-only browsing, document extraction, credential vault operations). Trust in the safety layer must be earned from real evidence.

---

## QUICK START SEQUENCE

```bash
# 1. Create project directory
mkdir autonomous-agent && cd autonomous-agent

# 2. Set up environment
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY and POSTGRES_PASSWORD at minimum

# 3. Generate vault encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste output as VAULT_KEY in .env

# 4. Start infrastructure
docker-compose up postgres redis chromadb -d
sleep 10  # wait for postgres to initialize

# 5. Install Python dependencies
cd backend
pip install fastapi uvicorn celery redis chromadb anthropic \
    pyautogen playwright browser-use cryptography pymupdf \
    unstructured pandas openpyxl langfuse psycopg2-binary \
    pydantic python-dotenv google-auth-oauthlib tavily-python
playwright install chromium

# 6. Initialize database schema
python -c "from core.db import run_migrations; run_migrations()"

# 7. Start backend (terminal 1)
uvicorn main:app --reload
# Verify: http://localhost:8000/docs shows all routes

# 8. Start Celery worker (terminal 2)
celery -A workers.celery_app worker --loglevel=info

# 9. Start UI (terminal 3)
cd ../frontend
pip install chainlit
chainlit run chainlit_app.py

# 10. Store a test credential
curl -X POST http://localhost:8000/credentials \
  -H "Content-Type: application/json" \
  -d '{"site":"test.example.com","username":"demo","password":"demo123"}'

# 11. Submit first ZERO RISK test task via Chainlit at http://localhost:8080
# Task: "Go to https://httpbin.org/get and tell me what my IP address is"
# This tests the full browser → extract → output pipeline with no credentials, no auth
```

### Services Reference

| Service | URL | Use |
|---|---|---|
| Chainlit (main UI) | http://localhost:8080 | Submit tasks, approve actions, view results |
| FastAPI docs | http://localhost:8000/docs | API testing and debugging |
| LangFuse | http://localhost:3000 | LLM call tracing and agent observability |
| ChromaDB | http://localhost:8001 | Vector memory inspection |
| PostgreSQL | localhost:5432 | Direct DB access for debugging |

---

## RECOMMENDED TEST TASK SEQUENCE

Execute these in order. Do not skip ahead. Each validates a prerequisite for the next.

| # | Task | Risk | Validates |
|---|---|---|---|
| 1 | "Go to httpbin.org/get and tell me my IP address" | ZERO | Browser + extract + output pipeline |
| 2 | "Navigate to example.com and summarize the page" | LOW | Browser navigation |
| 3 | "Log into [low stakes site] and tell me what you see on the dashboard" | LOW | Login flow + credential vault |
| 4 | "Download the terms PDF from [site]" | LOW | Download + file agent |
| 5 | "Search for latest news about [topic] and summarize" | LOW | Research agent + Tavily |
| 6 | "Draft an email to test@example.com with subject Test and body Hello" | MEDIUM | Draft composer + approval UI |
| 7 | "Send the draft email from task 6" | HIGH | Full approval gate + Gmail send |
| 8 | "Log into Zerodha and tell me my total invested amount" | LOW | Real financial portal |
| 9 | "Give me portfolio across Zerodha and Groww" | LOW | Parallel agents |
| 10 | "Renew my domain on Namecheap" | HIGH | Payment flow + approval + receipt |

---

*This document is the complete implementation brief. Build Phase 1, validate it works on Tasks 1–7 above, then proceed to Phase 2. The architecture is designed so each phase adds value without requiring rework of prior phases.*
