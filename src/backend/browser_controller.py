"""Async Playwright wrapper for browser lifecycle and screenshot capture."""

from __future__ import annotations

import logging

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from src.backend.config import SCREEN_HEIGHT, SCREEN_WIDTH

logger = logging.getLogger(__name__)


class BrowserController:
    """Manages an async Playwright Chromium instance."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._page

    async def launch(self, url: str = "https://www.google.com") -> None:
        """Start Chromium and navigate to *url*."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=False)
        self._context = await self._browser.new_context(
            viewport={"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
        )
        self._page = await self._context.new_page()
        await self._page.goto(url, wait_until="domcontentloaded")
        logger.info("Browser launched -> %s", url)

    async def screenshot(self) -> bytes:
        """Capture the current page as a JPEG image (smaller than PNG)."""
        return await self.page.screenshot(type="jpeg", quality=70)

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")
        logger.info("Navigated to %s", url)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        logger.info("Browser closed")
