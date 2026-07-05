"""
Scraper — Structured data extraction from web pages.

Extracts text, tables, forms, and structured data from page DOM.
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class Scraper:
    """
    Extracts structured data from web pages.
    """

    async def extract_text(self, page, selector: str = "body") -> str:
        """Extract text content from an element."""
        try:
            text = await page.text_content(selector)
            return (text or "").strip()
        except Exception as e:
            logger.warning(f"Text extraction failed for {selector}: {e}")
            return ""

    async def extract_tables(self, page) -> List[List[List[str]]]:
        """Extract all tables from the page as lists of rows."""
        try:
            tables = await page.evaluate("""
                () => {
                    const result = [];
                    document.querySelectorAll('table').forEach(table => {
                        const rows = [];
                        table.querySelectorAll('tr').forEach(tr => {
                            const cells = [];
                            tr.querySelectorAll('td, th').forEach(cell => {
                                cells.push(cell.textContent.trim());
                            });
                            if (cells.length > 0) rows.push(cells);
                        });
                        if (rows.length > 0) result.push(rows);
                    });
                    return result;
                }
            """)
            return tables
        except Exception as e:
            logger.warning(f"Table extraction failed: {e}")
            return []

    async def extract_links(self, page,
                            filter_text: Optional[str] = None) -> List[Dict[str, str]]:
        """Extract links from the page, optionally filtered by text."""
        try:
            links = await page.evaluate("""
                () => {
                    const links = [];
                    document.querySelectorAll('a').forEach(a => {
                        const href = a.href;
                        const text = a.textContent.trim();
                        if (href && text) {
                            links.push({ href, text: text.substring(0, 200) });
                        }
                    });
                    return links;
                }
            """)

            if filter_text:
                filter_lower = filter_text.lower()
                links = [l for l in links
                         if filter_lower in l.get("text", "").lower()
                         or filter_lower in l.get("href", "").lower()]

            return links
        except Exception as e:
            logger.warning(f"Link extraction failed: {e}")
            return []

    async def extract_form_data(self, page) -> List[Dict[str, Any]]:
        """Extract form fields and their current values."""
        try:
            forms = await page.evaluate("""
                () => {
                    const result = [];
                    document.querySelectorAll('form').forEach((form, idx) => {
                        const fields = [];
                        form.querySelectorAll('input, select, textarea').forEach(el => {
                            fields.push({
                                tag: el.tagName.toLowerCase(),
                                type: el.type || '',
                                name: el.name || '',
                                id: el.id || '',
                                placeholder: el.placeholder || '',
                                value: el.type === 'password' ? '[hidden]' : (el.value || ''),
                                required: el.required,
                            });
                        });
                        result.push({
                            index: idx,
                            action: form.action || '',
                            method: form.method || 'GET',
                            fields: fields,
                        });
                    });
                    return result;
                }
            """)
            return forms
        except Exception as e:
            logger.warning(f"Form extraction failed: {e}")
            return []

    async def extract_structured_data(
        self,
        page,
        data_points: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Extract specific data points using CSS selectors.

        Args:
            page: Playwright page
            data_points: Dict of {name: css_selector}

        Returns:
            Dict of {name: extracted_value}
        """
        results = {}
        for name, selector in data_points.items():
            try:
                text = await page.text_content(selector)
                results[name] = (text or "").strip()
            except Exception as e:
                results[name] = None
                logger.warning(f"Could not extract '{name}' ({selector}): {e}")
        return results

    async def extract_page_metadata(self, page) -> Dict[str, str]:
        """Extract page metadata (title, description, URL, etc.)."""
        try:
            metadata = await page.evaluate("""
                () => ({
                    title: document.title,
                    url: window.location.href,
                    description: (document.querySelector('meta[name="description"]')
                                  || {}).content || '',
                    h1: (document.querySelector('h1') || {}).textContent || '',
                    canonical: (document.querySelector('link[rel="canonical"]')
                               || {}).href || '',
                })
            """)
            return metadata
        except Exception as e:
            logger.warning(f"Metadata extraction failed: {e}")
            return {"title": "", "url": page.url}
