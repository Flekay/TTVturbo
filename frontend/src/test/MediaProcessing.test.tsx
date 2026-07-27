import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AppRouter } from "../router";
import { renderWithProviders, installFetchMock } from "../test/test-utils";
import type { BackendStatus } from "../types/status";

const statusResponse: BackendStatus = {
  status: "online",
  app_name: "TTVturbo",
  version: "0.1.0",
  uptime_seconds: 100,
  recordings: { count: 0, total_duration_seconds: 0, total_size_bytes: 0 },
  storage: { free_bytes: 1000000 },
  features: {
    recording: "available",
    voice_cloning: "available",
    vod_downloader: "available",
    vod_pipeline: "available",
    audio_extraction: "available",
    transcription: "available",
    clip_finder: "not_implemented",
    twitch_profiles: "available",
    vod_analysis: "not_implemented",
    video_editor: "not_implemented",
  },
  vod_pipeline: {
    profiles: 1,
    vods: 2,
    ready: 1,
    active: 0,
    failed: 0,
    downloaded_bytes: 1024,
  },
  media_processing: {
    audio_artifacts: 0,
    transcripts: 0,
    audio_jobs: { total: 0, ready: 0, failed: 0, active: 0 },
    transcription_jobs: { total: 0, ready: 0, failed: 0, active: 0 },
    pipeline_runs: { total: 0, active: 0, ready_for_clip_analysis: 0, failed: 0 },
  },
};

const transcriptionRuntimeOk = {
  available: true,
  busy: false,
  busy_owner_type: null,
  model: "large-v3",
  device: "cuda",
  compute_type: "int8_float16",
  device_name: "RTX 5070",
  model_cached: false,
  faster_whisper_importable: true,
  cuda_available: true,
  reasons: [],
  warnings: [],
};

const vodReady = {
  schema_version: 1,
  id: "33333333-3333-3333-3333-333333333333",
  profile_id: "11111111-1111-1111-1111-111111111111",
  twitch_video_id: "200",
  source_url: "https://www.twitch.tv/videos/200",
  title: "Ready VOD",
  description: "",
  type: "archive",
  language: "de",
  published_at: "2024-01-01T00:00:00+00:00",
  created_at: "2024-01-01T00:00:00+00:00",
  duration_seconds: 3600,
  thumbnail_url: "",
  view_count: 100,
  status: "READY",
  progress: { percent: null, downloaded_bytes: null, total_bytes: null, speed_bytes_per_second: null, eta_seconds: null },
  download: {
    started_at: "2024-01-01T00:00:00+00:00",
    completed_at: "2024-01-01T01:00:00+00:00",
    file_name: "source.mp4",
    file_size_bytes: 1024,
    container: "mp4",
    duration_seconds: 3600,
    width: 1920,
    height: 1080,
    video_codec: "h264",
    audio_codec: "aac",
  },
  error: null,
  updated_at: "2024-01-01T01:00:00+00:00",
};

function mainOf(container: HTMLElement): HTMLElement {
  return (container.querySelector("#main-content") as HTMLElement | null) ?? container;
}

describe("Media Processing frontend", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, statusResponse);
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
    mock.setResponse("GET /api/twitch/status", 200, {
      available: true,
      downloader_available: true,
      yt_dlp_version: "2026.07.04",
      ffprobe_available: true,
      download_dir_writable: true,
      reasons: [],
      warnings: [],
    });
    mock.setResponse("GET /api/twitch/profiles", 200, {
      profiles: [
        {
          schema_version: 1,
          id: "11111111-1111-1111-1111-111111111111",
          login: "casepayt",
          channel_url: "https://www.twitch.tv/casepayt",
          display_name: "casepayt",
          created_at: "2024-01-01T00:00:00+00:00",
          updated_at: "2024-01-01T00:00:00+00:00",
          last_synced_at: null,
          vod_count: 1,
        },
      ],
    });
    mock.setResponse("GET /api/vods", 200, { vods: [vodReady] });
    mock.setResponse("GET /api/vods?status=READY", 200, { vods: [vodReady] });
    mock.setResponse("GET /api/transcription/status", 200, transcriptionRuntimeOk);
    mock.setResponse("GET /api/transcriptions", 200, { transcriptions: [] });
    mock.setResponse("GET /api/pipeline-runs", 200, { pipeline_runs: [] });
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the Transcription page with runtime status", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/transcription"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Neue Transkription starten" })).toBeInTheDocument();
    });
  });

  it("renders the VOD Pipeline page with startable VODs", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Pipeline starten" })).toBeInTheDocument();
    });
    // The Ready VOD should appear as a startable VOD.
    await waitFor(() => {
      expect(within(main).getByText("Ready VOD")).toBeInTheDocument();
    });
  });

  it("starts a pipeline run when the button is clicked", async () => {
    const user = userEvent.setup();
    mock.setResponse("POST /api/pipeline-runs", 201, {
      schema_version: 1,
      id: "44444444-4444-4444-4444-444444444444",
      source_type: "twitch_vod",
      source_id: vodReady.id,
      profile_id: "11111111-1111-1111-1111-111111111111",
      status: "RUNNING",
      steps: [
        { type: "DOWNLOAD", status: "READY", job_id: null, error: null },
        { type: "EXTRACT_AUDIO", status: "WAITING", job_id: null, error: null },
        { type: "TRANSCRIBE", status: "WAITING", job_id: null, error: null },
        { type: "FIND_CLIPS", status: "NOT_IMPLEMENTED", job_id: null, error: null },
      ],
      error: null,
      created_at: "2024-01-01T00:00:00+00:00",
      updated_at: "2024-01-01T00:00:00+00:00",
      completed_at: null,
    });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Ready VOD")).toBeInTheDocument());
    const startBtn = within(main).getByRole("button", { name: /Pipeline starten/ });
    await user.click(startBtn);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url === "/api/pipeline-runs" && c.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("renders the VOD detail page for a specific VOD", async () => {
    mock.setResponse(`GET /api/vods/${vodReady.id}`, 200, vodReady);
    mock.setResponse(`GET /api/vods/${vodReady.id}/transcriptions`, 200, { transcriptions: [] });
    mock.setResponse(`GET /api/vods/${vodReady.id}/pipeline-runs`, 200, { pipeline_runs: [] });
    // Audio artifact returns 404 (no artifact yet).
    mock.setResponse(`GET /api/vods/${vodReady.id}/artifacts/audio`, 404, {
      detail: { code: "audio_artifact_not_found", message: "No audio artifact for this VOD." },
    });

    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: [`/vod-pipeline/${vodReady.id}`],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Download" })).toBeInTheDocument();
    });
    // Download section should show.
    expect(within(main).getByText("Download")).toBeInTheDocument();
  });

  it("renders the VOD Downloader page at /vod-downloader", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "VOD-Link importieren" })).toBeInTheDocument();
    });
  });

  it("shows the sidebar with all new sections", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/dashboard"],
    });
    const sidebar = container.querySelector("aside") ?? container;
    // AUTOMATION section.
    expect(within(sidebar).getByText("Automation")).toBeInTheDocument();
    // ON-DEMAND section.
    expect(within(sidebar).getByText("On-Demand Werkzeuge")).toBeInTheDocument();
    // MANAGEMENT section.
    expect(within(sidebar).getByText("Verwaltung")).toBeInTheDocument();
    // New routes are present.
    expect(within(sidebar).getByText("VOD Downloader")).toBeInTheDocument();
    expect(within(sidebar).getByText("Transkription")).toBeInTheDocument();
  });
});
