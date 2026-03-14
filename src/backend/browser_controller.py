"""Async Playwright wrapper for browser lifecycle and screenshot capture."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from src.backend.config import PROJECT_ROOT, SCREEN_HEIGHT, SCREEN_WIDTH

logger = logging.getLogger(__name__)

USER_DATA_DIR = PROJECT_ROOT / "playwright-profile"


class BrowserController:
    """Manages an async Playwright Chromium instance."""

    def __init__(self) -> None:
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._page

    async def launch(self, url: str = "https://www.google.com") -> None:
        """Start Chromium with a persistent profile and navigate to *url*."""
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            viewport={"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
        )
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
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
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._playwright = None
        logger.info("Browser closed")
