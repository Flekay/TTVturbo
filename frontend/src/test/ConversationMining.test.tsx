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
    vods: 1,
    ready: 1,
    active: 0,
    failed: 0,
    downloaded_bytes: 1024,
  },
  media_processing: {
    audio_artifacts: 0,
    transcripts: 1,
    audio_jobs: { total: 0, ready: 0, failed: 0, active: 0 },
    transcription_jobs: { total: 0, ready: 0, failed: 0, active: 0 },
    pipeline_runs: { total: 0, active: 0, ready_for_clip_analysis: 0, failed: 0 },
  },
};

const vodReady = {
  schema_version: 1,
  id: "33333333-3333-3333-3333-333333333333",
  profile_id: "11111111-1111-1111-1111-111111111111",
  twitch_video_id: "200",
  source_url: "https://www.twitch.tv/videos/200",
  title: "Mining Test VOD",
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

const transcriptionReady = {
  schema_version: 1,
  id: "55555555-5555-5555-5555-555555555555",
  source_type: "twitch_vod",
  source_id: vodReady.id,
  audio_artifact: "artifacts/audio/source_audio.flac",
  model: "large-v3",
  device: "cpu",
  compute_type: "int8",
  language: "de",
  language_probability: 0.9,
  duration_seconds: 3600,
  created_at: "2024-01-01T00:00:00+00:00",
  status: "READY",
  segment_count: 10,
  files: { json: "transcript.json", txt: "transcript.txt", srt: "transcript.srt", vtt: "transcript.vtt" },
};

const miningRuntimeAvailable = {
  available: true,
  model: "fake-model/test",
  device: "cpu",
  dtype: "auto",
  busy: false,
  busy_owner_type: null,
  reasons: [],
};

const miningRuntimeUnavailable = {
  available: false,
  model: "",
  device: "cpu",
  dtype: "auto",
  busy: false,
  busy_owner_type: null,
  reasons: ["no model configured"],
};

const completedMiningRun = {
  schema_version: 1,
  id: "66666666-6666-6666-6666-666666666666",
  media_item_id: vodReady.id,
  transcript_id: transcriptionReady.id,
  transcript_revision: 1,
  status: "COMPLETED",
  model: { provider: "local", model_id: "fake-model/test", revision: null },
  mining_config_version: 1,
  created_at: "2024-01-01T00:00:00+00:00",
  started_at: "2024-01-01T00:00:01+00:00",
  completed_at: "2024-01-01T00:05:00+00:00",
  error: null,
  blocks: [
    { block_id: "block-0", start: 0.0, end: 90.0, status: "COMPLETED", attempt: 1, model_input_segments: 10, result_count: 2, error: null },
  ],
  conversations: [
    {
      id: "conv-1",
      start: 10.0,
      end: 45.0,
      start_segment_id: "segment-1",
      end_segment_id: "segment-4",
      title: "Cool Trick",
      summary: "The streamer pulls off a cool trick.",
      topic: null,
      category: "REACTION",
      transcript_excerpt: "Ich hab den Trick bekommen. Das war echt cool.",
      excerpt_has_corrected: false,
      signals: ["emotion", "payoff"],
      context: { requires_previous_context: false, requires_following_context: true },
      confidence: 0.85,
    },
    {
      id: "conv-2",
      start: 60.0,
      end: 90.0,
      start_segment_id: "segment-6",
      end_segment_id: "segment-9",
      title: "Viral Prediction",
      summary: "The streamer predicts this will go viral.",
      topic: null,
      category: "OPINION",
      transcript_excerpt: "Ich glaube, das wird viral.",
      excerpt_has_corrected: false,
      signals: ["controversy"],
      context: { requires_previous_context: false, requires_following_context: false },
      confidence: 0.72,
    },
  ],
  progress: 100.0,
  current_block: null,
};

function mainOf(container: HTMLElement): HTMLElement {
  return (container.querySelector("#main-content") as HTMLElement | null) ?? container;
}

describe("Conversation Mining frontend", () => {
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
          login: "miningtestpayt",
          channel_url: "https://www.twitch.tv/miningtestpayt",
          display_name: "miningtestpayt",
          created_at: "2024-01-01T00:00:00+00:00",
          updated_at: "2024-01-01T00:00:00+00:00",
          last_synced_at: null,
          vod_count: 1,
        },
      ],
    });
    mock.setResponse("GET /api/vods", 200, { vods: [vodReady] });
    mock.setResponse("GET /api/vods?status=READY", 200, { vods: [vodReady] });
    mock.setResponse("GET /api/transcription/status", 200, {
      available: true,
      busy: false,
      busy_owner_type: null,
      model: "large-v3",
      device: "cpu",
      compute_type: "int8",
      reasons: [],
    });
    mock.setResponse("GET /api/transcriptions", 200, { transcriptions: [transcriptionReady] });
    mock.setResponse("GET /api/pipeline-runs", 200, { pipeline_runs: [] });
    mock.setResponse("GET /api/conversation-mining/status", 200, miningRuntimeAvailable);
    mock.setResponse("GET /api/conversation-mining/runs", 200, { runs: [] });
    // VOD detail page queries.
    mock.setResponse(`GET /api/vods/${vodReady.id}`, 200, vodReady);
    mock.setResponse(`GET /api/vods/${vodReady.id}/audio-artifact`, 404, { detail: "no audio artifact" });
    mock.setResponse(`GET /api/vods/${vodReady.id}/transcriptions`, 200, { transcriptions: [transcriptionReady] });
    mock.setResponse(`GET /api/vods/${vodReady.id}/pipeline-runs`, 200, { pipeline_runs: [] });
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the mining panel on the VOD detail page with a ready transcript", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: [`/vod-pipeline/${vodReady.id}`],
    });
    const main = mainOf(container);
    // Wait for the mining status to load and the start button to appear.
    await waitFor(() => {
      expect(within(main).getByRole("button", { name: /Mining starten/ })).toBeInTheDocument();
    });
  });

  it("shows the unavailable badge when the model is not configured", async () => {
    mock.setResponse("GET /api/conversation-mining/status", 200, miningRuntimeUnavailable);
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: [`/vod-pipeline/${vodReady.id}`],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByText("Nicht verfügbar")).toBeInTheDocument();
    });
    // The start button should be disabled.
    const startBtn = within(main).getByRole("button", { name: /Mining starten/ }) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
  });

  it("starts a mining run when the start button is clicked", async () => {
    const user = userEvent.setup();
    mock.setResponse("POST /api/conversation-mining/runs", 201, {
      ...completedMiningRun,
      status: "QUEUED",
      conversations: [],
      progress: 0.0,
    });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: [`/vod-pipeline/${vodReady.id}`],
    });
    const main = mainOf(container);
    // Wait for the start button to be enabled (no active run, model available).
    await waitFor(() => {
      expect(within(main).getByRole("button", { name: /Mining starten/ })).toBeEnabled();
    });
    await user.click(within(main).getByRole("button", { name: /Mining starten/ }));
    // The POST should have been made.
    await waitFor(() => {
      const postCalls = mock.calls.filter((c) => c.method === "POST" && c.url.includes("/api/conversation-mining/runs"));
      expect(postCalls.length).toBeGreaterThan(0);
    });
  });

  it("renders completed conversations with title, category and signals", async () => {
    mock.setResponse("GET /api/conversation-mining/runs", 200, { runs: [completedMiningRun] });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: [`/vod-pipeline/${vodReady.id}`],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByText("Cool Trick")).toBeInTheDocument();
    });
    // Category badge should be visible.
    expect(within(main).getByText("Reaktion")).toBeInTheDocument();
    // Signal chips should be visible.
    expect(within(main).getByText("Emotion")).toBeInTheDocument();
    expect(within(main).getByText("Payoff")).toBeInTheDocument();
    // The second conversation should also be visible.
    expect(within(main).getByText("Viral Prediction")).toBeInTheDocument();
  });

  it("shows empty state when no mining runs exist", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: [`/vod-pipeline/${vodReady.id}`],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByText("Keine Mining-Läufe")).toBeInTheDocument();
    });
  });
});
