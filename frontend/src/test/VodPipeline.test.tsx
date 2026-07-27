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
    voice_cloning: "not_implemented",
    vod_pipeline: "available",
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
};

const twitchStatusOk = {
  available: true,
  downloader_available: true,
  yt_dlp_version: "2026.07.04",
  ffprobe_available: true,
  download_dir_writable: true,
  reasons: [],
  warnings: [],
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

const vodDiscovered = {
  schema_version: 1,
  id: "22222222-2222-2222-2222-222222222222",
  profile_id: profile.id,
  twitch_video_id: "100",
  source_url: "https://www.twitch.tv/videos/100",
  title: "First VOD",
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

const vodReady = {
  ...vodDiscovered,
  id: "33333333-3333-3333-3333-333333333333",
  twitch_video_id: "101",
  source_url: "https://www.twitch.tv/videos/101",
  title: "Ready VOD",
  status: "READY",
  download: {
    started_at: "2024-01-01T00:00:00+00:00",
    completed_at: "2024-01-01T01:00:00+00:00",
    file_name: "source.mp4",
    file_size_bytes: 1024,
    container: "mp4",
    duration_seconds: 60,
    width: 1920,
    height: 1080,
    video_codec: "h264",
    audio_codec: "aac",
  },
};

function mainOf(container: HTMLElement): HTMLElement {
  return (container.querySelector("#main-content") as HTMLElement | null) ?? container;
}

describe("VOD Pipeline frontend", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, statusResponse);
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
    mock.setResponse("GET /api/twitch/status", 200, twitchStatusOk);
    mock.setResponse("GET /api/twitch/profiles", 200, { profiles: [profile] });
    mock.setResponse("GET /api/vods", 200, { vods: [vodDiscovered, vodReady] });
    mock.setResponse(`GET /api/vods?profile_id=${profile.id}`, 200, { vods: [vodDiscovered, vodReady] });
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the VOD Downloader route with profile and VOD list", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "VOD Downloader", level: 1 })).toBeInTheDocument();
    });
    // Profile card is rendered.
    await waitFor(() => {
      expect(within(main).getAllByText("casepayt").length).toBeGreaterThanOrEqual(1);
    });
  });

  it("renders the Twitch-Profile route", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/twitch-profiles"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Twitch-Profile", level: 1 })).toBeInTheDocument();
    });
  });

  it("redirects /vod-explorer to /vod-downloader", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-explorer"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "VOD Downloader", level: 1 })).toBeInTheDocument();
    });
  });

  it("shows the VOD Downloader module card on the dashboard", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/dashboard"],
    });
    const main = mainOf(container);
    // The dashboard renders the VOD Downloader section heading.
    await waitFor(() => {
      const headings = within(main).getAllByRole("heading", { name: "VOD Downloader" });
      expect(headings.length).toBeGreaterThanOrEqual(1);
    });
    // VOD Downloader aggregate section.
    expect(within(main).getAllByText("Twitch-Profile").length).toBeGreaterThanOrEqual(1);
  });

  it("selecting a profile loads its VODs and shows download buttons", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getAllByText("casepayt").length).toBeGreaterThanOrEqual(1);
    });
    // Click the profile card (button with aria-pressed).
    const profileBtn = within(main).getByRole("button", { name: /casepayt/ });
    await user.click(profileBtn);
    // VOD titles appear.
    await waitFor(() => {
      expect(within(main).getByText("First VOD")).toBeInTheDocument();
      expect(within(main).getByText("Ready VOD")).toBeInTheDocument();
    });
    // DISCOVERED VOD has an "Auf Server laden" (primary) and "Herunterladen" (secondary) button.
    expect(within(main).getAllByRole("button", { name: /auf server laden/i }).length).toBeGreaterThanOrEqual(1);
    expect(within(main).getAllByRole("button", { name: /herunterladen/i }).length).toBeGreaterThanOrEqual(1);
  });

  it("renders a download button that triggers the stream-download endpoint", async () => {
    const user = userEvent.setup();
    mock.setResponse(`GET /api/vods/${vodDiscovered.id}/stream-download`, 200, "");
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getAllByText("casepayt").length).toBeGreaterThanOrEqual(1));
    await user.click(within(main).getByRole("button", { name: /casepayt/ }));
    await waitFor(() => expect(within(main).getByText("First VOD")).toBeInTheDocument());
    const dlButtons = within(main).getAllByRole("button", { name: /herunterladen/i });
    // Click the first download button (First VOD is rendered first).
    await user.click(dlButtons[0]);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url === `/api/vods/${vodDiscovered.id}/stream-download` && c.method === "GET",
      );
      expect(call).toBeDefined();
    });
  });

  it("starts a library download when the 'Auf Server laden' button is clicked", async () => {
    const user = userEvent.setup();
    mock.setResponse(`POST /api/vods/${vodDiscovered.id}/download`, 200, {
      ...vodDiscovered,
      status: "QUEUED",
    });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getAllByText("casepayt").length).toBeGreaterThanOrEqual(1));
    await user.click(within(main).getByRole("button", { name: /casepayt/ }));
    await waitFor(() => expect(within(main).getByText("First VOD")).toBeInTheDocument());
    const loadButtons = within(main).getAllByRole("button", { name: /auf server laden/i });
    await user.click(loadButtons[0]);
    await waitFor(() => {
      const call = mock.calls.find(
        (c) => c.url === `/api/vods/${vodDiscovered.id}/download` && c.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("rejects invalid VOD URLs in the import form", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => expect(within(main).getAllByText("casepayt").length).toBeGreaterThanOrEqual(1));
    await user.click(within(main).getByRole("button", { name: /casepayt/ }));
    const input = within(main).getByPlaceholderText(/twitch\.tv\/videos\//) as HTMLInputElement;
    await user.type(input, "https://youtube.com/watch?v=1");
    mock.setResponse("POST /api/vods/import", 400, {
      detail: { code: "vod_validation", message: "Only twitch.tv VOD or clip URLs are supported." },
    });
    await user.click(within(main).getByRole("button", { name: /Importieren/ }));
    await waitFor(() => {
      expect(within(main).getByText(/Only twitch\.tv VOD or clip/)).toBeInTheDocument();
    });
  });

  it("renders the VOD Downloader route even when yt-dlp is missing", async () => {
    mock.setResponse("GET /api/twitch/status", 200, {
      ...twitchStatusOk,
      available: false,
      downloader_available: false,
      yt_dlp_version: null,
      reasons: ["yt-dlp is not installed or not on PATH"],
    });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "VOD Downloader", level: 1 })).toBeInTheDocument();
    });
    // Profile card is still rendered.
    await waitFor(() => {
      expect(within(main).getAllByText("casepayt").length).toBeGreaterThanOrEqual(1);
    });
  });

  it("never renders a secret value on the VOD Downloader page", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/vod-downloader"],
    });
    const main = mainOf(container);
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "VOD Downloader", level: 1 })).toBeInTheDocument();
    });
    expect(main.textContent ?? "").not.toContain("fake-token");
    expect(main.textContent ?? "").not.toContain("csec");
  });
});


