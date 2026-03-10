"""Translate Gemini Computer-Use tool calls into Playwright actions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from playwright.async_api import Page

from src.backend.config import SCREEN_HEIGHT, SCREEN_WIDTH

logger = logging.getLogger(__name__)


def _denormalize_x(x: int) -> int:
    """Convert a 0-999 normalised x to actual pixel coordinate."""
    return int(x / 1000 * SCREEN_WIDTH)


def _denormalize_y(y: int) -> int:
    """Convert a 0-999 normalised y to actual pixel coordinate."""
    return int(y / 1000 * SCREEN_HEIGHT)


async def execute_action(
    page: Page,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Run a single Computer-Use action on *page*.

    Returns:
        A dict with at least ``{"result": "ok"}`` on success or
        ``{"error": "<message>"}`` on failure.
    """
    try:
        result = await _dispatch(page, name, args)
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
        await asyncio.sleep(0.5)
        return result
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Action %s failed", name)
        return {"error": str(exc)}


async def _dispatch(
    page: Page,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Map *name* to the corresponding Playwright call."""

    if name == "open_web_browser":
        return {"result": "ok"}

    if name == "navigate":
        url = args["url"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        await page.goto(url, wait_until="domcontentloaded")
        return {"result": "ok", "url": page.url}

    if name == "click_at":
        px = _denormalize_x(int(args["x"]))
        py = _denormalize_y(int(args["y"]))
        await page.mouse.click(px, py)
        logger.info("click_at(%d, %d) -> pixel (%d, %d)", args["x"], args["y"], px, py)
        return {"result": "ok"}

    if name == "type_text_at":
        px = _denormalize_x(int(args["x"]))
        py = _denormalize_y(int(args["y"]))
        text = args.get("text", "")
        press_enter = args.get("press_enter", True)
        clear_first = args.get("clear_before_typing", True)

        await page.mouse.click(px, py)
        if clear_first:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        await page.keyboard.type(text, delay=30)
        if press_enter:
            await page.keyboard.press("Enter")
        logger.info("type_text_at(%d, %d) text=%r", args["x"], args["y"], text)
        return {"result": "ok"}

    if name == "hover_at":
        px = _denormalize_x(int(args["x"]))
        py = _denormalize_y(int(args["y"]))
        await page.mouse.move(px, py)
        return {"result": "ok"}

    if name == "scroll_document":
        direction = args.get("direction", "down")
        delta_map = {"down": 400, "up": -400, "right": 400, "left": -400}
        delta = delta_map.get(direction, 400)
        if direction in ("down", "up"):
            await page.mouse.wheel(0, delta)
        else:
            await page.mouse.wheel(delta, 0)
        return {"result": "ok"}

    if name == "scroll_at":
        px = _denormalize_x(int(args["x"]))
        py = _denormalize_y(int(args["y"]))
        direction = args.get("direction", "down")
        magnitude = int(args.get("magnitude", 800))
        pixel_mag = int(magnitude / 1000 * SCREEN_HEIGHT)
        await page.mouse.move(px, py)
        if direction in ("down", "up"):
            delta = pixel_mag if direction == "down" else -pixel_mag
            await page.mouse.wheel(0, delta)
        else:
            delta = pixel_mag if direction == "right" else -pixel_mag
            await page.mouse.wheel(delta, 0)
        return {"result": "ok"}

    if name == "go_back":
        await page.go_back()
        return {"result": "ok"}

    if name == "go_forward":
        await page.go_forward()
        return {"result": "ok"}

    if name == "search":
        await page.goto("https://www.google.com", wait_until="domcontentloaded")
        return {"result": "ok"}

    if name == "wait_5_seconds":
        await asyncio.sleep(5)
        return {"result": "ok"}

    if name == "key_combination":
        keys = args.get("keys", "")
        await page.keyboard.press(keys)
        return {"result": "ok"}

    if name == "drag_and_drop":
        sx = _denormalize_x(int(args["x"]))
        sy = _denormalize_y(int(args["y"]))
        dx = _denormalize_x(int(args["destination_x"]))
        dy = _denormalize_y(int(args["destination_y"]))
        await page.mouse.move(sx, sy)
        await page.mouse.down()
        await page.mouse.move(dx, dy, steps=20)
        await page.mouse.up()
        return {"result": "ok"}

    logger.warning("Unknown action: %s", name)
    return {"error": f"Unknown action: {name}"}
