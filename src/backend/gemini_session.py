"""Manage a Gemini Live API session with browser-control function calling."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types

from src.backend.config import GOOGLE_API_KEY, MODEL_NAME, SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

AudioCallback = Callable[[bytes], Any]
ToolCallCallback = Callable[[list[dict]], Any]
TranscriptionCallback = Callable[[str, str], Any]
StatusCallback = Callable[[str], Any]

_BROWSER_FUNCTIONS: list[dict[str, Any]] = [
    {
        "name": "navigate",
        "description": (
            "Navigate the browser to a URL. Always include the full URL "
            "with https:// prefix."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL including https:// prefix.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "click_at",
        "description": (
            "Click at a position on the page. Coordinates are on a "
            "normalised 0-999 grid mapped to the screen dimensions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X (0-999)."},
                "y": {"type": "integer", "description": "Y (0-999)."},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "type_text_at",
        "description": (
            "Click at a position then type text. Optionally press Enter."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X (0-999)."},
                "y": {"type": "integer", "description": "Y (0-999)."},
                "text": {"type": "string", "description": "Text to type."},
                "press_enter": {
                    "type": "boolean",
                    "description": "Press Enter after typing.",
                },
            },
            "required": ["x", "y", "text"],
        },
    },
    {
        "name": "scroll_document",
        "description": "Scroll the entire page in a direction.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                },
            },
            "required": ["direction"],
        },
    },
    {
        "name": "go_back",
        "description": "Navigate to the previous page in browser history.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "key_combination",
        "description": "Press a keyboard key or combo (e.g. 'Enter').",
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {"type": "string"},
            },
            "required": ["keys"],
        },
    },
    {
        "name": "wait_5_seconds",
        "description": "Wait 5 seconds for the page to load.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "save_screenshot",
        "description": (
            "Capture the current browser view and save it locally. "
            "Returns the filename you can later pass to analyze_screenshot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": (
                        "Short label describing the screenshot content."
                    ),
                },
            },
            "required": ["label"],
        },
    },
    {
        "name": "analyze_screenshot",
        "description": (
            "Send a previously saved screenshot to a Gemini Flash "
            "sub-agent for detailed visual analysis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_filename": {
                    "type": "string",
                    "description": (
                        "Filename returned by save_screenshot."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": "What to analyze or ask about the image.",
                },
            },
            "required": ["image_filename", "prompt"],
        },
    },
    {
        "name": "generate_image",
        "description": (
            "Generate an image using Nano Banana (Gemini native image "
            "generation). Can also edit or remix an existing screenshot "
            "by providing a reference_filename."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Detailed text description of the image to generate."
                    ),
                },
                "label": {
                    "type": "string",
                    "description": "Short label for the saved filename.",
                },
                "reference_filename": {
                    "type": "string",
                    "description": (
                        "Optional filename from save_screenshot to use "
                        "as a reference for editing or remixing."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
]


class GeminiSession:
    """Wraps a single Gemini Live API WebSocket session."""

    def __init__(self) -> None:
        self._client = genai.Client(api_key=GOOGLE_API_KEY)
        self._ctx = None
        self._session = None
        self._resumption_handle: str | None = None
        self.running = False

        self.on_audio: AudioCallback | None = None
        self.on_tool_call: ToolCallCallback | None = None
        self.on_transcription: TranscriptionCallback | None = None
        self.on_status: StatusCallback | None = None

    def _build_config(self) -> types.LiveConnectConfig:
        """Build session config using SDK types for correctness."""
        resumption = None
        if self._resumption_handle:
            resumption = types.SessionResumptionConfig(
                handle=self._resumption_handle,
            )
        else:
            resumption = types.SessionResumptionConfig()

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(**f)
                        for f in _BROWSER_FUNCTIONS
                    ],
                ),
            ],
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            session_resumption=resumption,
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    async def connect(self) -> None:
        config = self._build_config()
        logger.info("Connecting to Gemini model=%s", MODEL_NAME)
        self._ctx = self._client.aio.live.connect(
            model=MODEL_NAME,
            config=config,
        )
        self._session = await self._ctx.__aenter__()
        self.running = True
        logger.info("Gemini session connected")
        if self.on_status:
            self.on_status("Gemini session connected")

    async def disconnect(self) -> None:
        self.running = False
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:  # pylint: disable=broad-except
                pass
            self._ctx = None
            self._session = None
        logger.info("Gemini session disconnected")

    @property
    def connected(self) -> bool:
        return self._session is not None and self.running

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self.connected:
            return
        try:
            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=pcm_bytes,
                    mime_type="audio/pcm;rate=16000",
                ),
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("send_audio failed (session closed)")

    async def send_screenshot(self, jpeg_bytes: bytes) -> None:
        if not self.connected:
            return
        try:
            await self._session.send_realtime_input(
                video=types.Blob(
                    data=jpeg_bytes,
                    mime_type="image/jpeg",
                ),
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("send_screenshot failed (session closed)")

    async def send_tool_response(
        self,
        responses: list[types.FunctionResponse],
    ) -> None:
        if not self.connected:
            return
        try:
            await self._session.send_tool_response(
                function_responses=responses,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("send_tool_response failed (session closed)")

    async def receive_loop(self) -> None:
        """Yield messages from Gemini until the connection drops."""
        if not self._session:
            raise RuntimeError("Not connected")
        try:
            async for msg in self._session.receive():
                if not self.running:
                    break
                await self._handle_message(msg)
        except asyncio.CancelledError:
            logger.info("Receive loop cancelled")
            raise
        except Exception:  # pylint: disable=broad-except
            logger.exception("Receive loop error")
            if self.on_status:
                self.on_status("Gemini connection lost -- reconnecting")

    async def _handle_message(self, msg: Any) -> None:
        if getattr(msg, "session_resumption_update", None):
            update = msg.session_resumption_update
            if getattr(update, "resumable", False) and getattr(
                update, "new_handle", None
            ):
                self._resumption_handle = update.new_handle
                logger.debug("Stored resumption handle")

        if getattr(msg, "go_away", None):
            logger.warning("GoAway received: %s", msg.go_away)
            if self.on_status:
                self.on_status("Session reconnecting soon...")

        audio_handled = False
        sc = getattr(msg, "server_content", None)
        if sc:
            if getattr(sc, "interrupted", False) and self.on_status:
                self.on_status("(interrupted)")

            model_turn = getattr(sc, "model_turn", None)
            if model_turn:
                for part in model_turn.parts or []:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data and self.on_audio:
                        raw = inline.data
                        if isinstance(raw, str):
                            raw = base64.b64decode(raw)
                        self.on_audio(raw)
                        audio_handled = True

            inp = getattr(sc, "input_transcription", None)
            if inp and getattr(inp, "text", None):
                if inp.text.strip() and self.on_transcription:
                    self.on_transcription("user", inp.text)

            out = getattr(sc, "output_transcription", None)
            if out and getattr(out, "text", None):
                if out.text.strip() and self.on_transcription:
                    self.on_transcription("model", out.text)

        if not audio_handled:
            data = getattr(msg, "data", None)
            if data is not None and self.on_audio:
                raw = data
                if isinstance(raw, str):
                    raw = base64.b64decode(raw)
                self.on_audio(raw)

        tc = getattr(msg, "tool_call", None)
        if tc and self.on_tool_call:
            calls = []
            for fc in tc.function_calls or []:
                calls.append({
                    "id": fc.id,
                    "name": fc.name,
                    "args": dict(fc.args) if fc.args else {},
                })
            if calls:
                await self.on_tool_call(calls)
