"""
Browser Specialist Agent — LLM-controlled Playwright browser via browser-use.

Controls a real web browser to complete tasks on websites exactly as a human would.
Sees the current page state (screenshot + DOM summary) and decides what action to take.

Each browser agent instance gets an isolated browser context (no session sharing).
"""

import os
import uuid
import base64
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional

from core.memory.credential_vault import CredentialVault
from core.task_manifest import RiskLevel
from agents.specialists.browser.login_handler import LoginHandler
from agents.specialists.browser.navigator import Navigator
from agents.specialists.browser.scraper import Scraper

logger = logging.getLogger(__name__)

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
  wait(seconds)                         Wait for page load
  screenshot()                          Capture current page state
  extract_text(element_description)     Read text from a specific element
  get_page_elements()                   Get structured list of interactive elements
  download_file(element_description)    Click download link and save file

EXECUTION PATTERN for every action:
  1. Look at current page state
  2. Identify the target element
  3. Take the action
  4. Verify the action had the expected effect
  5. If unexpected: screenshot and report back

IMPORTANT RULES:
  - You are operating in a real browser on real websites with real consequences
  - Never proceed past a payment confirmation screen without approval
  - If a page looks unexpected (wrong site, error, CAPTCHA), report immediately
  - Do not close the browser between steps
"""


class BrowserSpecialist:
    """
    LLM-controlled browser agent using Playwright + browser-use.

    Each instance creates an isolated browser context.
    Integrates with credential vault for secure login flows.
    """

    def __init__(
        self,
        vault: Optional[CredentialVault] = None,
        downloads_dir: str = "./downloads",
        screenshots_dir: str = "./screenshots",
    ):
        self.vault = vault
        self.downloads_dir = Path(downloads_dir)
        self.screenshots_dir = Path(screenshots_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

        self.login_handler = LoginHandler(vault=vault)
        self.navigator = Navigator()
        self.scraper = Scraper()

        self._browser = None
        self._context = None
        self._page = None

    async def execute(
        self,
        instructions: str,
        sub_task_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """
        Execute browser instructions using browser-use.

        Args:
            instructions: Natural language instructions from the Orchestrator
            sub_task_id: ID of the sub-task being executed
            task_id: Parent task ID

        Returns:
            Dict with results, evidence, screenshots, etc.
        """
        result = {
            "success": False,
            "sub_task_id": sub_task_id,
            "evidence": {},
            "screenshots": [],
            "extracted_data": {},
        }

        try:
            # Try to use browser-use library for LLM-controlled browsing
            agent_result = await self._execute_with_browser_use(
                instructions, sub_task_id, task_id
            )
            result.update(agent_result)

        except ImportError:
            logger.warning("browser-use not available, using direct Playwright")
            agent_result = await self._execute_with_playwright(
                instructions, sub_task_id, task_id
            )
            result.update(agent_result)

        except Exception as e:
            logger.error(f"Browser execution failed: {e}")
            result["error"] = str(e)
            result["success"] = False

        return result

    async def _execute_with_browser_use(
        self,
        instructions: str,
        sub_task_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """Execute using the browser-use library for LLM-controlled browsing."""
        from browser_use import Agent, Browser, BrowserConfig

        # Create isolated browser context
        browser_config = BrowserConfig(
            headless=os.environ.get("BROWSER_HEADLESS", "true").lower() == "true",
        )
        browser = Browser(config=browser_config)

        # Create the browser-use agent with Gemini
        api_key = os.environ.get("GEMINI_API_KEY")
        model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

        # Prepare credential instructions (without actual credentials)
        enhanced_instructions = instructions
        if self.vault:
            enhanced_instructions = self._inject_credential_awareness(instructions)

        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(
                model=model,
                google_api_key=api_key,
                timeout=120,
            )

            agent = Agent(
                task=enhanced_instructions,
                llm=llm,
                browser=browser,
            )

            # Run the agent
            history = await agent.run(max_steps=30)

            # Collect results
            result = {
                "success": True,
                "evidence": {},
                "screenshots": [],
                "extracted_data": {},
            }

            # Extract final result from history
            if history:
                final = history.final_result()
                if final:
                    result["extracted_data"] = {"agent_output": final}
                    result["evidence"]["final_output"] = final

                # Save screenshots
                screenshots = history.screenshots()
                for i, screenshot in enumerate(screenshots or []):
                    path = self.screenshots_dir / f"{task_id}_{sub_task_id}_{i}.png"
                    if isinstance(screenshot, bytes):
                        path.write_bytes(screenshot)
                    elif isinstance(screenshot, str):
                        # base64 encoded
                        path.write_bytes(base64.b64decode(screenshot))
                    result["screenshots"].append(str(path))

                # Get action history for evidence
                actions = history.action_names() if hasattr(history, 'action_names') else []
                result["evidence"]["actions"] = actions

            return result

        except Exception as e:
            logger.error(f"browser-use execution failed: {e}")
            raise
        finally:
            await browser.close()

    async def _execute_with_playwright(
        self,
        instructions: str,
        sub_task_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """
        Fallback: Execute using direct Playwright without browser-use.
        Provides basic navigation, screenshot, and extraction.
        """
        from playwright.async_api import async_playwright

        result = {
            "success": False,
            "evidence": {},
            "screenshots": [],
            "extracted_data": {},
        }

        async with async_playwright() as p:
            # Create isolated browser context
            context_dir = Path(f"/tmp/browser_ctx_{uuid.uuid4().hex[:8]}")
            context_dir.mkdir(parents=True, exist_ok=True)

            browser = await p.chromium.launch(
                headless=os.environ.get("BROWSER_HEADLESS", "true").lower() == "true",
            )
            context = await browser.new_context(
                accept_downloads=True,
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()

            try:
                # Parse basic actions from instructions using LLM
                actions = await self._parse_instructions(instructions)

                for action in actions:
                    action_type = action.get("type", "")

                    if action_type == "navigate":
                        url = action.get("url", "")
                        await page.goto(url, wait_until="domcontentloaded",
                                        timeout=30000)
                        await page.wait_for_timeout(2000)  # let JS settle
                        logger.info(f"Navigated to: {url}")

                    elif action_type == "screenshot":
                        path = self.screenshots_dir / f"{task_id}_{sub_task_id}_{uuid.uuid4().hex[:6]}.png"
                        await page.screenshot(path=str(path), full_page=True)
                        result["screenshots"].append(str(path))

                    elif action_type == "extract_text":
                        selector = action.get("selector", "body")
                        try:
                            text = await page.text_content(selector)
                            result["extracted_data"][selector] = text
                        except Exception:
                            # Fallback: extract all visible text
                            text = await page.text_content("body")
                            result["extracted_data"]["body"] = text[:5000]

                    elif action_type == "click":
                        selector = action.get("selector", "")
                        if selector:
                            await page.click(selector, timeout=10000)

                    elif action_type == "type":
                        selector = action.get("selector", "")
                        text = action.get("text", "")
                        if selector and text:
                            await page.fill(selector, text)

                    elif action_type == "wait":
                        seconds = action.get("seconds", 2)
                        await asyncio.sleep(seconds)

                    elif action_type == "login":
                        site = action.get("site", "")
                        login_result = await self.login_handler.login(
                            page=page,
                            site=site,
                        )
                        result["evidence"]["login"] = login_result

                # Take final screenshot
                final_path = self.screenshots_dir / f"{task_id}_{sub_task_id}_final.png"
                await page.screenshot(path=str(final_path), full_page=True)
                result["screenshots"].append(str(final_path))

                # Extract page content
                title = await page.title()
                url = page.url
                body_text = await page.text_content("body")
                result["extracted_data"]["page_title"] = title
                result["extracted_data"]["page_url"] = url
                result["extracted_data"]["page_text"] = (body_text or "")[:5000]
                result["evidence"]["final_url"] = url
                result["evidence"]["final_title"] = title

                result["success"] = True

            except Exception as e:
                logger.error(f"Playwright execution error: {e}")
                result["error"] = str(e)

                # Try to capture error screenshot
                try:
                    err_path = self.screenshots_dir / f"{task_id}_{sub_task_id}_error.png"
                    await page.screenshot(path=str(err_path))
                    result["screenshots"].append(str(err_path))
                except Exception:
                    pass

            finally:
                await context.close()
                await browser.close()

        return result

    async def _parse_instructions(self, instructions: str) -> list:
        """
        Use LLM to parse natural language instructions into browser actions.
        """
        import google.generativeai as genai

        try:
            api_key = os.environ.get("GEMINI_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
            model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=(
                    "Convert browser instructions to a JSON array of actions. "
                    "Each action has a 'type' (navigate, click, type, screenshot, "
                    "extract_text, wait, login) and relevant params (url, selector, "
                    "text, seconds, site). Output ONLY valid JSON array."
                )
            )
            response = await model.generate_content_async(instructions)
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return __import__("json").loads(text)
        except Exception as e:
            logger.warning(f"Failed to parse instructions via LLM: {e}")
            # Fallback: extract URLs and navigate
            import re
            urls = re.findall(r'https?://[^\s"\']+', instructions)
            actions = []
            for url in urls:
                actions.append({"type": "navigate", "url": url})
                actions.append({"type": "screenshot"})
                actions.append({"type": "extract_text", "selector": "body"})
            if not actions:
                actions = [
                    {"type": "navigate", "url": "about:blank"},
                    {"type": "screenshot"},
                ]
            return actions

    def _inject_credential_awareness(self, instructions: str) -> str:
        """
        Add credential retrieval instructions without actual credential values.
        """
        return (
            f"{instructions}\n\n"
            f"CREDENTIAL HANDLING: If login is required, the credentials will "
            f"be provided through the browser automation system. Do NOT ask "
            f"the user for credentials. They are retrieved securely from the vault."
        )
