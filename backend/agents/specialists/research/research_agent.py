"""
Research Specialist Agent — visits URLs, extracts content, synthesizes answers.

Handles tasks like:
- "Go to <url> and tell me X"
- "Look up <topic> on <site>"
- Research + summarise tasks

Uses Playwright to visit pages, Gemini to synthesize the answer.
"""

import os
import re
import json
import logging
import asyncio
from typing import Dict, Any

import google.generativeai as genai

logger = logging.getLogger(__name__)


class ResearchSpecialist:
    """
    Visits URLs, extracts page content, and synthesises an answer using Gemini.
    """

    async def execute(
        self,
        instructions: str,
        sub_task_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """
        Execute a research / read-only browsing task.

        Strategy:
        1. Extract URLs from instructions
        2. Visit each URL with Playwright and grab page text
        3. Ask Gemini to synthesise the answer from the extracted text
        """
        logger.info(f"ResearchSpecialist executing sub_task={sub_task_id}")

        # --- 1. Extract URLs ---
        urls = re.findall(r'https?://[^\s"\']+', instructions)
        if not urls:
            # No URL found — answer from LLM knowledge only
            return await self._llm_only_answer(instructions, sub_task_id)

        # --- 2. Scrape each URL ---
        page_texts: Dict[str, str] = {}
        for url in urls[:3]:  # cap at 3 URLs
            text = await self._scrape_url(url)
            page_texts[url] = text

        if not any(page_texts.values()):
            return {
                "success": False,
                "error": "Could not extract content from any URL",
                "sub_task_id": sub_task_id,
            }

        # --- 3. Synthesise with Gemini ---
        answer = await self._synthesise(instructions, page_texts)

        return {
            "success": True,
            "sub_task_id": sub_task_id,
            "answer": answer,
            "sources": list(page_texts.keys()),
            "evidence": {
                "final_output": answer,
                "pages_visited": list(page_texts.keys()),
            },
            "extracted_data": {
                "answer": answer,
                "page_texts": {url: txt[:500] for url, txt in page_texts.items()},
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _scrape_url(self, url: str) -> str:
        """Visit a URL with Playwright and return visible body text."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(2)  # let JS render
                    text = await page.text_content("body") or ""
                    # Collapse whitespace
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text[:8000]
                except Exception as e:
                    logger.warning(f"Failed to scrape {url}: {e}")
                    return ""
                finally:
                    await context.close()
                    await browser.close()
        except Exception as e:
            logger.error(f"Playwright not available: {e}")
            return ""

    async def _synthesise(
        self, instructions: str, page_texts: Dict[str, str]
    ) -> str:
        """Use Gemini to answer the question given the scraped content."""
        api_key = os.environ.get("GEMINI_API_KEY")
        model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
        genai.configure(api_key=api_key)

        context_parts = []
        for url, text in page_texts.items():
            if text:
                context_parts.append(f"--- Content from {url} ---\n{text}\n")

        prompt = (
            f"You are a helpful research assistant. Based ONLY on the web page "
            f"content below, answer the user's question precisely and concisely.\n\n"
            f"USER TASK: {instructions}\n\n"
            f"{''.join(context_parts)}\n"
            f"Answer:"
        )

        try:
            model = genai.GenerativeModel(model_name)
            response = await model.generate_content_async(prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini synthesis failed: {e}")
            return f"Could not synthesise answer: {e}"

    async def _llm_only_answer(
        self, instructions: str, sub_task_id: str
    ) -> Dict[str, Any]:
        """Answer from LLM knowledge when no URL is present."""
        api_key = os.environ.get("GEMINI_API_KEY")
        model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
        genai.configure(api_key=api_key)

        try:
            model = genai.GenerativeModel(model_name)
            response = await model.generate_content_async(instructions)
            answer = response.text.strip()
            return {
                "success": True,
                "sub_task_id": sub_task_id,
                "answer": answer,
                "sources": [],
                "evidence": {"final_output": answer},
                "extracted_data": {"answer": answer},
            }
        except Exception as e:
            logger.error(f"LLM-only answer failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "sub_task_id": sub_task_id,
            }
