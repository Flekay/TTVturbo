import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GenerationList } from "../components/voiceClone/GenerationList";
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

function readyGen(overrides: Partial<Record<string, unknown>> = {}) {
  return {
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
    ...overrides,
  };
}

function renderVoiceLab() {
  return renderWithProviders(
    <AppLayout>
      <VoiceLabPage />
    </AppLayout>,
    { initialEntries: ["/voice-lab"] },
  );
}

async function switchToTab(tabName: string) {
  const user = userEvent.setup();
  const tab = screen.getByRole("tab", { name: tabName });
  await user.click(tab);
  return user;
}

describe("Frontend hardening - audio URL stability", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [readyGen()] });
  });

  afterEach(() => mock.restore());

  it("generation audio URL stays stable across re-renders (no Date.now cache buster)", async () => {
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    const audio = await screen.findByLabelText(/Audio-Player für Generierung/);
    const src1 = audio.getAttribute("src");
    expect(src1).toBe("/api/voice-clone/generations/gen-ready-001/audio");
    expect(src1).not.toContain("?t=");
    expect(src1).not.toContain("Date");

    // Trigger a re-render by toggling the details section.
    await userEvent.setup().click(screen.getByRole("button", { name: /Technische Details/i }));
    const src2 = audio.getAttribute("src");
    expect(src2).toBe(src1);
  });

  it("reference audio URL is stable and has no cache buster", async () => {
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/abc123.wav",
      200,
      goodQuality,
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Voice Clone");
    const select = await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" });
    await user.selectOptions(select, "abc123.wav");
    const refAudio = await screen.findByLabelText(/Referenz abc123.wav abspielen/i);
    const src = refAudio.getAttribute("src");
    expect(src).toBe("/api/recordings/abc123.wav");
    expect(src).not.toContain("?t=");
  });

  it("audio URL does not change when the generations list is refetched", async () => {
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    const audio = await screen.findByLabelText(/Audio-Player für Generierung/);
    const src1 = audio.getAttribute("src");

    // Re-respond with the same data and re-render by toggling details twice.
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [readyGen()] });
    await userEvent.setup().click(screen.getByRole("button", { name: /Technische Details/i }));
    await userEvent.setup().click(screen.getByRole("button", { name: /Technische Details/i }));
    const src2 = audio.getAttribute("src");
    expect(src2).toBe(src1);
  });
});

describe("Frontend hardening - delete confirmation dialog", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [readyGen()] });
  });

  afterEach(() => mock.restore());

  it("opens an AlertDialog when the delete button is clicked", async () => {
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Generierungen");
    await user.click(screen.getByRole("button", { name: /gen-read löschen/i }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Generierung endgültig löschen?")).toBeInTheDocument();
    expect(within(dialog).getByText(/Zieltext:/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Erstellt:/)).toBeInTheDocument();
    expect(within(dialog).getByText(/unwiderruflich gelöscht/)).toBeInTheDocument();
  });

  it("cancel does not dispatch a delete request", async () => {
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Generierungen");
    await user.click(screen.getByRole("button", { name: /gen-read löschen/i }));
    await user.click(await screen.findByRole("button", { name: "Abbrechen" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(mock.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("confirm calls the delete mutation exactly once", async () => {
    mock.setResponse("DELETE /api/voice-clone/generations/gen-ready-001", 200, {
      id: "gen-ready-001",
      deleted: true,
    });
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Generierungen");
    await user.click(screen.getByRole("button", { name: /gen-read löschen/i }));
    await user.click(await screen.findByRole("button", { name: "Endgültig löschen" }));
    await waitFor(() => {
      expect(
        mock.calls.filter(
          (c) => c.method === "DELETE" && c.url.includes("gen-ready-001"),
        ).length,
      ).toBe(1);
    });
  });
});

describe("Frontend hardening - Zod validation", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
  });

  afterEach(() => mock.restore());

  it("accepts a well-formed quality response", async () => {
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/abc123.wav",
      200,
      goodQuality,
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Voice Clone");
    const select = await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" });
    await user.selectOptions(select, "abc123.wav");
    expect(await screen.findByText(/Qualität: Gut/i)).toBeInTheDocument();
  });

  it("shows an error state for an invalid quality response instead of crashing", async () => {
    // Missing required nested fields -> Zod parse fails.
    mock.setResponse(
      "GET /api/voice-clone/analyze-reference/abc123.wav",
      200,
      { technical: { sample_rate: 44100 }, quality: "GOOD" },
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    const user = await switchToTab("Voice Clone");
    const select = await screen.findByRole("combobox", { name: "Referenzaufnahme auswählen" });
    await user.selectOptions(select, "abc123.wav");
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
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
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
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Voice Clone");
    expect(await screen.findByText(/Voice Clone ist aktuell nicht verfügbar/i)).toBeInTheDocument();
    expect(screen.getByText("Qwen3-TTS ist nicht installiert.")).toBeInTheDocument();
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
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Voice Clone");
    const submit = await screen.findByRole("button", { name: /Generierung läuft/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/blockiert/i)).toBeInTheDocument();
    // The active generation id is shown truncated to 12 chars.
    expect(screen.getByText("other-gen-ab")).toBeInTheDocument();
  });
});

describe("Frontend hardening - status display", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
  });

  afterEach(() => mock.restore());

  it("renders an unknown future status without crashing and shows the raw string", async () => {
    mock.setResponse(
      "GET /api/voice-clone/generations",
      200,
      { generations: [readyGen({ id: "gen-x-001", status: "POSTPROCESSING_FUTURE" })] },
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    expect(await screen.findByText("POSTPROCESSING_FUTURE")).toBeInTheDocument();
  });
});

describe("Frontend hardening - polling", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-clone/status", 200, voiceCloneStatusIdle);
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
  });

  afterEach(() => mock.restore());

  it("does not poll the generations list when all generations are terminal", async () => {
    mock.setResponse(
      "GET /api/voice-clone/generations",
      200,
      { generations: [readyGen()] },
    );
    renderVoiceLab();
    await waitFor(() => expect(screen.getByText("abc123.wav")).toBeInTheDocument());
    await switchToTab("Generierungen");
    await screen.findByText(/Dies ist der Zieltext/i);
    const callsBefore = mock.calls.filter(
      (c) => c.method === "GET" && c.url.includes("/api/voice-clone/generations"),
    ).length;
    // Wait a bit; no aggressive polling should happen for terminal states.
    await new Promise((r) => setTimeout(r, 500));
    const callsAfter = mock.calls.filter(
      (c) => c.method === "GET" && c.url.includes("/api/voice-clone/generations"),
    ).length;
    expect(callsAfter).toBe(callsBefore);
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
          // No output_sha256, output_sample_rate, worker_exit_code, device_name.
        }),
      ],
    });
    renderWithProviders(<GenerationList />);
    await screen.findByText(/Dies ist der Zieltext/i);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Technische Details/i }));
    // Model is always present and shown.
    expect(screen.getByText("Qwen/Qwen3-TTS-12Hz-1.7B-Base")).toBeInTheDocument();
    // Optional fields that were not supplied must not appear.
    expect(screen.queryByText("NVIDIA RTX 5070")).toBeNull();
    expect(screen.queryByText("24000 Hz")).toBeNull();
    mock.restore();
  });
});
