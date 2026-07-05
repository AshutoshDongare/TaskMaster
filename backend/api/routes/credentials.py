"""
Credential API Routes.

GET    /credentials       — List sites with stored credentials (names only)
POST   /credentials       — Store credentials for a site
DELETE /credentials/{site} — Remove stored credentials
"""

import logging

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class CredentialStoreRequest(BaseModel):
    site: str
    username: str
    password: str


@router.get("")
async def list_credentials(request: Request):
    """
    List sites with stored credentials.
    Returns site names ONLY — no usernames, no passwords.
    """
    state = request.app.state.app_state

    if not state.vault:
        raise HTTPException(status_code=503,
                            detail="Credential vault not initialized. "
                                   "Set VAULT_KEY environment variable.")

    sites = state.vault.list_sites()
    return {"sites": sites, "count": len(sites)}


@router.post("")
async def store_credentials(request: Request, body: CredentialStoreRequest):
    """
    Store credentials for a site. Encrypted at rest using Fernet.
    """
    state = request.app.state.app_state

    if not state.vault:
        raise HTTPException(status_code=503,
                            detail="Credential vault not initialized")

    state.vault.store(
        body.site,
        {"username": body.username, "password": body.password},
    )

    # Audit log (WITHOUT the actual credentials)
    await state.audit_logger.log(
        action="credentials_stored",
        context={"site": body.site},
        risk_level="medium",
        task_id="system",
        agent_name="user",
        result="success",
    )

    return {
        "status": "stored",
        "site": body.site,
        "message": f"Credentials stored for {body.site}",
    }


@router.delete("/{site}")
async def delete_credentials(request: Request, site: str):
    """Remove stored credentials for a site."""
    state = request.app.state.app_state

    if not state.vault:
        raise HTTPException(status_code=503,
                            detail="Credential vault not initialized")

    deleted = state.vault.delete(site)
    if not deleted:
        raise HTTPException(status_code=404,
                            detail=f"No credentials found for {site}")

    await state.audit_logger.log(
        action="credentials_deleted",
        context={"site": site},
        risk_level="medium",
        task_id="system",
        agent_name="user",
        result="success",
    )

    return {"status": "deleted", "site": site}
