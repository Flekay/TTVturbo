import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GenerationList } from "../components/voiceClone/GenerationList";
import { VoiceClonePage } from "../pages/VoiceClonePage";
import { AppLayout } from "../components/layout/AppLayout";
import { renderWithProviders, installFetchMock } from "../test/test-utils";
import type { BackendStatus } from "../types/status";

const status: BackendStatus = {
  status: "online",
  app_name: "TTVturbo",
  version: "0.1.0",
  uptime_seconds: 1,
  recordings: { count: 1, total_duration_seconds: 5.2, total_size_bytes: 1024 },
  storage: { free_bytes: 1000 },
  features: {
    recording: "available",
    voice_cloning: "available",
    vod_analysis: "not_implemented",
    video_editor: "not_implemented",
  },
};

const voiceCloneStatusIdle = {
  available: true,
  busy: false,
  active_generation_id: null,
  model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
};

const goodQuality = {
  technical: { sample_rate: 44100, channels: 1, frame_count: 220500, duration_seconds: 5.0, subtype: "PCM_16", format: "WAV" },
  levels: { peak_dbfs: -10, rms_dbfs: -20, dc_offset: 0, clipping_sample_count: 0, clipping_sample_ratio: 0 },
  silence: { leading_silence_ms: 100, trailing_silence_ms: 100, total_silence_ratio: 0.1, voice_ratio: 0.9, frame_count_total: 500, frame_count_silent: 50, frame_count_active: 450 },
  noise: { estimated_noise_floor_dbfs: -60, estimated_snr_db: 40, active_frames_used: 450 },
  dropouts: { dropout_count: 0, dropout_total_ms: 0, longest_dropout_ms: 0 },
  integrity: { has_nan: false, has_infinity: false },
  quality: "GOOD",
  reasons: [],
  warnings: [],
  voice_clone_reference: { eligible: true, quality: "GOOD", reasons: [], warnings: [] },
};

function readyGen(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "gen-ready-001",
    status: "READY",
    reference_recording: "ref.wav",
    reference_sha256: "abc",
    reference_text: "Ref",
    target_text: "Dies ist der Zieltext.",
    language: "German",
    model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    model_revision: "rev123",
    created_at: "2026-01-01T12:00:00+00:00",
    completed_at: "2026-01-01T12:01:00+00:00",
    output_duration_seconds: 4.8,
    generation_seconds: 7.2,
    peak_vram_bytes: 10100000000,
    quality: { quality: "GOOD" },
    failure_reason: null,
    warnings: [],
    ...overrides,
  };
}

function renderVoiceClonePage() {
  return renderWithProviders(
    <AppLayout>
      <VoiceClonePage />
    </AppLayout>,
    { initialEntries: ["/voice-clone"] },
  );
}

async function switchToManualUpload() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Manueller Upload" }));
  return user;
}

describe("Frontend hardening - audio URL stability", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [readyGen()] });
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
  });

  afterEach(() => mock.restore());

  it("generation audio URL stays stable across re-renders (no Date.now cache buster)", async () => {
    renderVoiceClonePage();
    const audio = await screen.findByLabelText(/Audio-Player für Generierung/);
    const src1 = audio.getAttribute("src");
    expect(src1).toBe("/api/voice-clone/generations/gen-ready-001/audio");
    expect(src1).not.toContain("?t=");
    expect(src1).not.toContain("Date");
  });

  it("reference audio URL is stable and has no cache buster", async () => {
    mock.setResponse("POST /api/recordings", 200, {
      filename: "uploaded.wav",
      url: "/api/recordings/uploaded.wav",
      size_bytes: 1024,
    });
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/uploaded.wav",
      200,
      goodQuality,
    );
    renderVoiceClonePage();
    const user = await switchToManualUpload();
    const fileInput = screen.getByLabelText("Referenzaufnahme hochladen");
    const file = new File(["audio-data"], "clip.wav", { type: "audio/wav" });
    await user.upload(fileInput, file);
    const refAudio = await screen.findByLabelText(/Referenz uploaded.wav abspielen/i);
    const src = refAudio.getAttribute("src");
    expect(src).toBe("/api/recordings/uploaded.wav");
    expect(src).not.toContain("?t=");
  });
});

describe("Frontend hardening - Zod validation", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
  });

  afterEach(() => mock.restore());

  it("accepts a well-formed quality response", async () => {
    mock.setResponse("POST /api/recordings", 200, {
      filename: "uploaded.wav",
      url: "/api/recordings/uploaded.wav",
      size_bytes: 1024,
    });
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/uploaded.wav",
      200,
      goodQuality,
    );
    renderVoiceClonePage();
    const user = await switchToManualUpload();
    const fileInput = screen.getByLabelText("Referenzaufnahme hochladen");
    const file = new File(["audio-data"], "clip.wav", { type: "audio/wav" });
    await user.upload(fileInput, file);
    expect(await screen.findByText(/Qualität: Gut/i)).toBeInTheDocument();
  });

  it("shows an error state for an invalid quality response instead of crashing", async () => {
    mock.setResponse("POST /api/recordings", 200, {
      filename: "uploaded.wav",
      url: "/api/recordings/uploaded.wav",
      size_bytes: 1024,
    });
    // Missing required nested fields -> Zod parse fails.
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/uploaded.wav",
      200,
      { technical: { sample_rate: 44100 }, quality: "GOOD" },
    );
    renderVoiceClonePage();
    const user = await switchToManualUpload();
    const fileInput = screen.getByLabelText("Referenzaufnahme hochladen");
    const file = new File(["audio-data"], "clip.wav", { type: "audio/wav" });
    await user.upload(fileInput, file);
    expect(await screen.findByText(/Qualitätsanalyse fehlgeschlagen/i)).toBeInTheDocument();
    // The form must not crash and the submit button stays in the DOM.
    expect(screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i })).toBeInTheDocument();
  });
});

describe("Frontend hardening - runtime availability", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
  });

  afterEach(() => mock.restore());

  it("disables Generate and lists reasons when runtime is unavailable", async () => {
    mock.setResponse("GET /api/voice-clone/status", 200, {
      ...voiceCloneStatusIdle,
      available: false,
      reasons: [
        "Qwen3-TTS ist nicht installiert.",
        "PyTorch besitzt keine CUDA-Unterstützung.",
      ],
    });
    renderVoiceClonePage();
    expect(await screen.findByText(/Voice Clone ist aktuell nicht verfügbar/i)).toBeInTheDocument();
    expect(await screen.findByText("Qwen3-TTS ist nicht installiert.")).toBeInTheDocument();
    expect(screen.getByText("PyTorch besitzt keine CUDA-Unterstützung.")).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    expect(submit).toBeDisabled();
  });

  it("busy status blocks a second generation and shows the active id", async () => {
    mock.setResponse("GET /api/voice-clone/status", 200, {
      ...voiceCloneStatusIdle,
      busy: true,
      active_generation_id: "other-gen-abc",
    });
    renderVoiceClonePage();
    const submit = await screen.findByRole("button", { name: /Generierung läuft/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/blockiert/i)).toBeInTheDocument();
    // The active generation id is shown truncated to 12 chars.
    expect(screen.getByText("other-gen-ab")).toBeInTheDocument();
  });
});

describe("Frontend hardening - GenerationList unit", () => {
  it("renders a READY generation with stable audio src and no cache buster", async () => {
    const mock = installFetchMock();
    mock.setResponse("GET /api/voice-clone/generations", 200, {
      generations: [readyGen()],
    });
    const { container } = renderWithProviders(<GenerationList />);
    await waitFor(() => expect(container.querySelector("audio")).not.toBeNull());
    const audio = container.querySelector("audio");
    const src = audio!.getAttribute("src");
    expect(src).toBe("/api/voice-clone/generations/gen-ready-001/audio");
    expect(src).not.toContain("?t=");
    mock.restore();
  });

  it("renders technical details only when present and omits missing fields", async () => {
    const mock = installFetchMock();
    mock.setResponse("GET /api/voice-clone/generations", 200, {
      generations: [
        readyGen({
          id: "gen-tech-001",
          output_sha256: "abcdef1234567890",
          output_sample_rate: 24000,
          worker_exit_code: 0,
          device_name: "NVIDIA RTX 5070",
        }),
      ],
    });
    renderWithProviders(<GenerationList />);
    await screen.findByText(/Dies ist der Zieltext/i);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Technische Details/i }));
    expect(screen.getByText("NVIDIA RTX 5070")).toBeInTheDocument();
    expect(screen.getByText(/abcdef123456/)).toBeInTheDocument();
    expect(screen.getByText("24000 Hz")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    mock.restore();
  });

  it("omits optional technical fields that are not supplied by the backend", async () => {
    const mock = installFetchMock();
    mock.setResponse("GET /api/voice-clone/generations", 200, {
      generations: [
        readyGen({
          id: "gen-min-001",
          peak_vram_bytes: null,
          model_revision: "unknown",
        }),
      ],
    });
    renderWithProviders(<GenerationList />);
    await screen.findByText(/Dies ist der Zieltext/i);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Technische Details/i }));
    expect(screen.queryByText("NVIDIA RTX 5070")).toBeNull();
    mock.restore();
  });
});
