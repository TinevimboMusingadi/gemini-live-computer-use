/**
 * Agent Home -- chat UI and image gallery logic.
 *
 * This page is rendered INSIDE the Playwright browser controlled by the
 * Live agent. The agent interacts with it by clicking, typing, and
 * reading the screen via screenshots.
 */

const chatMessages = document.getElementById("chat-messages");
const chatInput = document.getElementById("chat-input");
const btnSend = document.getElementById("btn-send");
const btnRefresh = document.getElementById("btn-refresh");
const galleryGrid = document.getElementById("gallery-grid");
const galleryUpload = document.getElementById("gallery-upload");
const quickBtns = document.querySelectorAll(".quick-btn");

function addMessage(role, html) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = html;
  chatMessages.appendChild(el);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function sendChat() {
  const text = chatInput.value.trim();
  if (!text) return;
  addMessage("user-action", text);
  chatInput.value = "";
}

btnSend.addEventListener("click", sendChat);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendChat();
});

quickBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    const action = btn.dataset.action;
    const prompts = {
      browse: "Navigate to a website for me",
      screenshot: "Save a screenshot of what you see",
      analyze: "Analyze the latest screenshot",
      generate: "Generate an image based on what you see",
      outfit: "Look at this page and suggest an outfit, then generate it",
    };
    const text = prompts[action] || action;
    chatInput.value = text;
    sendChat();
  });
});

async function loadGallery() {
  try {
    const res = await fetch("/api/screenshots");
    const items = await res.json();
    galleryGrid.innerHTML = "";
    if (!items.length) {
      galleryGrid.innerHTML =
        '<div class="gallery-empty">No images yet. Save a screenshot or generate an image.</div>';
      return;
    }
    for (const item of items) {
      const thumb = document.createElement("div");
      thumb.className = "gallery-thumb";
      thumb.title = item.filename;
      thumb.innerHTML = '<img src="' + item.url + '" alt="' + item.filename + '" loading="lazy" />';
      thumb.addEventListener("click", () => {
        addMessage("system", '<strong>' + item.filename + '</strong><br/><img src="' + item.url + '" />');
      });
      galleryGrid.appendChild(thumb);
    }
  } catch {
    galleryGrid.innerHTML = '<div class="gallery-empty">Failed to load gallery.</div>';
  }
}

btnRefresh.addEventListener("click", loadGallery);

galleryUpload.addEventListener("change", async () => {
  const file = galleryUpload.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    const meta = await res.json();
    addMessage("system", "Uploaded: <strong>" + meta.filename + "</strong>");
    loadGallery();
  } catch {
    addMessage("system", "Upload failed.");
  }
  galleryUpload.value = "";
});

loadGallery();
setInterval(loadGallery, 5000);
