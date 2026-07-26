import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoiceLabPage } from "../pages/VoiceLabPage";
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

const sampleRecording = {
  filename: "abc123.wav",
  created_at: "2026-01-01T12:00:00+00:00",
  duration_seconds: 5.2,
  file_size_bytes: 1024,
  audio_url: "/api/recordings/abc123.wav",
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

function renderVoiceLab() {
  const result = renderWithProviders(
    <AppLayout>
      <VoiceLabPage />
    </AppLayout>,
    { initialEntries: ["/voice-lab"] },
  );
  const main =
    (result.container.querySelector("#main-content") as HTMLElement | null) ??
    result.container;
  return { ...result, main };
}

async function switchToTab(tabName: string) {
  const user = userEvent.setup();
  const tab = screen.getByRole("tab", { name: tabName });
  await user.click(tab);
  return user;
}

describe("VoiceLabPage - Voice Clone tab", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the three tabs", async () => {
    renderVoiceLab();
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /Aufnahmen/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /Voice Clone/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Generierungen/i })).toBeInTheDocument();
  });

  it("shows the voice clone form after switching tabs", async () => {
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Voice Clone");
    expect(await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" })).toBeInTheDocument();
  });

  it("fetches and shows quality analysis when a reference is selected", async () => {
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/abc123.wav",
      200,
      goodQuality,
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Voice Clone");
    const select = await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" });
    await userEvent.setup().selectOptions(select, "abc123.wav");
    await waitFor(() => {
      expect(screen.getByText(/Qualität: Gut/i)).toBeInTheDocument();
    });
  });

  it("shows a REJECT notice and blocks submit when quality is REJECT", async () => {
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/abc123.wav",
      200,
      rejectQuality,
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Voice Clone");
    const select = await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" });
    await userEvent.setup().selectOptions(select, "abc123.wav");
    await waitFor(() => {
      expect(screen.getByText(/Qualität: Reject/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/technisch abgelehnt/i)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    expect(submit).toBeDisabled();
  });

  it("shows a REVIEW warning and requires the checkbox to proceed", async () => {
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/abc123.wav",
      200,
      reviewQuality,
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Voice Clone");
    const select = await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" });
    await user.selectOptions(select, "abc123.wav");
    await waitFor(() => {
      expect(screen.getByText(/Review/i)).toBeInTheDocument();
    });
    // Fill the text fields.
    await user.type(screen.getByLabelText("Exakter Referenztext"), "Hallo Welt");
    await user.type(screen.getByLabelText("Zieltext"), "Neuer Text");
    // Submit is disabled until the checkbox is checked.
    let submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    expect(submit).toBeDisabled();
    const checkbox = screen.getByRole("checkbox");
    await user.click(checkbox);
    submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    expect(submit).not.toBeDisabled();
  });

  it("calls the create generation mutation on submit", async () => {
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/abc123.wav",
      200,
      goodQuality,
    );
    mock.setResponse("POST /api/voice-clone/generations", 201, {
      id: "gen-abc-123",
      status: "QUEUED",
    });
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Voice Clone");
    const select = await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" });
    await user.selectOptions(select, "abc123.wav");
    await waitFor(() => expect(screen.getByText(/Qualität: Gut/i)).toBeInTheDocument());
    await user.type(screen.getByLabelText("Exakter Referenztext"), "Hallo Welt");
    await user.type(screen.getByLabelText("Zieltext"), "Neuer Zieltext");
    const submit = screen.getByRole("button", { name: /Generierung starten|Generierung läuft/i });
    await user.click(submit);
    await waitFor(() => {
      expect(
        mock.calls.some(
          (c) => c.method === "POST" && c.url.includes("/api/voice-clone/generations"),
        ),
      ).toBe(true);
    });
  });

  it("blocks submit while a generation is busy", async () => {
    mock.setResponse("GET /api/voice-clone/status", 200, {
      ...voiceCloneStatusIdle,
      busy: true,
      active_generation_id: "other-gen",
    });
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Voice Clone");
    const submit = await screen.findByRole("button", { name: /Generierung läuft/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/blockiert/i)).toBeInTheDocument();
  });
});

describe("VoiceLabPage - Generierungen tab", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
  });

  afterEach(() => {
    mock.restore();
  });

  it("shows an empty state when there are no generations", async () => {
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    expect(await screen.findByText("Noch keine Generierungen")).toBeInTheDocument();
  });

  it("shows a READY generation with audio player and download", async () => {
    const gen = {
      id: "gen-ready-001",
      status: "READY",
      reference_recording: "abc123.wav",
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
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    expect(await screen.findByText(/Dies ist der Zieltext/i)).toBeInTheDocument();
    expect(screen.getByText("Fertig")).toBeInTheDocument();
    expect(screen.getByLabelText(/Audio-Player für Generierung/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /herunterladen/i })).toBeInTheDocument();
  });

  it("shows a FAILED generation with the concrete error", async () => {
    const gen = {
      id: "gen-failed-001",
      status: "FAILED",
      reference_recording: "abc123.wav",
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
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    expect(await screen.findByText(/CUDA out of memory/i)).toBeInTheDocument();
    // No audio player for a FAILED generation.
    expect(screen.queryByLabelText(/Audio-Player für Generierung/)).toBeNull();
  });

  it("deletes a generation when the delete button is clicked", async () => {
    const gen = {
      id: "gen-ready-001",
      status: "READY",
      reference_recording: "abc123.wav",
      reference_sha256: "abc",
      reference_text: "Ref",
      target_text: "Zu loeschen",
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
    mock.setResponse("DELETE /api/voice-clone/generations/gen-ready-001", 200, {
      id: "gen-ready-001",
      deleted: true,
    });
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Generierungen");
    expect(await screen.findByText(/Zu loeschen/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /gen-read löschen/i }));
    await waitFor(() => {
      expect(
        mock.calls.some(
          (c) => c.method === "DELETE" && c.url.includes("gen-ready-001"),
        ),
      ).toBe(true);
    });
  });

  it("blocks delete while a generation is active", async () => {
    const gen = {
      id: "gen-active-001",
      status: "GENERATING",
      reference_recording: "abc123.wav",
      reference_sha256: "abc",
      reference_text: "Ref",
      target_text: "Laeuft noch",
      language: "German",
      model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
      model_revision: "unknown",
      created_at: "2026-01-01T12:00:00+00:00",
      completed_at: null,
      output_duration_seconds: null,
      generation_seconds: null,
      peak_vram_bytes: null,
      quality: {},
      failure_reason: null,
      warnings: [],
    };
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [gen] });
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    expect(await screen.findByText(/Laeuft noch/i)).toBeInTheDocument();
    const deleteBtn = screen.getByRole("button", { name: /gen-acti löschen/i });
    expect(deleteBtn).toBeDisabled();
  });
});
