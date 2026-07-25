"use strict";

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const resultSection = document.getElementById("resultSection");
const player = document.getElementById("player");
const metaEl = document.getElementById("meta");
const recordingsList = document.getElementById("recordingsList");
const recordingsStatus = document.getElementById("recordingsStatus");

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
    loadRecordings();
  } catch (err) {
    showError("Netzwerkfehler beim Upload: " + err.message);
    setStatus("Fehler.");
  } finally {
    startBtn.disabled = false;
  }
}

function formatDuration(seconds) {
  if (typeof seconds !== "number" || !isFinite(seconds)) return "?";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.round((seconds - Math.floor(seconds)) * 100);
  return m + ":" + String(s).padStart(2, "0") + "." + String(ms).padStart(2, "0");
}

function formatSize(bytes) {
  if (typeof bytes !== "number") return "?";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

function formatCreatedAt(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch (err) {
    return iso;
  }
}

async function loadRecordings() {
  recordingsStatus.textContent = "Lade Aufnahmen …";
  recordingsStatus.hidden = false;
  recordingsList.innerHTML = "";
  try {
    const resp = await fetch("/api/recordings");
    if (!resp.ok) {
      recordingsStatus.textContent = "Fehler beim Laden der Aufnahmen.";
      return;
    }
    const data = await resp.json();
    const items = data.recordings || [];
    if (items.length === 0) {
      recordingsStatus.textContent = "Noch keine Aufnahmen vorhanden.";
      return;
    }
    recordingsStatus.hidden = true;
    for (const rec of items) {
      recordingsList.appendChild(renderRecordingItem(rec));
    }
  } catch (err) {
    recordingsStatus.textContent = "Netzwerkfehler: " + err.message;
  }
}

function renderRecordingItem(rec) {
  const li = document.createElement("li");
  li.className = "recording-item";
  li.dataset.filename = rec.filename;

  const meta = document.createElement("p");
  meta.className = "recording-meta";
  meta.textContent =
    "Erstellt: " + formatCreatedAt(rec.created_at) + "\n" +
    "Dauer: " + formatDuration(rec.duration_seconds) + "\n" +
    "Größe: " + formatSize(rec.file_size_bytes) + "\n" +
    "Datei: " + rec.filename;

  const audio = document.createElement("audio");
  audio.controls = true;
  audio.src = rec.audio_url + "?t=" + Date.now();

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "delete-btn";
  delBtn.textContent = "Löschen";
  delBtn.addEventListener("click", () => deleteRecording(rec.filename, li));

  li.appendChild(meta);
  li.appendChild(audio);
  li.appendChild(delBtn);
  return li;
}

async function deleteRecording(filename, li) {
  if (!confirm("Aufnahme '" + filename + "' wirklich löschen?")) return;
  try {
    const resp = await fetch("/api/recordings/" + encodeURIComponent(filename), {
      method: "DELETE",
    });
    if (resp.status === 404) {
      showError("Datei nicht mehr vorhanden: " + filename);
    } else if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      showError("Löschen fehlgeschlagen: " + (data.detail || resp.statusText));
      return;
    }
    li.remove();
    if (recordingsList.children.length === 0) {
      recordingsStatus.textContent = "Noch keine Aufnahmen vorhanden.";
      recordingsStatus.hidden = false;
    }
  } catch (err) {
    showError("Netzwerkfehler beim Löschen: " + err.message);
  }
}

loadRecordings();
