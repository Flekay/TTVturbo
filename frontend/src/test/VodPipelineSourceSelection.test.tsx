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

const profile = {
  schema_version: 1,
  id: "11111111-1111-1111-1111-111111111111",
  login: "casepayt",
  channel_url: "https://www.twitch.tv/casepayt",
  display_name: "casepayt",
  created_at: "2024-01-01T00:00:00+00:00",
  updated_at: "2024-01-01T00:00:00+00:00",
  last_synced_at: null,
  vod_count: 2,
};

const vodA = {
  schema_version: 1,
  id: "22222222-2222-2222-2222-222222222222",
  profile_id: profile.id,
  twitch_video_id: "100",
  source_url: "https://www.twitch.tv/videos/100",
  title: "Alpha VOD",
  description: "",
  type: "archive",
  language: "de",
  published_at: "2024-01-01T00:00:00+00:00",
  created_at: "2024-01-01T00:00:00+00:00",
  duration_seconds: 5415,
  thumbnail_url: "",
  view_count: 100,
  status: "DISCOVERED",
  progress: { percent: null, downloaded_bytes: null, total_bytes: null, speed_bytes_per_second: null, eta_seconds: null },
  download: { started_at: null, completed_at: null, file_name: null, file_size_bytes: null, container: null, duration_seconds: null, width: null, height: null, video_codec: null, audio_codec: null },
  error: null,
  updated_at: "2024-01-01T00:00:00+00:00",
};

const vodB = {
  ...vodA,
  id: "33333333-3333-3333-3333-333333333333",
  twitch_video_id: "101",
  source_url: "https://www.twitch.tv/videos/101",
  title: "Bravo VOD",
  status: "READY",
};

const vodActive = {
  ...vodA,
  id: "44444444-4444-4444-4444-444444444444",
  twitch_video_id: "102",
  source_url: "https://www.twitch.tv/videos/102",
  title: "Active Pipeline VOD",
};

const activeRun = {
  schema_version: 2,
  id: "55555555-5555-5555-5555-555555555555",
  source_type: "twitch_vod",
  source_id: vodActive.id,
  profile_id: profile.id,
  status: "RUNNING",
  steps: [],
  error: null,
  created_at: "2024-01-01T00:00:00+00:00",
  updated_at: "2024-01-01T00:00:00+00:00",
  completed_at: null,
  source: {
    provider: "twitch",
    type: "vod",
    external_id: "102",
    url: "https://www.twitch.tv/videos/102",
    profile_id: profile.id,
    title: "Active Pipeline VOD",
    thumbnail_url: null,
    duration_seconds: 60,
    legacy: false,
  },
  progress: 10,
  current_step: "DOWNLOAD",
  started_at: "2024-01-01T00:00:00+00:00",
  library_item_id: null,
  transcript_id: null,
};

function mainOf(container: HTMLElement): HTMLElement {
  return (container.querySelector("#main-content") as HTMLElement | null) ?? container;
}

describe("VOD Pipeline source selection", () => {
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
    mock.setResponse("GET /api/twitch/profiles", 200, { profiles: [profile] });
    mock.setResponse("GET /api/transcription/status", 200, {
      available: true,
      busy: false,
      busy_owner_type: null,
      model: "large-v3",
      device: "cpu",
      compute_type: "int8",
      device_name: null,
      model_cached: false,
      faster_whisper_importable: true,
      cuda_available: false,
      reasons: [],
      warnings: [],
    });
    mock.setResponse("GET /api/transcriptions", 200, { transcriptions: [] });
    mock.setResponse("GET /api/pipeline-runs", 200, { pipeline_runs: [] });
  });

  afterEach(() => {
    mock.restore();
  });

  function setVods(vods: unknown[]) {
    mock.setResponse("GET /api/vods", 200, { vods });
    mock.setResponse(`GET /api/vods?profile_id=${profile.id}`, 200, { vods });
  }

  it("shows the profile filter with 'Alle Profile' as default and profiles selectable", async () => {
    setVods([vodA, vodB]);
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    const filter = await waitFor(() =>
      within(main).getByLabelText("Twitch-Profil filtern") as HTMLSelectElement,
    );
    expect(filter.value).toBe("all");
    expect(within(filter).getByRole("option", { name: "Alle Profile" })).toBeInTheDocument();
    // Wait for the profile to load and appear as an option.
    await waitFor(() =>
      expect(within(filter).getByRole("option", { name: "casepayt" })).toBeInTheDocument(),
    );
  });

  it("loads VOD cards from the selected profile", async () => {
    setVods([vodA, vodB]);
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    expect(within(main).getByText("Bravo VOD")).toBeInTheDocument();
  });

  it("supports search across title / profile / twitch id", async () => {
    setVods([vodA, vodB]);
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    const search = within(main).getByLabelText("VODs durchsuchen") as HTMLInputElement;
    await user.type(search, "Bravo");
    // Refetch returns both; client filter is not required, but the search
    // request must include the search param.
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url.includes("/api/vods") && c.url.includes("search=Bravo"),
      );
      expect(call).toBeDefined();
    });
  });

  it("supports sort selection", async () => {
    setVods([vodA, vodB]);
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    const sortSelect = within(main).getByLabelText("Sortierung") as HTMLSelectElement;
    await user.selectOptions(sortSelect, "oldest");
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url.includes("/api/vods") && c.url.includes("sort=oldest"),
      );
      expect(call).toBeDefined();
    });
  });

  it("selects a single VOD and starts a pipeline run via the batch endpoint", async () => {
    setVods([vodA]);
    mock.setResponse("POST /api/vod-pipeline/runs/batch", 201, {
      created: [{ source_external_id: "100", run_id: "run-1" }],
      conflicts: [],
      failed: [],
    });
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    const checkbox = within(main).getByLabelText("VOD Alpha VOD auswählen") as HTMLInputElement;
    await user.click(checkbox);
    const startBtn = within(main).getByRole("button", { name: /VOD zur Pipeline hinzufügen/ });
    await user.click(startBtn);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url === "/api/vod-pipeline/runs/batch" && c.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("selects multiple VODs and starts a batch", async () => {
    setVods([vodA, vodB]);
    mock.setResponse("POST /api/vod-pipeline/runs/batch", 201, {
      created: [
        { source_external_id: "100", run_id: "run-1" },
        { source_external_id: "101", run_id: "run-2" },
      ],
      conflicts: [],
      failed: [],
    });
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    await user.click(within(main).getByLabelText("VOD Alpha VOD auswählen"));
    await user.click(within(main).getByLabelText("VOD Bravo VOD auswählen"));
    const startBtn = within(main).getByRole("button", { name: /2 ausgewählte VODs zur Pipeline hinzufügen/ });
    await user.click(startBtn);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url === "/api/vod-pipeline/runs/batch" && c.method === "POST",
      );
      expect(call).toBeDefined();
      const body = JSON.parse((call!.body as string) ?? "{}");
      expect(body.sources).toHaveLength(2);
      expect(body.sources[0].provider).toBe("twitch");
    });
  });

  it("selects all visible and clears selection", async () => {
    setVods([vodA, vodB]);
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    const selectAll = within(main).getByRole("button", { name: /Alle sichtbaren auswählen/ });
    await user.click(selectAll);
    expect((within(main).getByLabelText("VOD Alpha VOD auswählen") as HTMLInputElement).checked).toBe(true);
    expect((within(main).getByLabelText("VOD Bravo VOD auswählen") as HTMLInputElement).checked).toBe(true);
    const clear = within(main).getByRole("button", { name: /Auswahl aufheben/ });
    await user.click(clear);
    expect((within(main).getByLabelText("VOD Alpha VOD auswählen") as HTMLInputElement).checked).toBe(false);
  });

  it("renders a partial-failure batch result", async () => {
    setVods([vodA, vodB]);
    mock.setResponse("POST /api/vod-pipeline/runs/batch", 201, {
      created: [{ source_external_id: "100", run_id: "run-1" }],
      conflicts: [{ source_external_id: "101", code: "active_run", message: "active" }],
      failed: [],
    });
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    await user.click(within(main).getByLabelText("VOD Alpha VOD auswählen"));
    await user.click(within(main).getByLabelText("VOD Bravo VOD auswählen"));
    await user.click(within(main).getByRole("button", { name: /2 ausgewählte VODs zur Pipeline hinzufügen/ }));
    await waitFor(() => expect(within(main).getByText(/1 Run\(s\) gestartet/)).toBeInTheDocument());
    expect(within(main).getByText(/Konflikt/)).toBeInTheDocument();
  });

  it("disables selection for a VOD with an active pipeline run", async () => {
    setVods([vodA, vodActive]);
    // The fetch mock falls back to the URL-without-query match, so we serve
    // the active run from the base pipeline-runs endpoint.
    mock.setResponse("GET /api/pipeline-runs", 200, { pipeline_runs: [activeRun] });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Active Pipeline VOD")).toBeInTheDocument());
    const cb = within(main).getByLabelText("VOD Active Pipeline VOD auswählen") as HTMLInputElement;
    await waitFor(() => expect(cb.disabled).toBe(true));
    expect(within(main).getByText("Pipeline aktiv")).toBeInTheDocument();
  });

  it("shows the library status badge on VOD cards", async () => {
    setVods([vodA, vodB]);
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Bravo VOD")).toBeInTheDocument());
    // Bravo VOD is READY -> "In Library" badge.
    expect(within(main).getByText("In Library")).toBeInTheDocument();
  });

  it("keeps the direct URL import available and uses the same start flow", async () => {
    setVods([vodA]);
    mock.setResponse("POST /api/vod-pipeline/runs", 201, {
      schema_version: 2,
      id: "run-url",
      source_type: "twitch_vod",
      source_id: vodA.id,
      profile_id: profile.id,
      status: "RUNNING",
      steps: [],
      error: null,
      created_at: "2024-01-01T00:00:00+00:00",
      updated_at: "2024-01-01T00:00:00+00:00",
      completed_at: null,
      source: {
        provider: "twitch",
        type: "vod",
        external_id: "100",
        url: "https://www.twitch.tv/videos/100",
        profile_id: profile.id,
        title: "Alpha VOD",
        thumbnail_url: null,
        duration_seconds: 60,
        legacy: false,
      },
      progress: 0,
      current_step: null,
      started_at: "2024-01-01T00:00:00+00:00",
      library_item_id: null,
      transcript_id: null,
    });
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    const input = await waitFor(() =>
      within(main).getByLabelText("Twitch-VOD- oder Clip-URL") as HTMLInputElement,
    );
    await user.type(input, "https://www.twitch.tv/videos/100");
    await user.click(within(main).getByRole("button", { name: /^Pipeline starten/ }));
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url === "/api/vod-pipeline/runs" && c.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("keeps the Aktiv and Verlauf tabs", async () => {
    setVods([vodA]);
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    expect(within(main).getByRole("tab", { name: /Aktiv/ })).toBeInTheDocument();
    expect(within(main).getByRole("tab", { name: /Verlauf/ })).toBeInTheDocument();
  });

  it("renders an empty state when no profiles exist", async () => {
    mock.setResponse("GET /api/twitch/profiles", 200, { profiles: [] });
    mock.setResponse("GET /api/vods", 200, { vods: [] });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() =>
      expect(within(main).getByText("Keine Twitch-Profile vorhanden.")).toBeInTheDocument(),
    );
  });

  it("renders an empty state when profiles exist but no VODs are synced", async () => {
    mock.setResponse("GET /api/vods", 200, { vods: [] });
    mock.setResponse(`GET /api/vods?profile_id=${profile.id}`, 200, { vods: [] });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Keine VODs gefunden.")).toBeInTheDocument());
  });

  it("triggers VOD synchronization via the existing sync endpoint", async () => {
    setVods([vodA]);
    mock.setResponse(`POST /api/twitch/profiles/${profile.id}/sync-vods`, 200, {
      created: 0,
      updated: 0,
      unchanged: 1,
      total: 1,
    });
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-pipeline"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getByText("Alpha VOD")).toBeInTheDocument());
    // Switch to the specific profile so sync targets only that profile.
    const filter = within(main).getByLabelText("Twitch-Profil filtern") as HTMLSelectElement;
    await user.selectOptions(filter, profile.id);
    await user.click(within(main).getByRole("button", { name: /VODs synchronisieren/ }));
    await waitFor(() => {
      const call = mock.calls.find(
        (c) =>
          c.url === `/api/twitch/profiles/${profile.id}/sync-vods` && c.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });
});
