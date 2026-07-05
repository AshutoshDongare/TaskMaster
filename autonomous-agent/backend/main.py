"""
FastAPI Application Entry Point — Autonomous Agent System.

Routes:
  POST /tasks         — Submit a new task
  GET  /tasks/{id}    — Get task status and results
  GET  /tasks         — List tasks with filters
  GET  /approvals/pending — Pending approval requests
  POST /approvals/{id}/approve — Approve action
  POST /approvals/{id}/reject  — Reject action
  POST /approvals/{id}/modify  — Modify and approve
  GET  /credentials   — List sites with stored credentials
  POST /credentials   — Store credentials for a site
  DELETE /credentials/{site} — Remove credentials
  GET  /audit/log     — Query audit trail
  WS   /ws/tasks/{id} — Real-time task event stream
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from core.memory.postgres_store import PostgresStore
from core.memory.redis_store import RedisStore
from core.memory.chroma_store import LongTermMemory
from core.memory.credential_vault import CredentialVault
from core.safety.audit_logger import AuditLogger
from core.safety.safety_gate import SafetyGate

from api.routes import tasks, approvals, credentials, audit
from api.websocket import router as ws_router

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


# ── Application State (shared services) ────────────────────────────────────

class AppState:
    """Shared application state — initialized on startup, closed on shutdown."""
    db: PostgresStore
    redis: RedisStore
    memory: LongTermMemory
    vault: CredentialVault
    audit_logger: AuditLogger
    safety_gate: SafetyGate


app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown."""
    logger.info("Starting Autonomous Agent System...")

    # Initialize PostgreSQL
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://agent:agent@localhost:5432/agentdb"
    )
    app_state.db = PostgresStore(db_url)
    await app_state.db.connect()
    logger.info("PostgreSQL connected")

    # Initialize Redis
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    app_state.redis = RedisStore(redis_url)
    await app_state.redis.connect()
    logger.info("Redis connected")

    # Initialize ChromaDB
    chroma_url = os.environ.get("CHROMA_URL", "http://localhost:8001")
    app_state.memory = LongTermMemory(chroma_url)
    try:
        app_state.memory.connect()
        logger.info("ChromaDB connected")
    except Exception as e:
        logger.warning(f"ChromaDB connection failed (non-fatal): {e}")

    # Initialize Credential Vault
    try:
        app_state.vault = CredentialVault()
        logger.info("Credential Vault initialized")
    except Exception as e:
        logger.warning(f"Credential Vault initialization failed: {e}")
        app_state.vault = None

    # Initialize Safety System
    app_state.audit_logger = AuditLogger(db=app_state.db)
    app_state.safety_gate = SafetyGate(
        db=app_state.db,
        redis=app_state.redis,
        audit_logger=app_state.audit_logger,
    )
    logger.info("Safety system initialized")

    logger.info("✅ All services started successfully")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down...")
    await app_state.redis.disconnect()
    await app_state.db.disconnect()
    logger.info("Shutdown complete")


# ── FastAPI App ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Autonomous Agent System",
    description="LLM-powered personal agent for autonomous task execution",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow Chainlit UI and development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Make app_state accessible to route handlers
app.state.app_state = app_state

# Register routes
app.include_router(tasks.router, prefix="/tasks", tags=["Tasks"])
app.include_router(approvals.router, prefix="/approvals", tags=["Approvals"])
app.include_router(credentials.router, prefix="/credentials", tags=["Credentials"])
app.include_router(audit.router, prefix="/audit", tags=["Audit"])
app.include_router(ws_router, tags=["WebSocket"])


@app.get("/health")
async def health_check():
    """System health check."""
    checks = {}

    try:
        checks["postgres"] = await app_state.db.health_check()
    except Exception:
        checks["postgres"] = False

    try:
        checks["redis"] = await app_state.redis.health_check()
    except Exception:
        checks["redis"] = False

    try:
        stats = app_state.memory.get_stats()
        checks["chromadb"] = "error" not in stats
    except Exception:
        checks["chromadb"] = False

    checks["vault"] = app_state.vault is not None

    all_healthy = all(checks.values())
    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": checks,
    }
