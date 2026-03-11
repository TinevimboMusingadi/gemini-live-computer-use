"""FastAPI application -- WebSocket bridge between the frontend and Gemini."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import pathlib
import socket
import time

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
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

_CONNECTIVITY_TARGETS = [
    ("generativelanguage.googleapis.com", 443),
    ("dns.google", 443),
    ("1.1.1.1", 443),
]


async def check_internet() -> dict:
    """Return a dict with connectivity diagnostics."""
    start = time.monotonic()
    reachable: list[str] = []
    unreachable: list[str] = []
    for host, port in _CONNECTIVITY_TARGETS:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=3,
            )
            writer.close()
            await writer.wait_closed()
            reachable.append(host)
        except (OSError, asyncio.TimeoutError, socket.error):
            unreachable.append(host)
    latency_ms = round((time.monotonic() - start) * 1000)

    gemini_ok = "generativelanguage.googleapis.com" in reachable
    any_ok = len(reachable) > 0
    if not any_ok:
        quality = "offline"
        message = "No internet connection detected"
    elif not gemini_ok:
        quality = "limited"
        message = (
            "Internet works but cannot reach Gemini API -- "
            "possible firewall or DNS issue"
        )
    elif latency_ms > 3000:
        quality = "slow"
        message = f"Internet is very slow (probe took {latency_ms} ms)"
    else:
        quality = "good"
        message = "Internet connection is healthy"

    return {
        "quality": quality,
        "message": message,
        "latency_ms": latency_ms,
        "reachable": reachable,
        "unreachable": unreachable,
    }


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/health")
async def health():
    """Lightweight endpoint for frontend connectivity checks."""
    info = await check_internet()
    return JSONResponse(info)


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
        consecutive_failures = 0
        while True:
            try:
                await gemini.receive_loop()
                consecutive_failures = 0
            except asyncio.CancelledError:
                return
            except Exception:  # pylint: disable=broad-except
                consecutive_failures += 1
                logger.exception("gemini_receiver error")

            if not gemini.running:
                return

            net = await check_internet()
            if net["quality"] == "offline":
                await _send_json({
                    "type": "connectivity",
                    "quality": "offline",
                    "message": net["message"],
                })
                logger.warning("Internet offline -- waiting for recovery")
                while True:
                    await asyncio.sleep(3)
                    net = await check_internet()
                    if net["quality"] != "offline":
                        await _send_json({
                            "type": "connectivity",
                            "quality": net["quality"],
                            "message": "Internet restored -- reconnecting",
                        })
                        break
            elif net["quality"] in ("slow", "limited"):
                await _send_json({
                    "type": "connectivity",
                    "quality": net["quality"],
                    "message": net["message"],
                })
            else:
                await _send_json({
                    "type": "status",
                    "message": "Gemini connection dropped -- reconnecting...",
                })

            logger.info(
                "Gemini receive ended -- reconnecting (net=%s, failures=%d)",
                net["quality"],
                consecutive_failures,
            )
            backoff = min(2 ** consecutive_failures, 16)
            await asyncio.sleep(backoff)

            try:
                await gemini.disconnect()
                await gemini.connect()
                await _send_json({
                    "type": "connectivity",
                    "quality": "good",
                    "message": "Reconnected to Gemini",
                })
                await _send_json({"type": "status", "message": "Session active"})
            except Exception:  # pylint: disable=broad-except
                logger.exception("Reconnect failed")
                net2 = await check_internet()
                if net2["quality"] == "offline":
                    await _send_json({
                        "type": "connectivity",
                        "quality": "offline",
                        "message": "Reconnect failed -- no internet",
                    })
                else:
                    await _send_json({
                        "type": "connectivity",
                        "quality": net2["quality"],
                        "message": (
                            f"Reconnect failed (internet is {net2['quality']})"
                        ),
                    })
                if consecutive_failures >= 5:
                    await _send_json({
                        "type": "error",
                        "message": "Too many failures -- please reconnect",
                    })
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
