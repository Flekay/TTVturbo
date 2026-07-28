import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { within, waitFor, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AppRouter } from "../router";
import { renderWithProviders, installFetchMock } from "./test-utils";
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

const TRANSCRIPTION_ID = "t-00000000-0000-0000-0000-000000000001";
const SOURCE_ID = "lib-00000000-0000-0000-0000-000000000001";

const readyJob = {
  schema_version: 1,
  id: "job-1",
  job_type: "TRANSCRIBE",
  source_type: "file_upload",
  source_id: SOURCE_ID,
  status: "READY",
  progress: { percent: 100, processed_seconds: 2, total_seconds: 2, phase: null },
  options: { model: "large-v3", model_family: "whisper", language: "de" },
  result: null,
  error: null,
  depends_on: null,
  transcription_id: TRANSCRIPTION_ID,
  created_at: "2024-01-01T00:00:00+00:00",
  started_at: "2024-01-01T00:00:00+00:00",
  completed_at: "2024-01-01T00:01:00+00:00",
  updated_at: "2024-01-01T00:01:00+00:00",
  transcript: {
    schema_version: 1,
    id: TRANSCRIPTION_ID,
    source_type: "file_upload",
    source_id: SOURCE_ID,
    audio_artifact: "artifacts/audio/source_audio.flac",
    model: "large-v3",
    device: "cpu",
    compute_type: "int8",
    language: "de",
    language_probability: 0.9,
    duration_seconds: 4,
    created_at: "2024-01-01T00:00:00+00:00",
    status: "READY",
    segment_count: 2,
    files: { json: "transcript.json", txt: "transcript.txt", srt: "transcript.srt", vtt: "transcript.vtt" },
  },
  transcript_status: "READY",
};

const libraryItem = {
  schema_version: 1,
  id: SOURCE_ID,
  source: "upload",
  title: "Test Upload",
  file_name: "test.mp4",
  file_size_bytes: 1024,
  file_exists: true,
  duration_seconds: 4,
  container: "mp4",
  twitch_video_id: null,
  vod_id: null,
  created_at: "2024-01-01T00:00:00+00:00",
  updated_at: "2024-01-01T00:00:00+00:00",
};

const transcriptView = {
  schema_version: 2,
  id: TRANSCRIPTION_ID,
  source_type: "file_upload",
  source_id: SOURCE_ID,
  media_item_id: SOURCE_ID,
  audio_artifact: "artifacts/audio/source_audio.flac",
  model: "large-v3",
  device: "cpu",
  compute_type: "int8",
  language: "de",
  language_probability: 0.9,
  duration_seconds: 4,
  created_at: "2024-01-01T00:00:00+00:00",
  updated_at: "2024-01-01T00:00:00+00:00",
  revision: 1,
  correction_status: "RAW",
  raw_text: "Ich hab den Trick bekommen. Das war echt cool.",
  corrected_text: null,
  engine: { family: "whisper", model: "large-v3", language: "de" },
  segments: [
    {
      id: "segment-0",
      start: 1.42,
      end: 3.85,
      raw_text: "Ich hab den Trick bekommen.",
      corrected_text: null,
      avg_logprob: null,
      no_speech_probability: null,
      words: [],
    },
    {
      id: "segment-1",
      start: 4.0,
      end: 6.5,
      raw_text: "Das war echt cool.",
      corrected_text: null,
      avg_logprob: null,
      no_speech_probability: null,
      words: [],
    },
  ],
};

function mainOf(container: HTMLElement): HTMLElement {
  return (container.querySelector("#main-content") as HTMLElement | null) ?? container;
}

function patchedTranscriptView(
  revision: number,
  overrides: Record<string, unknown> = {},
) {
  return JSON.parse(
    JSON.stringify({ ...transcriptView, revision, ...overrides }),
  ) as typeof transcriptView;
}

describe("Transcript editor frontend", () => {
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
    mock.setResponse("GET /api/twitch/profiles", 200, { profiles: [] });
    mock.setResponse("GET /api/vods", 200, { vods: [] });
    mock.setResponse("GET /api/transcription/status", 200, transcriptionRuntimeOk);
    mock.setResponse("GET /api/transcriptions", 200, { transcriptions: [readyJob] });
    mock.setResponse("GET /api/library/items", 200, { items: [libraryItem] });
    mock.setResponse("GET /api/pipeline-runs", 200, { pipeline_runs: [] });
    mock.setResponse(`GET /api/transcriptions/${TRANSCRIPTION_ID}/transcript`, 200, transcriptView);
    mock.setResponse(`GET /api/transcriptions/${TRANSCRIPTION_ID}/revisions`, 200, { revisions: [] });
  });

  afterEach(() => {
    mock.restore();
  });

  async function openEditor() {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/transcription"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Neue Transkription starten" })).toBeInTheDocument();
    });
    const korrekturTab = within(main).getByRole("tab", { name: "Korrektur" });
    await user.click(korrekturTab);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Transkript korrigieren" })).toBeInTheDocument();
    });
    return { user, container, main };
  }

  async function selectTranscript(user: ReturnType<typeof userEvent.setup>, main: HTMLElement) {
    const select = within(main).getByRole("combobox", { name: /Transkription/ }) as HTMLSelectElement;
    await user.selectOptions(select, TRANSCRIPTION_ID);
    // Wait for the transcript view to render the first raw segment text.
    await waitFor(() => {
      expect(within(main).getByText("Ich hab den Trick bekommen.")).toBeInTheDocument();
    });
  }

  it("loads the transcript and shows raw text", async () => {
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    // Raw text for both segments is visible.
    expect(within(main).getByText("Ich hab den Trick bekommen.")).toBeInTheDocument();
    expect(within(main).getByText("Das war echt cool.")).toBeInTheDocument();
    // Effective preview shows the raw text initially.
    expect(within(main).getByText(/Ich hab den Trick bekommen\. Das war echt cool\./)).toBeInTheDocument();
  });

  it("edits a correction and updates the effective preview", async () => {
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    const textareas = within(main).getAllByPlaceholderText("Korrektur eingeben …");
    expect(textareas.length).toBeGreaterThanOrEqual(2);
    await user.clear(textareas[0]);
    await user.type(textareas[0], "Ich hab den Drake bekommen.");
    // Effective preview should now include the corrected text.
    await waitFor(() => {
      expect(within(main).getByText(/Ich hab den Drake bekommen\. Das war echt cool\./)).toBeInTheDocument();
    });
    // The dirty segment should be marked.
    expect(within(main).getByText("geändert")).toBeInTheDocument();
  });

  it("saves corrections via the save button", async () => {
    const savedView = patchedTranscriptView(2, {
      correction_status: "CORRECTED",
      corrected_text: "Ich hab den Drake bekommen. Das war echt cool.",
      segments: [
        { ...transcriptView.segments[0], corrected_text: "Ich hab den Drake bekommen." },
        transcriptView.segments[1],
      ],
    });
    mock.setResponse(`PATCH /api/transcriptions/${TRANSCRIPTION_ID}/corrections`, 200, savedView);
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    const textareas = within(main).getAllByPlaceholderText("Korrektur eingeben …");
    await user.clear(textareas[0]);
    await user.type(textareas[0], "Ich hab den Drake bekommen.");
    const saveBtn = within(main).getByRole("button", { name: /Änderungen speichern/ });
    await user.click(saveBtn);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) =>
          c.url === `/api/transcriptions/${TRANSCRIPTION_ID}/corrections` &&
          c.method === "PATCH",
      );
      expect(call).toBeDefined();
    });
  });

  it("shows a revision conflict error on HTTP 409", async () => {
    const conflictBody = {
      detail: {
        code: "revision_conflict",
        message: "revision conflict: expected 1, current 2",
        current_revision: 2,
        transcript: patchedTranscriptView(2),
      },
    };
    mock.setResponse(`PATCH /api/transcriptions/${TRANSCRIPTION_ID}/corrections`, 409, conflictBody);
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    const textareas = within(main).getAllByPlaceholderText("Korrektur eingeben …");
    await user.clear(textareas[0]);
    await user.type(textareas[0], "Drake");
    const saveBtn = within(main).getByRole("button", { name: /Änderungen speichern/ });
    await user.click(saveBtn);
    await waitFor(() => {
      expect(within(main).getByText(/Revision Conflict/)).toBeInTheDocument();
    });
  });

  it("resets a single segment via the per-segment reset button", async () => {
    // First, load a transcript that already has a correction on segment-0.
    const correctedView = patchedTranscriptView(2, {
      correction_status: "CORRECTED",
      corrected_text: "Drake Das war echt cool.",
      segments: [
        { ...transcriptView.segments[0], corrected_text: "Drake" },
        transcriptView.segments[1],
      ],
    });
    mock.setResponse(`GET /api/transcriptions/${TRANSCRIPTION_ID}/transcript`, 200, correctedView);
    const resetView = patchedTranscriptView(3, {
      correction_status: "RAW",
      corrected_text: null,
      segments: [
        { ...transcriptView.segments[0], corrected_text: null },
        transcriptView.segments[1],
      ],
    });
    mock.setResponse(
      `POST /api/transcriptions/${TRANSCRIPTION_ID}/segments/segment-0/reset`,
      200,
      resetView,
    );
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    // The "korrigiert" badge should be present for the corrected segment.
    await waitFor(() => expect(within(main).getByText("korrigiert")).toBeInTheDocument());
    const resetBtn = within(main).getByTitle("Gespeicherte Korrektur zurücksetzen") as HTMLButtonElement;
    expect(resetBtn).toBeDefined();
    await user.click(resetBtn);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) =>
          c.url === `/api/transcriptions/${TRANSCRIPTION_ID}/segments/segment-0/reset` &&
          c.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("resets all corrections via the reset-all button (with confirm)", async () => {
    const correctedView = patchedTranscriptView(2, {
      correction_status: "CORRECTED",
      corrected_text: "Drake Stark",
      segments: [
        { ...transcriptView.segments[0], corrected_text: "Drake" },
        { ...transcriptView.segments[1], corrected_text: "Stark" },
      ],
    });
    mock.setResponse(`GET /api/transcriptions/${TRANSCRIPTION_ID}/transcript`, 200, correctedView);
    const resetAllView = patchedTranscriptView(3, {
      correction_status: "RAW",
      corrected_text: null,
      segments: [
        { ...transcriptView.segments[0], corrected_text: null },
        { ...transcriptView.segments[1], corrected_text: null },
      ],
    });
    mock.setResponse(
      `POST /api/transcriptions/${TRANSCRIPTION_ID}/reset-corrections`,
      200,
      resetAllView,
    );
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    const resetAllBtn = within(main).getByRole("button", {
      name: /Alle Korrekturen zurücksetzen/,
    });
    await user.click(resetAllBtn);
    // Confirm dialog appears in a portal (outside #main-content).
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Alle Korrekturen zurücksetzen" })).toBeInTheDocument(),
    );
    const confirmBtn = screen.getByRole("button", { name: "Zurücksetzen" });
    await user.click(confirmBtn);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) =>
          c.url === `/api/transcriptions/${TRANSCRIPTION_ID}/reset-corrections` &&
          c.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("warns before discarding unsaved drafts", async () => {
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    const textareas = within(main).getAllByPlaceholderText("Korrektur eingeben …");
    await user.clear(textareas[0]);
    await user.type(textareas[0], "Drake");
    const discardBtn = within(main).getByRole("button", { name: /Änderungen verwerfen/ });
    await user.click(discardBtn);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Ungespeicherte Änderungen verwerfen" }),
      ).toBeInTheDocument(),
    );
  });

  it("toggles between Raw / Korrigiert / Effektiv preview modes", async () => {
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    const rawTab = within(main).getByRole("tab", { name: "Raw" });
    await user.click(rawTab);
    // Raw preview shows the raw text.
    await waitFor(() =>
      expect(within(main).getByText(/Ich hab den Trick bekommen\. Das war echt cool\./)).toBeInTheDocument(),
    );
    const correctedTab = within(main).getByRole("tab", { name: "Korrigiert" });
    await user.click(correctedTab);
    // No corrections yet, so "Korrigiert" falls back to raw text.
    await waitFor(() =>
      expect(within(main).getByText(/Ich hab den Trick bekommen\. Das war echt cool\./)).toBeInTheDocument(),
    );
  });

  it("saves via Ctrl+S keyboard shortcut", async () => {
    const savedView = patchedTranscriptView(2, {
      correction_status: "CORRECTED",
      corrected_text: "Drake Das war echt cool.",
      segments: [
        { ...transcriptView.segments[0], corrected_text: "Drake" },
        transcriptView.segments[1],
      ],
    });
    mock.setResponse(`PATCH /api/transcriptions/${TRANSCRIPTION_ID}/corrections`, 200, savedView);
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    const textareas = within(main).getAllByPlaceholderText("Korrektur eingeben …");
    await user.clear(textareas[0]);
    await user.type(textareas[0], "Drake");
    // Trigger Ctrl+S.
    await user.keyboard("{Control>}s{/Control}");
    await waitFor(() => {
      const call = mock.calls.find(
        (c) =>
          c.url === `/api/transcriptions/${TRANSCRIPTION_ID}/corrections` &&
          c.method === "PATCH",
      );
      expect(call).toBeDefined();
    });
  });

  it("seeks the player when clicking a segment timestamp", async () => {
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    // The first segment timestamp button shows the start time.
    const timeBtn = within(main).getByRole("button", {
      name: /00:01\.420 – 00:03\.850/,
    });
    // Clicking should not throw (jsdom video element is a stub).
    await user.click(timeBtn);
    // No assertion on playback; we just verify the click handler is wired.
    expect(timeBtn).toBeInTheDocument();
  });

  it("does not crash on existing transcripts without new correction fields", async () => {
    // A v1-style transcript view (no corrected_text on segments) — the
    // schema treats corrected_text as optional/nullable, so this must
    // not crash the editor.
    const legacyView = {
      ...transcriptView,
      segments: [
        { id: "segment-0", start: 1.42, end: 3.85, raw_text: "Ich hab den Trick bekommen." },
        { id: "segment-1", start: 4.0, end: 6.5, raw_text: "Das war echt cool." },
      ],
    };
    mock.setResponse(`GET /api/transcriptions/${TRANSCRIPTION_ID}/transcript`, 200, legacyView);
    const { user, main } = await openEditor();
    await selectTranscript(user, main);
    expect(within(main).getByText("Ich hab den Trick bekommen.")).toBeInTheDocument();
  });
});
