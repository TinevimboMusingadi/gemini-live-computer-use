## Gemini Live + Computer Use – Architecture & Deployment Notes

This document explains how the agent is wired end‑to‑end, what each
component does, how **Live mode** and **Computer Use–style** behavior are
combined, and why the backend is not a good fit for Vercel’s serverless
runtime.

---

## High‑level picture

- **Goal**: Speak to an agent that:
  - hears you in real time (Live API native audio),
  - sees a browser it controls (periodic screenshots as video frames),
  - takes actions on the web (click, type, scroll, navigate, etc.),
  - can **save screenshots**, analyze them with **Gemini Flash**, and
    generate or edit images with **Nano Banana**.

- **Shape**:
  - **Frontend (browser)** – UI, mic capture, audio playback, and rendering
    of screenshots and transcripts.
  - **Backend (FastAPI)** – owns the **Gemini Live session**, the
    **Playwright browser**, and the sub‑agents (Flash, Nano Banana).
  - **Gemini Live API** – real‑time audio + video + tool calling.
  - **Playwright** – drives a persistent Chromium profile that can stay
    logged into sites (Google, Pinterest, etc.).

---

## Components and responsibilities

### Backend

- **`config.py`**
  - **Live model**: `MODEL_NAME = "gemini-2.5-flash-native-audio-preview-12-2025"`
  - **Sub‑agents**:
    - `FLASH_MODEL = "gemini-2.0-flash"` for screenshot analysis.
    - `NANO_BANANA_MODEL = "gemini-3.1-flash-image-preview"` for native
      image generation/editing.
  - **Screen & paths**:
    - `SCREEN_WIDTH`, `SCREEN_HEIGHT` – fixed viewport used by Playwright.
    - `PROJECT_ROOT`, `SCREENSHOTS_DIR`, `GENERATED_DIR` – where
      screenshots and generated images are stored.
  - **System instruction** – tells the Live agent:
    - it sees the browser via periodic screenshots,
    - which tools it has (`save_screenshot`, `analyze_screenshot`,
      `generate_image`, browser actions),
    - that coordinates are normalized 0–999.

- **`gemini_session.py`**
  - Wraps the **Gemini Live WebSocket** using the official Python SDK.
  - Builds a `LiveConnectConfig` with:
    - **`response_modalities=["AUDIO"]`** – model replies in audio.
    - **Function tools** (not built‑in ComputerUse) declared in
      `_BROWSER_FUNCTIONS`:
      - `navigate`, `click_at`, `type_text_at`, `scroll_document`,
        `go_back`, `key_combination`, `wait_5_seconds`,
        `save_screenshot`, `analyze_screenshot`, `generate_image`.
    - **`context_window_compression`** with sliding window.
    - **Input/output transcription** enabled.
    - **`thinking_config`** set to zero budget (no extra “thoughts”).
  - Methods:
    - **`connect()`** – calls `client.aio.live.connect(...)`, stores
      the async context and session, and marks the session as running.
    - **`send_audio()`** – sends 16‑kHz PCM from the user to the model via
      `send_realtime_input(audio=Blob(...))`.
    - **`send_screenshot()`** – sends JPEG screenshots as
      `send_realtime_input(video=Blob(...))`. This is how the model
      “sees” the browser; Live API treats these as video frames.
    - **`send_tool_response()`** – sends function results back to Gemini.
    - **`receive_loop()`** – core Live receive loop:
      - streams messages from `session.receive()`,
      - emits callbacks:
        - `on_audio` – chunks of model audio,
        - `on_tool_call` – requested tool calls,
        - `on_transcription` – input/output text,
        - `on_status` – connection events,
      - handles session resumption and GoAway signals.

- **`browser_controller.py`**
  - Manages a **persistent Chromium profile** (`playwright-profile`).
  - Uses `chromium.launch_persistent_context(...)` so:
    - logins (Google, Pinterest, etc.) are **remembered** between runs,
    - the agent acts as a logged‑in user after you log in once.
  - Exposes:
    - `launch(url)` – starts Playwright with fixed viewport, opens URL.
    - `screenshot()` – returns a JPEG frame for both Gemini and the UI.
    - `goto(url)` – navigate within the session.
    - `close()` – closes context and Playwright cleanly.

- **`action_executor.py`**
  - Translates tool calls (from Gemini) into **Playwright actions**.
  - Handles **0–999 → pixel coordinate** mapping:
    - `_denormalize_x`, `_denormalize_y` use `SCREEN_WIDTH` /
      `SCREEN_HEIGHT`.
  - Supports actions:
    - `navigate`, `click_at`, `hover_at`, `type_text_at`,
      `scroll_document`, `scroll_at`, `go_back`, `go_forward`,
      `key_combination`, `search`, `wait_5_seconds`, `drag_and_drop`.
  - Returns a dict with a basic `"result"` and sometimes extras (e.g. URL)
    which is sent back as a `FunctionResponse`.

- **`screenshot_store.py`**
  - Persists images:
    - `save(jpeg_bytes, label)` – saves browser screenshots under
      `screenshots/`.
    - `save_generated(image_bytes, label, ext)` – saves generated images
      under `screenshots/generated/`.
  - `list_screenshots()` – returns metadata `{filename, url}` for all
    images (screenshots + generated).
  - `get_path(filename)` – resolves a stored filename to a `Path`.

- **`sub_agents.py`**
  - Connects to **Gemini Flash** and **Nano Banana** using the standard
    (non‑Live) `generate_content` API:
  - `analyze_image(image_filename, prompt)`:
    - Loads a saved screenshot from disk.
    - Calls `FLASH_MODEL` (`gemini-2.0-flash`) with `[image, prompt]`.
    - Returns textual analysis (e.g. “describe this outfit”, “extract the
      data from this chart”).
  - `generate_image(prompt, label, reference_filename)`:
    - Optionally loads a **reference image** for editing/remixing.
    - Calls `NANO_BANANA_MODEL` (`gemini-3.1-flash-image-preview`) via
      `generate_content` with modalities `TEXT` and `IMAGE`.
    - Extracts the first returned image part and saves it via
      `screenshot_store.save_generated(...)`.
    - Returns `{filename, url, text}` so the Live agent can talk about
      the new image.

- **`main.py`**
  - FastAPI app + WebSocket endpoint:
    - `GET /` – main control UI (`index.html`).
    - `GET /agent-home` – **Agent Home** chat UI (`agent_home.html`).
    - `GET /health` – connectivity diagnostics (Gemini + general internet).
    - `GET /api/screenshots` – lists all saved images.
    - `POST /api/upload` – accepts uploaded images from the Agent Home
      page and saves them into the gallery.
    - `StaticFiles` mounts:
      - `/static` – frontend assets.
      - `/screenshots` – saved screenshots and generated images.
    - `WebSocket /ws` – main real‑time bridge to the frontend.
  - Inside `/ws`:
    - Instantiates:
      - `BrowserController`,
      - `GeminiSession`,
      - three async tasks:
        - **screenshot_loop** – sends periodic screenshots to Gemini and
          the frontend,
        - **audio_relay** – forwards mic audio from frontend to Gemini,
        - **gemini_receiver** – reads Live responses and handles
          reconnection.
    - Implements `_on_tool_call`:
      - Routes **browser tools** to `action_executor`.
      - Routes **sub‑agent tools**:
        - `save_screenshot` → `screenshot_store.save(...)`,
        - `analyze_screenshot` → `sub_agents.analyze_image(...)`,
        - `generate_image` → `sub_agents.generate_image(...)`.
      - After each action, sends a `FunctionResponse` back to Gemini and,
        when relevant, notifies the frontend (`gallery_update`).
    - Manages **connectivity awareness**:
      - `check_internet()` probes Gemini + a couple of public endpoints.
      - When Gemini drops, it classifies the situation as
        `offline` / `slow` / `limited` / `good` and informs the frontend
        so the UI can show “it’s your internet, not the agent”.

### Frontend

- **`index.html` / `app.js` / `styles.css`**
  - **Control UI**:
    - URL bar, Connect/Disconnect buttons, mic indicator.
    - Left side: **live screenshot view** of the Playwright browser.
    - Right side: **transcript** of user/model speech + list of actions
      (tool calls) for debugging.
    - Status bar and connectivity banner (good/slow/limited/offline).
  - **WebSocket client**:
    - Connects to `/ws`.
    - Sends:
      - PCM audio chunks (`{type: "audio", data: ...}`).
      - Initial `{type: "connect", url}` specifying the URL the agent’s
        browser should open (defaults to `http://localhost:8000/agent-home`).
    - Receives:
      - audio, screenshot, transcription, action, connectivity, and
        gallery notifications.
  - **Audio handling**:
    - Mic capture via `ScriptProcessorNode` at 16 kHz, encoded to base64,
      sent to backend.
    - Playback of 24 kHz model audio via `AudioContext`, with scheduling
      so chunks play gap‑free.

- **`agent_home.html` / `agent_home.js`**
  - A **chat‑style “home” page** that the agent opens at startup.
  - Layout:
    - **Chat panel**:
      - System + “user” messages (what you type into the widget).
      - Quick‑action chips like “Browse a website”, “Save screenshot”,
        “Analyze an image”, “Generate image”, “Suggest an outfit”.
      - Simple text input and “Send” button – these are just UI helpers;
        the *real* conversation is happening via voice + Live.
    - **Gallery panel**:
      - Thumbnails of all saved screenshots and generated images, loaded
        from `/api/screenshots`.
      - Clicking a thumb shows a larger preview and filename in the chat.
      - Small “Upload” button wired to `/api/upload` for manual image
        uploads.
  - The agent can **see and click** everything on this page via the
    screenshots and its browser tools, just like any other site.

---

## How Live mode and “Computer Use” are combined

- **Live side**:
  - Uses the *real* **Gemini Live API** (bidi WebSocket) with a native
    audio model (`gemini-2.5-flash-native-audio-preview-12-2025`).
  - Audio is streamed in and out in near real time.
  - Screenshots are sent as **video frames** (max ~1 fps) – the official
    way Live handles visual input.

- **Computer Use side**:
  - The pure “ComputerUse” tool (`computer_use=types.ComputerUse(...)`)
    is not available in Live for this model.
  - Instead, we:
    - Declare **custom functions** (`navigate`, `click_at`, etc.) that
      look like Computer Use actions (normalized coordinates, scroll,
      key combos).
    - Let the Live model **see the screen** (screenshots) and **ask for
      those functions** via standard function calling.
    - Run the actions in **Playwright** and send **screenshots +
      FunctionResponses** back, forming the same loop Computer Use would.
  - Result: from the outside, you get the same behavior:
    - Agent visually grounds itself on the page.
    - Agent proposes UI actions.
    - Client executes them and refreshes the visual context.

So the design is:

- **Live API for timing + voice + streaming + tools**,
- **Playwright for actual UI control**,
- **Custom tools** that mimic Computer Use actions,
- **Sub‑agents** (Flash + Nano Banana) for heavy image understanding and
  generation.

---

## Deployment notes – especially Vercel

### What the backend needs

The backend process is a **long‑running app** that:

- Keeps a WebSocket to the frontend (`/ws`).
- Keeps a WebSocket to Gemini Live (via `client.aio.live.connect`).
- Runs Playwright + Chromium **continuously** (browser stays open).
- Performs background `asyncio` tasks (audio relay, screenshot loop,
  Gemini receiver) for the whole lifetime of the session.

This requires:

- A host where a Python process can run **for minutes or hours**, not just
  per‑request.
- Permission and resources to run **Chromium** and Playwright.
- Stable support for **WebSockets** and long‑lived TCP connections.

### Why not Vercel for the backend

Vercel’s primary runtimes (serverless functions and edge functions) are
optimised for:

- **Short‑lived HTTP requests**, not processes that stay alive.
- **Stateless** execution (no persistent Playwright browser).
- Limited or no support for running **full browsers** like Chromium.
- Aggressive timeouts and scaling that can kill long‑running WebSocket
  handlers.

For this project, that would break:

- The **Gemini Live session** (needs a stable connection).
- The **Playwright browser** (needs a long‑lived context).
- The **three‑task agent loop** (would be interrupted by function
  timeouts).

### Recommended deployment split

- **Frontend**:
  - Can be hosted on Vercel as a static or Next.js app:
    - Serve `index.html`, `agent_home.html`, `app.js`, `agent_home.js`,
      `styles.css`.
    - The browser connects to the backend via WebSocket
      (`wss://backend.your-domain.com/ws`).

- **Backend**:
  - Should run on a **long‑running** environment:
    - A VM (e.g. small cloud instance).
    - A container on a platform like Fly.io, Railway, Render, etc.
  - Needs:
    - Python 3.12 + Playwright dependencies.
    - A place to write to disk (`screenshots/`, `playwright-profile/`).
    - Stable WebSocket support and outbound HTTPS to Gemini.

From the user’s perspective, this still looks like a single app:

- They open the Vercel URL in their browser.
- The frontend JS reaches out to the backend.
- All the Live + Computer Use behavior continues to happen on the backend
  machine, just as it does in local development.

---

## Summary

- **Yes, this is a true Live agent** – it uses the Gemini Live WebSocket
  with native audio, ongoing input, and streaming output.
- **Computer Use concepts are implemented via custom tools** plus
  Playwright, because the specific Live model does not expose the
  built‑in Computer Use tool.
- **Sub‑agents** (Gemini Flash and Nano Banana) hang off the side as
  helper tools for screenshot analysis and image generation.
- **Persistent Playwright profile** allows you to log into sites once and
  let the agent operate as a logged‑in user.
- **Deployment**:
  - Frontend can happily live on Vercel.
  - Backend must stay on a full, long‑running environment rather than
    Vercel’s serverless runtime.

