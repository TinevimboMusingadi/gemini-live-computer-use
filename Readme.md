# Gemini Live + Computer Use Demo

A demo application combining **Gemini Live API** (real-time audio/video streaming)
with **Computer Use** (browser automation via Playwright). Speak to the agent,
watch it control a browser, and have it talk back while clicking, typing, and
navigating websites.

## Features

- **Real-time voice conversation** -- talk to the agent through your microphone
  while it controls a browser
- **Browser automation** -- navigate, click, type, scroll, and more via
  Playwright
- **Screenshot storage** -- the agent can save browser screenshots locally for
  later use
- **Sub-agent tools** -- the Live agent can delegate tasks to other models:
  - **Gemini Flash** for detailed visual analysis of saved screenshots
  - **Nano Banana** (`gemini-3.1-flash-image-preview`) for native image
    generation and editing, with optional reference image support
- **Agent Home** -- a custom chat-style dashboard with an image gallery that
  the agent opens by default
- **Internet connectivity checks** -- live diagnostics so you know if a
  disconnect is your internet or the Gemini API

## Prerequisites

- Python 3.12+
- A Google API key with access to Gemini models

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 3. Configure your API key
cp .env.example .env
# Edit .env and add your GOOGLE_API_KEY

# 4. Run the application
python -m src.backend.main
```

Open http://localhost:8000 in your browser, click **Connect**, grant microphone
access, and start talking to the agent.

## Architecture

```
[Browser Frontend] <--WebSocket--> [Python Backend (FastAPI)] <--WebSocket--> [Gemini Live API]
                                            |
                                   [Playwright Browser]
                                            |
                              [Sub-agents: Flash, Nano Banana]
```

The backend runs three concurrent async tasks:

1. **Audio Relay** -- forwards mic audio from the frontend to Gemini
2. **Screenshot Loop** -- captures Playwright screenshots every ~2 s and sends
   them to Gemini as video frames
3. **Gemini Receiver** -- listens for audio responses, tool calls, and
   transcriptions from Gemini and routes them appropriately

## Agent Tools

| Tool | Description |
|------|-------------|
| `navigate` | Open a URL in the browser |
| `click_at` | Click at normalized (x, y) coordinates |
| `type_text_at` | Click and type text |
| `scroll_document` | Scroll the page |
| `save_screenshot` | Capture the browser and save locally |
| `analyze_screenshot` | Send a saved image to Gemini Flash for analysis |
| `generate_image` | Generate an image with Nano Banana |

## Project Structure

```
src/
├── backend/
│   ├── action_executor.py   # Maps tool calls to Playwright actions
│   ├── browser_controller.py # Playwright browser lifecycle
│   ├── config.py             # Environment and model configuration
│   ├── gemini_session.py     # Gemini Live API session manager
│   ├── main.py               # FastAPI app and WebSocket bridge
│   ├── screenshot_store.py   # Save/list/serve screenshots
│   └── sub_agents.py         # Gemini Flash + Nano Banana sub-agents
└── frontend/
    ├── agent_home.html       # Chat UI the agent opens by default
    ├── agent_home.js         # Agent home page logic
    ├── app.js                # Main frontend WebSocket and audio
    ├── index.html            # Control panel UI
    └── styles.css            # Styling
```
