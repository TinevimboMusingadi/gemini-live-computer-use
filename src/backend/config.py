"""Application configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY: str = os.environ.get("GOOGLE_API_KEY", "")

MODEL_NAME: str = "gemini-2.5-flash-native-audio-preview-12-2025"

SCREEN_WIDTH: int = 1440
SCREEN_HEIGHT: int = 900

HOST: str = "0.0.0.0"
PORT: int = 8000

SCREENSHOT_INTERVAL_S: float = 2.0

SYSTEM_INSTRUCTION: str = (
    "You are a real-time browser assistant. The user is speaking to you "
    "through their microphone and you can see the browser they are looking at "
    "through periodic screenshots.\n\n"
    "When the user asks you to interact with a website (click a button, type "
    "text, navigate somewhere, scroll, etc.), use the computer-use tool "
    "functions to perform the requested actions.\n\n"
    "While executing actions, keep talking to the user to describe what you "
    "are doing. Be concise but helpful.\n\n"
    "If a safety confirmation is required for an action, ask the user for "
    "verbal permission before proceeding.\n\n"
    "Coordinates returned by the computer-use tool are normalized to a "
    "0-999 grid. Your client will denormalize them to actual screen pixels."
)
