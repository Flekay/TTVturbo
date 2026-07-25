"use strict";

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const resultSection = document.getElementById("resultSection");
const player = document.getElementById("player");
const metaEl = document.getElementById("meta");

let mediaRecorder = null;
let chunks = [];
let mimeType = "";

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status" + (cls ? " " + cls : "");
}

function showError(text) {
  errorEl.textContent = text;
  errorEl.hidden = false;
}

function clearError() {
  errorEl.textContent = "";
  errorEl.hidden = true;
}

function pickMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
    "audio/mp4",
  ];
  for (const type of candidates) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }
  return "";
}

startBtn.addEventListener("click", async () => {
  clearError();

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showError("Dieser Browser unterstützt getUserMedia nicht.");
    return;
  }
  if (!window.MediaRecorder) {
    showError("Dieser Browser unterstützt MediaRecorder nicht.");
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showError("Mikrofonzugriff abgelehnt oder nicht verfügbar: " + err.message);
    return;
  }

  mimeType = pickMimeType();
  try {
    mediaRecorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);
  } catch (err) {
    showError("MediaRecorder konnte nicht gestartet werden: " + err.message);
    stream.getTracks().forEach((t) => t.stop());
    return;
  }

  chunks = [];
  mediaRecorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };
  mediaRecorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    uploadRecording();
  };

  mediaRecorder.start();
  startBtn.disabled = true;
  stopBtn.disabled = false;
  resultSection.hidden = true;
  setStatus("Aufnahme läuft …", "recording");
});

stopBtn.addEventListener("click", () => {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
    stopBtn.disabled = true;
    setStatus("Verarbeite Aufnahme …");
  }
});

async function uploadRecording() {
  if (chunks.length === 0) {
    showError("Aufnahme enthält keine Daten.");
    startBtn.disabled = false;
    setStatus("Bereit.");
    return;
  }

  const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
  const ext = (mimeType.includes("webm") && "webm")
    || (mimeType.includes("ogg") && "ogg")
    || (mimeType.includes("mp4") && "mp4")
    || "webm";

  const formData = new FormData();
  formData.append("audio", blob, `recording.${ext}`);

  setStatus("Lade hoch und konvertiere mit FFmpeg …");

  try {
    const response = await fetch("/api/recordings", { method: "POST", body: formData });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      showError("Upload fehlgeschlagen: " + (data.detail || response.statusText));
      setStatus("Fehler.");
      startBtn.disabled = false;
      return;
    }

    player.src = data.url + "?t=" + Date.now();
    metaEl.textContent =
      "Datei: " + data.filename + "\n" +
      "Größe: " + data.size_bytes + " Bytes\n" +
      "Probe: " + (data.probe || "n/a");
    resultSection.hidden = false;
    setStatus("Aufnahme gespeichert und abspielbar.", "done");
  } catch (err) {
    showError("Netzwerkfehler beim Upload: " + err.message);
    setStatus("Fehler.");
  } finally {
    startBtn.disabled = false;
  }
}
