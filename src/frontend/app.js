/**
 * Gemini Live + Computer Use -- frontend application.
 *
 * Handles:
 *  - WebSocket communication with the Python backend
 *  - Microphone capture (16 kHz 16-bit PCM via ScriptProcessorNode)
 *  - Audio playback of model responses (24 kHz 16-bit PCM)
 *  - Screenshot display and transcript rendering
 */

// ---- DOM refs ----
const btnConnect = document.getElementById("btn-connect");
const btnDisconnect = document.getElementById("btn-disconnect");
const urlInput = document.getElementById("url-input");
const screenshotImg = document.getElementById("browser-screenshot");
const placeholder = document.getElementById("browser-placeholder");
const transcriptList = document.getElementById("transcript-list");
const actionsList = document.getElementById("actions-list");
const statusText = document.getElementById("status-text");
const micIndicator = document.getElementById("mic-indicator");

// ---- State ----
let ws = null;
let audioCtx = null;
let micStream = null;
let micProcessor = null;
let nextPlayTime = 0;

// ---- Helpers ----

function setStatus(msg) {
  statusText.textContent = msg;
}

function addTranscript(source, text) {
  const el = document.createElement("div");
  el.className = "transcript-entry " + source;
  const label = source === "user" ? "You" : "Gemini";
  el.innerHTML = "<strong>" + label + ":</strong> " + text;
  transcriptList.appendChild(el);
  transcriptList.scrollTop = transcriptList.scrollHeight;
}

function addAction(name, args) {
  const el = document.createElement("div");
  el.className = "action-entry";
  el.textContent = name + "(" + JSON.stringify(args) + ")";
  actionsList.appendChild(el);
  actionsList.scrollTop = actionsList.scrollHeight;
}

// ---- Audio playback (24 kHz PCM) ----
// Schedule each chunk to play immediately after the previous one so there
// are no gaps between buffers.

function enqueueAudio(base64Pcm) {
  const raw = atob(base64Pcm);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    bytes[i] = raw.charCodeAt(i);
  }

  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: 24000,
    });
    nextPlayTime = 0;
  }

  const int16 = new Int16Array(bytes.buffer);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768;
  }

  const abuf = audioCtx.createBuffer(1, float32.length, 24000);
  abuf.getChannelData(0).set(float32);

  const src = audioCtx.createBufferSource();
  src.buffer = abuf;
  src.connect(audioCtx.destination);

  const now = audioCtx.currentTime;
  const startAt = Math.max(now, nextPlayTime);
  src.start(startAt);
  nextPlayTime = startAt + abuf.duration;
}

// ---- Microphone capture (16 kHz PCM) ----

async function startMicrophone() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      sampleRate: 16000,
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
    },
  });

  const ctx = new AudioContext({ sampleRate: 16000 });
  const source = ctx.createMediaStreamSource(micStream);

  // ScriptProcessorNode for wide browser compat (AudioWorklet would be ideal
  // but requires HTTPS or localhost + separate worklet file).
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  processor.onaudioprocess = (e) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const float32 = e.inputBuffer.getChannelData(0);
    const int16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      let s = Math.max(-1, Math.min(1, float32[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    const b64 = arrayBufferToBase64(int16.buffer);
    ws.send(JSON.stringify({ type: "audio", data: b64 }));
  };
  source.connect(processor);
  processor.connect(ctx.destination);
  micProcessor = { ctx, processor, source };
  micIndicator.classList.remove("off");
  micIndicator.classList.add("on");
}

function stopMicrophone() {
  if (micProcessor) {
    micProcessor.processor.disconnect();
    micProcessor.source.disconnect();
    micProcessor.ctx.close();
    micProcessor = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  micIndicator.classList.remove("on");
  micIndicator.classList.add("off");
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

// ---- WebSocket ----

function openWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(proto + "://" + location.host + "/ws");

  ws.onopen = async () => {
    setStatus("Connected to backend");
    const url = urlInput.value.trim() || "https://www.google.com";
    ws.send(JSON.stringify({ type: "connect", url }));
    try {
      await startMicrophone();
    } catch (err) {
      setStatus("Mic error: " + err.message + " (session continues without mic)");
      console.error("Microphone error:", err);
    }
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
      case "audio":
        enqueueAudio(msg.data);
        break;
      case "screenshot":
        screenshotImg.src = "data:image/jpeg;base64," + msg.data;
        screenshotImg.style.display = "block";
        placeholder.style.display = "none";
        break;
      case "transcription":
        addTranscript(msg.source, msg.text);
        break;
      case "action":
        addAction(msg.name, msg.args);
        break;
      case "safety_confirm":
        addTranscript(
          "model",
          "[Safety] " + msg.explanation + " -- Action: " + msg.action,
        );
        break;
      case "status":
        setStatus(msg.message);
        break;
      case "error":
        setStatus("Error: " + msg.message);
        break;
    }
  };

  ws.onclose = () => {
    setStatus("Disconnected");
    cleanup();
  };

  ws.onerror = () => {
    setStatus("WebSocket error");
    cleanup();
  };
}

function closeWebSocket() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "disconnect" }));
    ws.close();
  }
  cleanup();
}

function cleanup() {
  stopMicrophone();
  if (audioCtx) {
    audioCtx.close().catch(() => {});
    audioCtx = null;
  }
  nextPlayTime = 0;
  btnConnect.disabled = false;
  btnDisconnect.disabled = true;
  ws = null;
}

// ---- Button handlers ----

btnConnect.addEventListener("click", () => {
  btnConnect.disabled = true;
  btnDisconnect.disabled = false;
  transcriptList.innerHTML = "";
  actionsList.innerHTML = "";
  setStatus("Connecting...");
  openWebSocket();
});

btnDisconnect.addEventListener("click", () => {
  closeWebSocket();
});
