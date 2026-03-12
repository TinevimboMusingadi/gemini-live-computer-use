"""Sub-agent calls: delegate work to other Gemini models."""

from __future__ import annotations

import logging
from typing import Any

from google import genai
from google.genai import types

from src.backend.config import (
    FLASH_MODEL,
    GOOGLE_API_KEY,
    NANO_BANANA_MODEL,
)
from src.backend import screenshot_store

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=GOOGLE_API_KEY)


async def analyze_image(
    image_filename: str,
    prompt: str,
) -> dict[str, Any]:
    """Send a saved screenshot to Gemini Flash for visual analysis.

    Args:
        image_filename: Name of a file previously saved by screenshot_store.
        prompt: What to analyze or ask about the image.

    Returns:
        Dict with ``result`` (analysis text) or ``error``.
    """
    path = screenshot_store.get_path(image_filename)
    if path is None:
        return {"error": f"File not found: {image_filename}"}

    image_bytes = path.read_bytes()
    mime = "image/jpeg" if path.suffix == ".jpg" else "image/png"

    logger.info(
        "Analyzing %s with %s: %.80s",
        image_filename,
        FLASH_MODEL,
        prompt,
    )

    try:
        response = await _client.aio.models.generate_content(
            model=FLASH_MODEL,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            inline_data=types.Blob(
                                data=image_bytes,
                                mime_type=mime,
                            ),
                        ),
                        types.Part(text=prompt),
                    ],
                ),
            ],
        )
        text = response.text or "(no response)"
        logger.info("Analysis complete (%d chars)", len(text))
        return {"result": text}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("analyze_image failed")
        return {"error": str(exc)}


async def generate_image(
    prompt: str,
    label: str = "",
    reference_filename: str = "",
) -> dict[str, Any]:
    """Generate an image using Nano Banana (Gemini native image generation).

    Uses ``generate_content`` with the Nano Banana model. The model can
    produce images alongside text; we extract the first image part and
    save it locally.

    Args:
        prompt: Detailed text description of the image to generate.
        label: Short label used in the saved filename.
        reference_filename: Optional existing screenshot to use as a
            reference image for editing or remixing.

    Returns:
        Dict with ``filename``, ``url``, and optional ``text``, or ``error``.
    """
    logger.info(
        "Generating image with Nano Banana (%s): %.120s",
        NANO_BANANA_MODEL,
        prompt,
    )

    parts: list[types.Part] = []

    if reference_filename:
        ref_path = screenshot_store.get_path(reference_filename)
        if ref_path is not None:
            ref_bytes = ref_path.read_bytes()
            mime = "image/jpeg" if ref_path.suffix == ".jpg" else "image/png"
            parts.append(
                types.Part(
                    inline_data=types.Blob(data=ref_bytes, mime_type=mime),
                ),
            )
            logger.info("Using reference image: %s", reference_filename)

    parts.append(types.Part(text=prompt))

    try:
        response = await _client.aio.models.generate_content(
            model=NANO_BANANA_MODEL,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        text_parts: list[str] = []
        saved_meta: dict[str, str] | None = None

        for part in response.parts or []:
            if part.text is not None:
                text_parts.append(part.text)
            elif part.inline_data is not None:
                image_bytes = part.inline_data.data
                if isinstance(image_bytes, str):
                    import base64
                    image_bytes = base64.b64decode(image_bytes)

                saved_meta = await screenshot_store.save_generated(
                    image_bytes,
                    label=label or "nano_banana",
                    ext="png",
                )
                logger.info(
                    "Nano Banana image saved: %s", saved_meta["filename"],
                )

        if saved_meta is None:
            return {
                "error": "Model did not return an image",
                "text": "\n".join(text_parts) if text_parts else "",
            }

        result: dict[str, Any] = {
            "result": "Image generated successfully",
            "filename": saved_meta["filename"],
            "url": saved_meta["url"],
        }
        if text_parts:
            result["text"] = "\n".join(text_parts)
        return result

    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("generate_image failed")
        return {"error": str(exc)}
