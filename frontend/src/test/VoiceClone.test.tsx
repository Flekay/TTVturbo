import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

const reviewQuality = {
  ...goodQuality,
  quality: "REVIEW",
  warnings: ["Low estimated SNR 5.0 dB (threshold 15 dB)."],
  voice_clone_reference: { eligible: false, quality: "REVIEW", reasons: [], warnings: ["Low estimated SNR 5.0 dB (threshold 15 dB)."] },
};

const rejectQuality = {
  ...goodQuality,
  quality: "REJECT",
  reasons: ["Near-complete silence."],
  voice_clone_reference: { eligible: false, quality: "REJECT", reasons: ["Near-complete silence."], warnings: [] },
};

const profileWithAcceptedRef = {
  id: "p1",
  name: "Meine Stimme",
  locale: "de-DE",
  created_at: "2026-01-01T00:00:00+00:00",
  archived: false,
  references: {
    s1: {
      script_id: "s1",
      script_text: "Hallo und willkommen.",
      category: "greeting",
      style: "neutral",
      recording_filename: "ref.wav",
      recording_sha256: "abc",
      quality: { voice_clone_reference: { quality: "GOOD" } },
      quality_class: "GOOD",
      status: "ACCEPTED",
      review_accepted: false,
      attached_at: "2026-01-01T00:00:00+00:00",
      updated_at: "2026-01-01T00:00:00+00:00",
    },
  },
  progress: {
    total: 1,
    accepted: 1,
    review: 0,
    rejected: 0,
    missing: 0,
    recorded: 1,
    percentage: 100,
    clone_ready: true,
    pack_complete: true,
  },
};

function renderVoiceClonePage() {
  const result = renderWithProviders(
    <AppLayout>
      <VoiceClonePage />
    </AppLayout>,
    { initialEntries: ["/voice-clone"] },
  );
  const main =
    (result.container.querySelector("#main-content") as HTMLElement | null) ??
    result.container;
  return { ...result, main };
}

describe("VoiceClonePage", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [profileWithAcceptedRef] });
    mock.setResponse("GET /api/voice-profiles/p1", 200, profileWithAcceptedRef);
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the page heading and defaults to Aus Voice-Profil mode", async () => {
    renderVoiceClonePage();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Voice Clone", level: 1 })).toBeInTheDocument();
    });
    // "Aus Voice-Profil" is the default pressed mode button.
    expect(screen.getByRole("button", { name: "Aus Voice-Profil" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows a link to create a profile when no profiles exist", async () => {
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
    renderVoiceClonePage();
    const link = await screen.findByRole("link", { name: /Voice-Profil erstellen/i });
    expect(link).toBeInTheDocument();
    expect(link.getAttribute("href")).toBe("/voice-profiles");
  });

  it("starts a generation from a profile reference", async () => {
    const user = userEvent.setup();
    mock.setResponse("POST /api/voice-clone/generations", 201, {
      id: "gen-abc-123",
      status: "QUEUED",
    });
    renderVoiceClonePage();
    const profileSelect = await screen.findByRole("combobox", { name: "Voice-Profil auswählen" });
    await user.selectOptions(profileSelect, "p1");
    const refSelect = await screen.findByRole("combobox", { name: "Akzeptierte Profilreferenz auswählen" });
    await user.selectOptions(refSelect, "s1");
    await user.type(screen.getByLabelText("Zieltext"), "Neuer Zieltext");
    await user.click(screen.getByRole("button", { name: /Generierung starten/i }));
    await waitFor(() => {
      expect(
        mock.calls.some(
          (c) => c.method === "POST" && c.url.includes("/api/voice-clone/generations"),
        ),
      ).toBe(true);
    });
  });

  it("uploads a reference file in manual mode and starts a generation", async () => {
    const user = userEvent.setup();
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
    mock.setResponse("POST /api/voice-clone/generations", 201, {
      id: "gen-manual-1",
      status: "QUEUED",
    });
    renderVoiceClonePage();
    await user.click(screen.getByRole("button", { name: "Manueller Upload" }));
    const fileInput = screen.getByLabelText("Referenzaufnahme hochladen") as HTMLInputElement;
    const file = new File(["audio-data"], "clip.wav", { type: "audio/wav" });
    await user.upload(fileInput, file);
    await waitFor(() => {
      expect(screen.getByText(/Qualität: Gut/i)).toBeInTheDocument();
    });
    await user.type(screen.getByLabelText("Exakter Referenztext"), "Hallo Welt");
    await user.type(screen.getByLabelText("Zieltext"), "Neuer Zieltext");
    await user.click(screen.getByRole("button", { name: /Generierung starten/i }));
    await waitFor(() => {
      expect(
        mock.calls.some(
          (c) => c.method === "POST" && c.url.includes("/api/voice-clone/generations"),
        ),
      ).toBe(true);
    });
  });

  it("shows a REJECT notice and blocks submit when quality is REJECT", async () => {
    const user = userEvent.setup();
    mock.setResponse("POST /api/recordings", 200, {
      filename: "uploaded.wav",
      url: "/api/recordings/uploaded.wav",
      size_bytes: 1024,
    });
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/uploaded.wav",
      200,
      rejectQuality,
    );
    renderVoiceClonePage();
    await user.click(screen.getByRole("button", { name: "Manueller Upload" }));
    const fileInput = screen.getByLabelText("Referenzaufnahme hochladen");
    const file = new File(["audio-data"], "clip.wav", { type: "audio/wav" });
    await user.upload(fileInput, file);
    await waitFor(() => {
      expect(screen.getByText(/Qualität: Reject/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/technisch abgelehnt/i)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    expect(submit).toBeDisabled();
  });

  it("shows a REVIEW warning and requires the checkbox to proceed", async () => {
    const user = userEvent.setup();
    mock.setResponse("POST /api/recordings", 200, {
      filename: "uploaded.wav",
      url: "/api/recordings/uploaded.wav",
      size_bytes: 1024,
    });
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/uploaded.wav",
      200,
      reviewQuality,
    );
    renderVoiceClonePage();
    await user.click(screen.getByRole("button", { name: "Manueller Upload" }));
    const fileInput = screen.getByLabelText("Referenzaufnahme hochladen");
    const file = new File(["audio-data"], "clip.wav", { type: "audio/wav" });
    await user.upload(fileInput, file);
    await waitFor(() => {
      expect(screen.getByText(/Review/i)).toBeInTheDocument();
    });
    await user.type(screen.getByLabelText("Exakter Referenztext"), "Hallo Welt");
    await user.type(screen.getByLabelText("Zieltext"), "Neuer Text");
    let submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    expect(submit).toBeDisabled();
    const checkbox = screen.getByRole("checkbox");
    await user.click(checkbox);
    submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    expect(submit).not.toBeDisabled();
  });

  it("blocks submit while a generation is busy", async () => {
    mock.setResponse("GET /api/voice-clone/status", 200, {
      ...voiceCloneStatusIdle,
      busy: true,
      active_generation_id: "other-gen",
    });
    renderVoiceClonePage();
    const submit = await screen.findByRole("button", { name: /Generierung läuft/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/blockiert/i)).toBeInTheDocument();
  });

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

  it("shows the latest READY generation with audio player and download", async () => {
    const gen = {
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
    };
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [gen] });
    renderVoiceClonePage();
    expect(await screen.findByText(/Dies ist der Zieltext/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Audio-Player für Generierung/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /herunterladen/i })).toBeInTheDocument();
  });

  it("shows a FAILED generation with the concrete error", async () => {
    const gen = {
      id: "gen-failed-001",
      status: "FAILED",
      reference_recording: "ref.wav",
      reference_sha256: "abc",
      reference_text: "Ref",
      target_text: "Fehlerhafter Zieltext",
      language: "German",
      model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
      model_revision: "unknown",
      created_at: "2026-01-01T12:00:00+00:00",
      completed_at: "2026-01-01T12:01:00+00:00",
      output_duration_seconds: null,
      generation_seconds: null,
      peak_vram_bytes: null,
      quality: {},
      failure_reason: "RuntimeError: CUDA out of memory",
      warnings: [],
    };
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [gen] });
    renderVoiceClonePage();
    expect(await screen.findByText(/CUDA out of memory/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Audio-Player für Generierung/)).toBeNull();
  });
});
