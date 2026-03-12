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
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.genai import types

from src.backend import action_executor, screenshot_store, sub_agents
from src.backend.browser_controller import BrowserController
from src.backend.config import HOST, PORT, SCREENSHOT_INTERVAL_S, SCREENSHOTS_DIR
from src.backend.gemini_session import GeminiSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Gemini Live + Computer Use Demo")

SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
(SCREENSHOTS_DIR / "generated").mkdir(parents=True, exist_ok=True)

app.mount(
    "/screenshots",
    StaticFiles(directory=str(SCREENSHOTS_DIR)),
    name="screenshots",
)
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


@app.get("/agent-home")
async def agent_home():
    """The custom chat UI the agent opens as its default tab."""
    return FileResponse(str(FRONTEND_DIR / "agent_home.html"))


@app.get("/api/screenshots")
async def api_screenshots():
    """Return a JSON list of all saved screenshots and generated images."""
    return JSONResponse(screenshot_store.list_screenshots())


@app.post("/api/upload")
async def api_upload(file: UploadFile):
    """Accept an image upload and save it to the screenshots directory."""
    data = await file.read()
    ext = (file.filename or "upload.jpg").rsplit(".", 1)[-1]
    label = (file.filename or "upload").rsplit(".", 1)[0]
    if ext in ("png", "gif", "webp"):
        meta = await screenshot_store.save_generated(data, label=label, ext=ext)
    else:
        meta = await screenshot_store.save(data, label=label)
    return JSONResponse(meta)


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

    _SUB_AGENT_TOOLS = {"save_screenshot", "analyze_screenshot", "generate_image"}

    async def _on_tool_call(calls: list[dict]) -> None:
        for call in calls:
            name = call["name"]
            args = call["args"]
            call_id = call["id"]

            safety = args.pop("safety_decision", None)
            if safety and safety.get("decision") == "require_confirmation":
                await _send_json({
                    "type": "safety_confirm",
                    "explanation": safety.get("explanation", ""),
                    "action": name,
                    "call_id": call_id,
                })
                logger.warning(
                    "Safety confirmation requested for %s -- skipping",
                    name,
                )
                fr = types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response={"error": "User denied action"},
                )
                await gemini.send_tool_response([fr])
                continue

            await _send_json({"type": "action", "name": name, "args": args})

            if name in _SUB_AGENT_TOOLS:
                result = await _handle_sub_agent_tool(name, args)
            else:
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

    async def _handle_sub_agent_tool(
        name: str,
        args: dict,
    ) -> dict:
        if name == "save_screenshot":
            jpeg = await browser.screenshot()
            meta = await screenshot_store.save(
                jpeg,
                label=args.get("label", ""),
            )
            await _send_json({"type": "gallery_update"})
            return {"result": "Screenshot saved", **meta}

        if name == "analyze_screenshot":
            result = await sub_agents.analyze_image(
                image_filename=args["image_filename"],
                prompt=args["prompt"],
            )
            return result

        if name == "generate_image":
            result = await sub_agents.generate_image(
                prompt=args["prompt"],
                label=args.get("label", ""),
                reference_filename=args.get("reference_filename", ""),
            )
            if "url" in result:
                await _send_json({"type": "gallery_update"})
            return result

        return {"error": f"Unknown sub-agent tool: {name}"}

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
