import { useCallback, useEffect, useRef, useState } from "react";

export type RecorderState =
  | "idle"
  | "requesting_permission"
  | "ready"
  | "recording"
  | "uploading"
  | "converting"
  | "completed"
  | "error";

export interface RecorderError {
  message: string;
  kind: "permission" | "device" | "recorder" | "upload" | "unknown";
}

export interface AudioDevice {
  deviceId: string;
  label: string;
}

interface UseRecorderOptions {
  onUploaded?: (filename: string) => void;
  onUploadError?: (message: string) => void;
}

interface UseRecorderApi {
  state: RecorderState;
  error: RecorderError | null;
  durationSeconds: number;
  level: number; // 0..1 peak amplitude
  devices: AudioDevice[];
  selectedDeviceId: string;
  permissionState: PermissionState | "unknown";
  hasMediaRecorder: boolean;
  lastUploadedFilename: string | null;
  selectDevice: (deviceId: string) => void;
  requestPermission: () => Promise<void>;
  start: () => Promise<void>;
  stop: () => void;
  reset: () => void;
}

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
];

function pickMimeType(): string {
  if (typeof window === "undefined" || !window.MediaRecorder) return "";
  for (const type of MIME_CANDIDATES) {
    if (window.MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

function extensionForMimeType(mime: string): string {
  if (mime.includes("webm")) return "webm";
  if (mime.includes("ogg")) return "ogg";
  if (mime.includes("mp4")) return "mp4";
  return "webm";
}

export function useRecorder(options: UseRecorderOptions = {}): UseRecorderApi {
  const { onUploaded, onUploadError } = options;
  const [state, setState] = useState<RecorderState>("idle");
  const [error, setError] = useState<RecorderError | null>(null);
  const [durationSeconds, setDurationSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>("");
  const [permissionState, setPermissionState] = useState<PermissionState | "unknown">("unknown");
  const [lastUploadedFilename, setLastUploadedFilename] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mimeTypeRef = useRef<string>("");
  const durationTimerRef = useRef<number | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(0);
  const onUploadedRef = useRef(onUploaded);
  const onUploadErrorRef = useRef(onUploadError);
  const selectedDeviceRef = useRef<string>("");

  useEffect(() => {
    onUploadedRef.current = onUploaded;
    onUploadErrorRef.current = onUploadError;
  }, [onUploaded, onUploadError]);

  useEffect(() => {
    selectedDeviceRef.current = selectedDeviceId;
  }, [selectedDeviceId]);

  const hasMediaRecorder =
    typeof window !== "undefined" && typeof window.MediaRecorder !== "undefined";

  const stopLevelMeter = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (analyserRef.current) {
      analyserRef.current.disconnect();
      analyserRef.current = null;
    }
    if (audioContextRef.current) {
      void audioContextRef.current.close().catch(() => undefined);
      audioContextRef.current = null;
    }
    setLevel(0);
  }, []);

  const startLevelMeter = useCallback((stream: MediaStream) => {
    try {
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      audioContextRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      analyserRef.current = analyser;
      const buffer = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(buffer);
        // Compute peak amplitude (0..1) from byte data centered at 128.
        let peak = 0;
        for (let i = 0; i < buffer.length; i++) {
          const v = Math.abs(buffer[i] - 128) / 128;
          if (v > peak) peak = v;
        }
        setLevel(peak);
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    } catch {
      // AudioContext not available -> level meter disabled, recording still works.
    }
  }, []);

  const stopDurationTimer = useCallback(() => {
    if (durationTimerRef.current !== null) {
      window.clearInterval(durationTimerRef.current);
      durationTimerRef.current = null;
    }
  }, []);

  const stopStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  const cleanup = useCallback(() => {
    stopDurationTimer();
    stopLevelMeter();
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      try {
        mediaRecorderRef.current.stop();
      } catch {
        // ignore
      }
    }
    mediaRecorderRef.current = null;
    stopStream();
    chunksRef.current = [];
  }, [stopDurationTimer, stopLevelMeter, stopStream]);

  useEffect(() => {
    return () => {
      cleanup();
    };
  }, [cleanup]);

  // Refresh device list whenever permission is granted.
  const refreshDevices = useCallback(async () => {
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      const audioInputs = list
        .filter((d) => d.kind === "audioinput")
        .map((d) => ({
          deviceId: d.deviceId,
          label: d.label || `Gerät ${d.deviceId.slice(0, 6) || "unbekannt"}`,
        }));
      setDevices(audioInputs);
      if (!selectedDeviceRef.current && audioInputs.length > 0) {
        setSelectedDeviceId(audioInputs[0].deviceId);
      }
    } catch {
      // ignore enumeration errors
    }
  }, []);

  const requestPermission = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setError({ message: "Dieser Browser unterstützt getUserMedia nicht.", kind: "permission" });
      setState("error");
      return;
    }
    if (!hasMediaRecorder) {
      setError({ message: "Dieser Browser unterstützt MediaRecorder nicht.", kind: "recorder" });
      setState("error");
      return;
    }
    setState("requesting_permission");
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      setPermissionState("granted");
      await refreshDevices();
      // Stop the permission stream; the real recording stream is opened on start().
      stream.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      setState("ready");
    } catch (err) {
      const name = (err as DOMException)?.name;
      let message = "Mikrofonzugriff fehlgeschlagen.";
      if (name === "NotAllowedError" || name === "SecurityError") {
        message = "Mikrofonzugriff wurde abgelehnt. Bitte in den Browser-Einstellungen erlauben.";
        setPermissionState("denied");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        message = "Kein Mikrofon gefunden.";
      } else if (err instanceof Error) {
        message = err.message;
      }
      setError({ message, kind: "permission" });
      setState("error");
    }
  }, [hasMediaRecorder, refreshDevices]);

  const start = useCallback(async () => {
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || !hasMediaRecorder) {
      await requestPermission();
      return;
    }
    try {
      const constraints: MediaStreamConstraints = {
        audio: selectedDeviceRef.current
          ? { deviceId: { exact: selectedDeviceRef.current } }
          : true,
      };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;
      setPermissionState("granted");
      await refreshDevices();

      const mimeType = pickMimeType();
      mimeTypeRef.current = mimeType;
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stopDurationTimer();
        stopLevelMeter();
        stopStream();
        void upload();
      };
      recorder.onerror = () => {
        setError({ message: "MediaRecorder-Fehler.", kind: "recorder" });
        setState("error");
        cleanup();
      };

      recorder.start();
      startTimeRef.current = performance.now();
      setDurationSeconds(0);
      durationTimerRef.current = window.setInterval(() => {
        setDurationSeconds((performance.now() - startTimeRef.current) / 1000);
      }, 100);
      startLevelMeter(stream);
      setState("recording");
    } catch (err) {
      const name = (err as DOMException)?.name;
      let message = "Aufnahme konnte nicht gestartet werden.";
      if (name === "NotAllowedError") {
        message = "Mikrofonzugriff abgelehnt.";
        setPermissionState("denied");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        message = "Das gewählte Audiogerät ist nicht mehr verfügbar.";
      } else if (err instanceof Error) {
        message = err.message;
      }
      setError({ message, kind: "device" });
      setState("error");
      cleanup();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cleanup, hasMediaRecorder, requestPermission, startLevelMeter, stopDurationTimer, stopLevelMeter, stopStream, refreshDevices]);

  const upload = useCallback(async () => {
    const chunks = chunksRef.current;
    if (chunks.length === 0) {
      setError({ message: "Aufnahme enthält keine Daten.", kind: "recorder" });
      setState("error");
      return;
    }
    const mime = mimeTypeRef.current;
    const blob = new Blob(chunks, { type: mime || "audio/webm" });
    const ext = extensionForMimeType(mime);
    const filename = `recording.${ext}`;
    setState("uploading");
    try {
      const formData = new FormData();
      formData.append("audio", blob, filename);
      const resp = await fetch("/api/recordings", { method: "POST", body: formData });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const message =
          (data && typeof data === "object" && "detail" in data
            ? String((data as { detail: unknown }).detail)
            : resp.statusText) || "Upload fehlgeschlagen.";
        setError({ message, kind: "upload" });
        setState("error");
        onUploadErrorRef.current?.(message);
        return;
      }
      const uploadedFilename =
        (data && typeof data === "object" && "filename" in data
          ? String((data as { filename: unknown }).filename)
          : null) ?? null;
      setLastUploadedFilename(uploadedFilename);
      setState("completed");
      if (uploadedFilename) onUploadedRef.current?.(uploadedFilename);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Netzwerkfehler beim Upload.";
      setError({ message, kind: "upload" });
      setState("error");
      onUploadErrorRef.current?.(message);
    } finally {
      chunksRef.current = [];
    }
  }, []);

  const stop = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        // ignore
      }
    }
  }, []);

  const reset = useCallback(() => {
    cleanup();
    setError(null);
    setDurationSeconds(0);
    setLevel(0);
    setState("idle");
  }, [cleanup]);

  const selectDevice = useCallback((deviceId: string) => {
    setSelectedDeviceId(deviceId);
    selectedDeviceRef.current = deviceId;
  }, []);

  // Listen for device changes while the page is open.
  useEffect(() => {
    if (!navigator.mediaDevices?.addEventListener) return;
    const handler = () => void refreshDevices();
    navigator.mediaDevices.addEventListener("devicechange", handler);
    return () => {
      navigator.mediaDevices?.removeEventListener?.("devicechange", handler);
    };
  }, [refreshDevices]);

  return {
    state,
    error,
    durationSeconds,
    level,
    devices,
    selectedDeviceId,
    permissionState,
    hasMediaRecorder,
    lastUploadedFilename,
    selectDevice,
    requestPermission,
    start,
    stop,
    reset,
  };
}
