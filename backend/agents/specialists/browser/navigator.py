"""
Navigator — Page navigation, element finding, scroll/wait utilities.

Memory-guided navigation: uses recalled patterns from past successful tasks
to navigate efficiently instead of exploring blindly.
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class Navigator:
    """
    Handles page navigation, element finding, scrolling, and waiting.
    """

    async def navigate(self, page, url: str, timeout: int = 30000) -> Dict[str, Any]:
        """Navigate to a URL and wait for load."""
        try:
            response = await page.goto(url, wait_until="networkidle",
                                        timeout=timeout)
            status = response.status if response else None
            return {
                "success": True,
                "url": page.url,
                "status": status,
                "title": await page.title(),
            }
        except Exception as e:
            logger.error(f"Navigation to {url} failed: {e}")
            return {"success": False, "error": str(e), "url": url}

    async def find_element(
        self,
        page,
        description: str,
        selectors: Optional[List[str]] = None,
    ) -> Optional[Any]:
        """
        Find an element by description or CSS selectors.

        Args:
            page: Playwright page
            description: Human-readable description of the element
            selectors: Optional list of CSS selectors to try

        Returns:
            Element handle or None
        """
        if selectors:
            for selector in selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=3000)
                    if element and await element.is_visible():
                        return element
                except Exception:
                    continue

        # Try text-based selection
        text_selectors = [
            f'text="{description}"',
            f'text={description}',
            f'*:has-text("{description}")',
            f'a:has-text("{description}")',
            f'button:has-text("{description}")',
        ]

        for selector in text_selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=2000)
                if element and await element.is_visible():
                    return element
            except Exception:
                continue

        return None

    async def scroll_to_element(self, page, selector: str) -> bool:
        """Scroll to make an element visible."""
        try:
            await page.eval_on_selector(
                selector,
                "el => el.scrollIntoView({behavior: 'smooth', block: 'center'})"
            )
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            logger.warning(f"Scroll to {selector} failed: {e}")
            return False

    async def scroll_page(self, page, direction: str = "down",
                          pixels: int = 500) -> bool:
        """Scroll the page."""
        try:
            if direction == "down":
                await page.evaluate(f"window.scrollBy(0, {pixels})")
            elif direction == "up":
                await page.evaluate(f"window.scrollBy(0, -{pixels})")
            await asyncio.sleep(0.3)
            return True
        except Exception as e:
            logger.warning(f"Scroll failed: {e}")
            return False

    async def wait_for_navigation(self, page, timeout: int = 10000) -> bool:
        """Wait for page navigation to complete."""
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout)
            return True
        except Exception:
            return False

    async def get_page_elements(self, page) -> List[Dict[str, str]]:
        """Get a structured list of interactive elements on the page."""
        try:
            elements = await page.evaluate("""
                () => {
                    const elements = [];
                    const selectors = 'a, button, input, select, textarea, [role="button"], [onclick]';
                    document.querySelectorAll(selectors).forEach((el, idx) => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            elements.push({
                                index: idx,
                                tag: el.tagName.toLowerCase(),
                                type: el.type || '',
                                text: (el.textContent || '').trim().substring(0, 100),
                                placeholder: el.placeholder || '',
                                name: el.name || '',
                                id: el.id || '',
                                href: el.href || '',
                                value: el.value ? '[has value]' : '',
                                visible: true,
                            });
                        }
                    });
                    return elements;
                }
            """)
            return elements
        except Exception as e:
            logger.warning(f"Failed to get page elements: {e}")
            return []

    async def take_screenshot(self, page, path: str,
                              full_page: bool = True) -> str:
        """Take a screenshot and return the file path."""
        await page.screenshot(path=path, full_page=full_page)
        return path
