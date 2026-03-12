"""Persist and serve browser screenshots on the local filesystem."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from src.backend.config import GENERATED_DIR, SCREENSHOTS_DIR

logger = logging.getLogger(__name__)


def _ensure_dirs() -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def _safe_label(label: str) -> str:
    """Sanitise a user-supplied label into a filename-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", label.strip().lower())
    return slug[:60] or "capture"


async def save(jpeg_bytes: bytes, label: str = "") -> dict[str, str]:
    """Save a JPEG screenshot and return its metadata.

    Args:
        jpeg_bytes: Raw JPEG image data.
        label: Human-readable label for the screenshot.

    Returns:
        Dict with ``filename`` and ``url`` keys.
    """
    _ensure_dirs()
    slug = _safe_label(label) if label else "capture"
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{slug}.jpg"
    filepath = SCREENSHOTS_DIR / filename
    filepath.write_bytes(jpeg_bytes)
    logger.info("Screenshot saved: %s (%d bytes)", filename, len(jpeg_bytes))
    return {
        "filename": filename,
        "url": f"/screenshots/{filename}",
    }


async def save_generated(
    image_bytes: bytes,
    label: str = "",
    ext: str = "png",
) -> dict[str, str]:
    """Save a generated image and return its metadata."""
    _ensure_dirs()
    slug = _safe_label(label) if label else "generated"
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{slug}.{ext}"
    filepath = GENERATED_DIR / filename
    filepath.write_bytes(image_bytes)
    logger.info("Generated image saved: %s (%d bytes)", filename, len(image_bytes))
    return {
        "filename": filename,
        "url": f"/screenshots/generated/{filename}",
    }


def list_screenshots() -> list[dict[str, str]]:
    """Return metadata for every saved screenshot, newest first."""
    _ensure_dirs()
    items: list[dict[str, str]] = []
    for p in sorted(SCREENSHOTS_DIR.glob("*.jpg"), reverse=True):
        items.append({"filename": p.name, "url": f"/screenshots/{p.name}"})
    for p in sorted(GENERATED_DIR.glob("*"), reverse=True):
        items.append({
            "filename": p.name,
            "url": f"/screenshots/generated/{p.name}",
        })
    return items


def get_path(filename: str) -> Path | None:
    """Resolve a filename to its absolute path, or None if not found."""
    candidate = SCREENSHOTS_DIR / filename
    if candidate.is_file():
        return candidate
    candidate = GENERATED_DIR / filename
    if candidate.is_file():
        return candidate
    return None
