"""FastAPI application -- WebSocket bridge between the frontend and Gemini."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import pathlib

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.genai import types

from src.backend import action_executor
from src.backend.browser_controller import BrowserController
from src.backend.config import HOST, PORT, SCREENSHOT_INTERVAL_S
from src.backend.gemini_session import GeminiSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Gemini Live + Computer Use Demo")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    logger.info("Frontend WebSocket connected")

    browser = BrowserController()
    gemini = GeminiSession()
    tasks: list[asyncio.Task] = []

    async def _send_json(payload: dict) -> None:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:  # pylint: disable=broad-except
            pass

    # ---- Gemini callbacks ----

    def _on_audio(pcm: bytes) -> None:
        encoded = base64.b64encode(pcm).decode()
        asyncio.ensure_future(_send_json({"type": "audio", "data": encoded}))

    async def _on_tool_call(calls: list[dict]) -> None:
        for call in calls:
            name = call["name"]
            args = call["args"]
            call_id = call["id"]

            # Check for safety_decision requiring confirmation
            safety = args.pop("safety_decision", None)
            if safety and safety.get("decision") == "require_confirmation":
                await _send_json({
                    "type": "safety_confirm",
                    "explanation": safety.get("explanation", ""),
                    "action": name,
                    "call_id": call_id,
                })
                # For v1 demo we auto-skip; a full implementation would
                # await a user response over the WebSocket.
                logger.warning(
                    "Safety confirmation requested for %s -- skipping action",
                    name,
                )
                fr = types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response={"error": "User denied action"},
                )
                await gemini.send_tool_response([fr])
                continue

            await _send_json({
                "type": "action",
                "name": name,
                "args": args,
            })

            result = await action_executor.execute_action(
                browser.page, name, args,
            )
            result["url"] = browser.page.url

            screenshot_bytes = await browser.screenshot()
            await _send_json({
                "type": "screenshot",
                "data": base64.b64encode(screenshot_bytes).decode(),
            })

            fr = types.FunctionResponse(
                id=call_id,
                name=name,
                response=result,
            )
            await gemini.send_tool_response([fr])

    def _on_transcription(source: str, text: str) -> None:
        asyncio.ensure_future(
            _send_json({"type": "transcription", "source": source, "text": text})
        )

    def _on_status(msg: str) -> None:
        asyncio.ensure_future(_send_json({"type": "status", "message": msg}))

    gemini.on_audio = _on_audio
    gemini.on_tool_call = _on_tool_call
    gemini.on_transcription = _on_transcription
    gemini.on_status = _on_status

    # ---- Async tasks ----

    async def screenshot_loop() -> None:
        """Periodically capture the browser and send to Gemini + frontend."""
        while True:
            try:
                jpeg = await browser.screenshot()
                await gemini.send_screenshot(jpeg)
                encoded = base64.b64encode(jpeg).decode()
                await _send_json({"type": "screenshot", "data": encoded})
            except asyncio.CancelledError:
                return
            except Exception:  # pylint: disable=broad-except
                logger.debug("Screenshot loop iteration error", exc_info=True)
            await asyncio.sleep(SCREENSHOT_INTERVAL_S)

    async def audio_relay() -> None:
        """Read messages from the frontend WS and forward audio to Gemini."""
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "audio":
                    pcm = base64.b64decode(msg["data"])
                    await gemini.send_audio(pcm)
                elif mtype == "disconnect":
                    break
        except WebSocketDisconnect:
            logger.info("Frontend disconnected")
        except asyncio.CancelledError:
            return
        except Exception:  # pylint: disable=broad-except
            logger.exception("audio_relay error")

    async def gemini_receiver() -> None:
        """Keep listening on Gemini; reconnect on transient drops."""
        while True:
            try:
                await gemini.receive_loop()
            except asyncio.CancelledError:
                return
            except Exception:  # pylint: disable=broad-except
                logger.exception("gemini_receiver error")

            if not gemini.running:
                return
            # Attempt reconnect after a transient drop
            logger.info("Gemini receive ended -- attempting reconnect")
            await _send_json({"type": "status", "message": "Reconnecting to Gemini..."})
            try:
                await gemini.disconnect()
                await asyncio.sleep(1)
                await gemini.connect()
                await _send_json({"type": "status", "message": "Session active"})
            except Exception:  # pylint: disable=broad-except
                logger.exception("Reconnect failed")
                await _send_json({"type": "status", "message": "Reconnect failed"})
                return

    # ---- Session lifecycle ----

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            if msg["type"] == "connect":
                url = msg.get("url", "https://www.google.com")
                await _send_json({"type": "status", "message": "Launching browser..."})
                await browser.launch(url)

                await _send_json({"type": "status", "message": "Connecting to Gemini..."})
                await gemini.connect()

                tasks = [
                    asyncio.create_task(screenshot_loop(), name="screenshots"),
                    asyncio.create_task(audio_relay(), name="audio_relay"),
                    asyncio.create_task(gemini_receiver(), name="gemini_rx"),
                ]

                await _send_json({"type": "status", "message": "Session active"})

                # audio_relay is the authority -- when the frontend
                # disconnects it ends, and we tear everything down.
                audio_task = tasks[1]
                await audio_task
                break
    except WebSocketDisconnect:
        logger.info("Frontend WebSocket closed")
    except Exception:  # pylint: disable=broad-except
        logger.exception("Session error")
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await gemini.disconnect()
        await browser.close()
        logger.info("Session fully cleaned up")


if __name__ == "__main__":
    uvicorn.run(
        "src.backend.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
