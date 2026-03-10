# Gemini Live + Computer Use Demo

A demo application combining **Gemini Live API** (real-time audio/video streaming)
with **Computer Use** (browser automation via Playwright). Speak to the agent,
watch it control a browser, and have it talk back while clicking, typing, and
navigating websites.

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
```

The backend runs three concurrent async tasks:

1. **Audio Relay** -- forwards mic audio from the frontend to Gemini
2. **Screenshot Loop** -- captures Playwright screenshots every ~1 s and sends
   them to Gemini as video frames
3. **Gemini Receiver** -- listens for audio responses, tool calls, and
   transcriptions from Gemini and routes them appropriately
