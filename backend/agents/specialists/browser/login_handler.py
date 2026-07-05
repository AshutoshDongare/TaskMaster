"""
Login Handler — Manages login flows, MFA handling, and session management.

LOGIN PROCEDURE — follow exactly, no shortcuts:
1. Navigate to site login page
2. Retrieve credentials from vault (NEVER hardcode)
3. Locate and fill username/email field
4. Locate and fill password field
5. Click submit/login button
6. Wait for page response
7. Check for MFA prompt → PAUSE and escalate to user
8. Verify login success
9. If failed after 1 attempt → STOP (account lockout risk)
"""

import logging
from typing import Dict, Any, Optional

from core.memory.credential_vault import CredentialVault

logger = logging.getLogger(__name__)


class LoginHandler:
    """
    Handles form-based login flows with MFA detection.
    Credentials come from the vault — never hardcoded.
    """

    # Common login form selectors (tried in order)
    USERNAME_SELECTORS = [
        'input[type="email"]',
        'input[name="email"]',
        'input[name="username"]',
        'input[name="user"]',
        'input[name="login"]',
        'input[id="email"]',
        'input[id="username"]',
        'input[id="login-email"]',
        'input[id="user_id"]',
        '#email',
        '#username',
        '#login',
    ]

    PASSWORD_SELECTORS = [
        'input[type="password"]',
        'input[name="password"]',
        'input[name="passwd"]',
        '#password',
        '#passwd',
    ]

    SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Log in")',
        'button:has-text("Login")',
        'button:has-text("Sign in")',
        'button:has-text("Sign In")',
        'button:has-text("Submit")',
        'button:has-text("Continue")',
        '#login-button',
        '#submit',
        '.login-button',
        '.btn-login',
    ]

    # MFA indicators
    MFA_INDICATORS = [
        "verification code",
        "two-factor",
        "2fa",
        "mfa",
        "authenticator",
        "otp",
        "one-time password",
        "security code",
        "enter the code",
        "check your phone",
        "check your email",
        "sms code",
        "totp",
    ]

    def __init__(self, vault: Optional[CredentialVault] = None):
        self.vault = vault

    async def login(
        self,
        page,
        site: str,
        login_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a login flow.

        Args:
            page: Playwright page object
            site: Domain name for credential lookup
            login_url: Optional direct login URL

        Returns:
            Dict with login result and evidence
        """
        result = {
            "success": False,
            "site": site,
            "mfa_required": False,
            "error": None,
        }

        # Step 1: Retrieve credentials from vault
        if not self.vault:
            result["error"] = "No credential vault configured"
            return result

        if not self.vault.exists(site):
            result["error"] = f"No credentials stored for '{site}'"
            return result

        creds = self.vault.retrieve(site)
        if not creds:
            result["error"] = f"Failed to retrieve credentials for '{site}'"
            return result

        username = creds.get("username") or creds.get("email") or creds.get("user")
        password = creds.get("password") or creds.get("passwd")

        if not username or not password:
            result["error"] = "Credentials incomplete — missing username or password"
            return result

        try:
            # Step 2: Navigate to login page if URL provided
            if login_url:
                await page.goto(login_url, wait_until="networkidle", timeout=30000)
            elif not page.url or page.url == "about:blank":
                await page.goto(f"https://{site}", wait_until="networkidle",
                                timeout=30000)

            # Step 3: Find and fill username field
            username_filled = False
            for selector in self.USERNAME_SELECTORS:
                try:
                    element = await page.wait_for_selector(selector, timeout=3000)
                    if element and await element.is_visible():
                        await element.fill(username)
                        username_filled = True
                        logger.info(f"Filled username using selector: {selector}")
                        break
                except Exception:
                    continue

            if not username_filled:
                result["error"] = "Could not find username/email input field"
                return result

            # Step 4: Find and fill password field
            password_filled = False
            for selector in self.PASSWORD_SELECTORS:
                try:
                    element = await page.wait_for_selector(selector, timeout=3000)
                    if element and await element.is_visible():
                        await element.fill(password)
                        password_filled = True
                        logger.info(f"Filled password using selector: {selector}")
                        break
                except Exception:
                    continue

            if not password_filled:
                # Maybe password is on a separate page (click next first)
                for selector in self.SUBMIT_SELECTORS:
                    try:
                        btn = await page.wait_for_selector(selector, timeout=2000)
                        if btn and await btn.is_visible():
                            await btn.click()
                            await page.wait_for_load_state("networkidle",
                                                           timeout=10000)
                            break
                    except Exception:
                        continue

                # Try password again
                for selector in self.PASSWORD_SELECTORS:
                    try:
                        element = await page.wait_for_selector(selector, timeout=5000)
                        if element and await element.is_visible():
                            await element.fill(password)
                            password_filled = True
                            break
                    except Exception:
                        continue

                if not password_filled:
                    result["error"] = "Could not find password input field"
                    return result

            # Step 5: Click submit/login button
            submitted = False
            for selector in self.SUBMIT_SELECTORS:
                try:
                    btn = await page.wait_for_selector(selector, timeout=3000)
                    if btn and await btn.is_visible():
                        await btn.click()
                        submitted = True
                        logger.info(f"Clicked submit using selector: {selector}")
                        break
                except Exception:
                    continue

            if not submitted:
                # Try pressing Enter as fallback
                await page.keyboard.press("Enter")
                submitted = True

            # Step 6: Wait for response
            await page.wait_for_load_state("networkidle", timeout=15000)
            import asyncio
            await asyncio.sleep(2)  # Extra wait for dynamic pages

            # Step 7: Check for MFA
            page_text = (await page.text_content("body") or "").lower()
            for indicator in self.MFA_INDICATORS:
                if indicator in page_text:
                    result["mfa_required"] = True
                    result["mfa_type"] = self._detect_mfa_type(page_text)
                    result["error"] = (
                        f"MFA required ({result['mfa_type']}). "
                        f"Please provide the code."
                    )
                    logger.info(f"MFA detected for {site}: {result['mfa_type']}")
                    return result

            # Step 8: Verify login success
            current_url = page.url
            login_success = await self._verify_login_success(page, site)

            if login_success:
                result["success"] = True
                result["logged_in_url"] = current_url
                logger.info(f"Login successful for {site}")
            else:
                # Check for error messages
                error_msg = await self._extract_login_error(page)
                result["error"] = error_msg or "Login may have failed — could not verify success"
                logger.warning(f"Login verification failed for {site}: {result['error']}")

        except Exception as e:
            logger.error(f"Login flow error for {site}: {e}")
            result["error"] = str(e)

        return result

    async def _verify_login_success(self, page, site: str) -> bool:
        """Check if login was successful by looking for common indicators."""
        try:
            page_text = (await page.text_content("body") or "").lower()

            # Success indicators
            success_words = [
                "dashboard", "welcome", "account", "profile",
                "logout", "sign out", "log out", "my account",
                "settings", "portfolio", "inbox",
            ]

            # Failure indicators (still on login page)
            failure_words = [
                "invalid password", "incorrect password",
                "login failed", "authentication failed",
                "wrong password", "invalid credentials",
                "account locked", "too many attempts",
            ]

            for word in failure_words:
                if word in page_text:
                    return False

            for word in success_words:
                if word in page_text:
                    return True

            # Check URL changed from login page
            url = page.url.lower()
            if "login" not in url and "signin" not in url:
                return True

            return False

        except Exception:
            return False

    async def _extract_login_error(self, page) -> Optional[str]:
        """Try to extract error message from the page."""
        error_selectors = [
            '.error', '.error-message', '.alert-danger', '.login-error',
            '[role="alert"]', '.notification-error', '.form-error',
        ]
        for selector in error_selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    text = await el.text_content()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue
        return None

    def _detect_mfa_type(self, page_text: str) -> str:
        """Detect the type of MFA required."""
        if "sms" in page_text or "text message" in page_text or "phone" in page_text:
            return "sms"
        elif "authenticator" in page_text or "totp" in page_text or "google auth" in page_text:
            return "totp"
        elif "email" in page_text and ("code" in page_text or "otp" in page_text):
            return "email_otp"
        return "unknown"
