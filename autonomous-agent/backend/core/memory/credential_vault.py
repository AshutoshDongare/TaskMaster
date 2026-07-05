"""
Credential Vault — Encrypted per-site credential storage.

Credentials encrypted at rest using Fernet symmetric encryption.
Encryption key loaded from VAULT_KEY environment variable ONLY.
Never stored in database. Never in code. Never in logs.

CRITICAL: The LLM never receives credential values. It instructs the
browser to "use stored credentials for site X". The vault decrypts
and passes to Playwright internals only.
"""

import json
import os
import logging
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class CredentialVault:
    """
    File-backed encrypted credential store using Fernet symmetric encryption.

    Usage:
        vault = CredentialVault()
        vault.store("namecheap.com", {"username": "user@email.com", "password": "..."})
        creds = vault.retrieve("namecheap.com")  # Returns to Playwright only
        exists = vault.exists("namecheap.com")    # Safe to return to LLM
        sites = vault.list_sites()                # Site names only, no passwords
    """

    def __init__(self):
        key_str = os.environ.get("VAULT_KEY")
        if not key_str:
            raise EnvironmentError(
                "VAULT_KEY environment variable is required. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        self.fernet = Fernet(key_str.encode())
        self.vault_path = Path(os.environ.get("VAULT_PATH", "./vault/credentials.enc"))
        self._ensure_vault_exists()

    def _ensure_vault_exists(self):
        """Create vault file and parent directories if they don't exist."""
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.vault_path.exists():
            self._write_vault({})
            logger.info(f"Created new credential vault at {self.vault_path}")

    def _read_vault(self) -> dict:
        """Read and decrypt the vault file."""
        try:
            encrypted_data = self.vault_path.read_bytes()
            if not encrypted_data:
                return {}
            decrypted = self.fernet.decrypt(encrypted_data)
            return json.loads(decrypted.decode("utf-8"))
        except InvalidToken:
            logger.error("Failed to decrypt vault — VAULT_KEY may have changed")
            raise ValueError("Cannot decrypt credential vault. Check VAULT_KEY.")
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_vault(self, data: dict):
        """Encrypt and write the vault file."""
        json_bytes = json.dumps(data, indent=2).encode("utf-8")
        encrypted = self.fernet.encrypt(json_bytes)
        self.vault_path.write_bytes(encrypted)

    def store(self, site: str, credentials: dict):
        """
        Store credentials for a site. Overwrites if site already exists.

        Args:
            site: Domain name (e.g., "namecheap.com")
            credentials: Dict with at minimum {"username": ..., "password": ...}
        """
        vault_data = self._read_vault()
        vault_data[site] = credentials
        self._write_vault(vault_data)
        # NEVER log credential values
        logger.info(f"Stored credentials for site: {site}")

    def retrieve(self, site: str) -> Optional[dict]:
        """
        Retrieve decrypted credentials for a site.

        WARNING: Return value contains plaintext credentials.
        Only pass to Playwright/browser internals. NEVER pass to LLM context,
        logs, audit records, or any external system.

        Args:
            site: Domain name

        Returns:
            Dict with credentials, or None if not found
        """
        vault_data = self._read_vault()
        creds = vault_data.get(site)
        if creds:
            logger.info(f"Retrieved credentials for site: {site}")
        else:
            logger.warning(f"No credentials found for site: {site}")
        return creds

    def exists(self, site: str) -> bool:
        """
        Check if credentials exist for a site. Safe to return to LLM.

        Args:
            site: Domain name

        Returns:
            True if credentials are stored for this site
        """
        vault_data = self._read_vault()
        return site in vault_data

    def list_sites(self) -> list[str]:
        """
        List all sites with stored credentials. Returns site names ONLY.
        Safe to return to LLM — no usernames, no passwords.

        Returns:
            List of site domain names
        """
        vault_data = self._read_vault()
        return list(vault_data.keys())

    def delete(self, site: str) -> bool:
        """
        Remove stored credentials for a site.

        Args:
            site: Domain name

        Returns:
            True if credentials were deleted, False if site not found
        """
        vault_data = self._read_vault()
        if site in vault_data:
            del vault_data[site]
            self._write_vault(vault_data)
            logger.info(f"Deleted credentials for site: {site}")
            return True
        logger.warning(f"Cannot delete — no credentials found for site: {site}")
        return False
