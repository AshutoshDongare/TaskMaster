# Autonomous Personal Agent System

A fully autonomous, LLM-powered personal agent system that accepts open-ended tasks in natural language and executes them end-to-end with minimal human intervention.

## What It Does

Give it natural language instructions like:
- *"I got an email from Namecheap — my domain expires in 7 days. Log in and renew it."*
- *"Download my Form 26AS from the income tax portal for FY 2024-25."*
- *"Give me my total portfolio balance across my Zerodha, Groww, and HDFC Securities accounts."*
- *"I got an email from Ravi asking about our refund policy. Research it and draft a reply."*

The system decomposes the task, spawns specialist agents (browser, document, email, research), executes autonomously, and **always asks for your approval before any irreversible action** (payments, sending emails, form submissions).

## Architecture

```
USER TASK → Intake Agent → Orchestrator (ReAct Loop) → Specialist Teams → Safety Gate → Verifier → Output
```

- **Intake Agent**: Parses intent, extracts entities, scores risk
- **Orchestrator**: Master coordinator with Think → Act → Observe → Repeat loop
- **Browser Team**: LLM-controlled Playwright browser (login, navigate, extract, download)
- **Safety Gate**: Blocks HIGH risk actions, requires human approval with screenshot evidence
- **Verifier**: Independently confirms each sub-task actually succeeded
- **Output Agent**: Assembles final report

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Framework | AutoGen (Microsoft) |
| LLM — Reasoning | Claude API (claude-sonnet-4-6) |
| Browser Automation | Playwright + browser-use |
| Short-term Memory | Redis |
| Long-term Memory | ChromaDB |
| Structured Store | PostgreSQL |
| Credential Store | Fernet encryption |
| Task Queue | Celery + Redis |
| API Backend | FastAPI |
| Agent UI | Chainlit |
| Observability | LangFuse |

## Quick Start

### 1. Set up environment

```bash
cd autonomous-agent
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY and POSTGRES_PASSWORD at minimum
```

### 2. Generate vault encryption key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste output as VAULT_KEY in .env
```

### 3. Start infrastructure

```bash
docker-compose up postgres redis chromadb -d
sleep 10  # Wait for postgres to initialize
```

### 4. Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
playwright install chromium
```

### 5. Start backend

```bash
# Terminal 1: FastAPI backend
uvicorn main:app --reload
# Verify: http://localhost:8000/docs

# Terminal 2: Celery worker
celery -A workers.celery_app worker --loglevel=info

# Terminal 3: Chainlit UI
cd ../frontend
pip install -r requirements.txt
chainlit run chainlit_app.py
```

### 6. Store a test credential

```bash
curl -X POST http://localhost:8000/credentials \
  -H "Content-Type: application/json" \
  -d '{"site":"test.example.com","username":"demo","password":"demo123"}'
```

### 7. Submit your first task

Open http://localhost:8080 (Chainlit UI) and try:
> "Go to https://httpbin.org/get and tell me what my IP address is"

## Services

| Service | URL | Use |
|---|---|---|
| Chainlit (main UI) | http://localhost:8080 | Submit tasks, approve actions, view results |
| FastAPI docs | http://localhost:8000/docs | API testing and debugging |
| LangFuse | http://localhost:3000 | LLM call tracing and observability |
| ChromaDB | http://localhost:8001 | Vector memory inspection |
| PostgreSQL | localhost:5432 | Direct DB access for debugging |

## Safety

- **Credentials never touch LLM context** — passwords are decrypted only at the Playwright layer
- **Every irreversible action requires human approval** with screenshot evidence
- **Immutable audit log** — PostgreSQL trigger prevents UPDATE/DELETE on audit records
- **3-retry limit** — failed sub-tasks escalate to user after 3 attempts
- **30-minute approval timeout** — unresponded approvals auto-cancel

## Project Structure

```
autonomous-agent/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── main.py                    # FastAPI entry point
│   ├── core/
│   │   ├── task_manifest.py       # Pydantic data models
│   │   ├── tool_registry.py       # Tool name → callable registry
│   │   ├── memory/                # Redis, ChromaDB, PostgreSQL, Vault
│   │   └── safety/                # Safety Gate, Risk Scorer, Audit Logger
│   ├── agents/
│   │   ├── intake_agent.py        # Natural language → TaskManifest
│   │   ├── orchestrator_agent.py  # Master coordinator + ReAct loop
│   │   ├── planner_agent.py       # Task decomposition
│   │   ├── verifier_agent.py      # Independent verification
│   │   ├── output_agent.py        # Final report assembly
│   │   └── specialists/           # Browser, Document, Email, Research teams
│   ├── api/                       # REST routes + WebSocket
│   └── workers/                   # Celery async execution
├── frontend/
│   ├── chainlit_app.py            # Chat UI
│   └── components/                # Approval cards, progress timeline
└── infra/
    ├── postgres/init.sql          # Database schema
    └── redis/redis.conf
```

## License

Private — Internal use only.
