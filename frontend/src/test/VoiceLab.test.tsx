import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
  recordings: { count: 0, total_duration_seconds: 0, total_size_bytes: 0 },
  storage: { free_bytes: 1000 },
  features: {
    recording: "available",
    voice_cloning: "not_implemented",
    vod_analysis: "not_implemented",
    video_editor: "not_implemented",
  },
};

const sampleRecording = {
  filename: "abc123.wav",
  created_at: "2026-01-01T12:00:00+00:00",
  duration_seconds: 5.2,
  file_size_bytes: 1024,
  audio_url: "/api/recordings/abc123.wav",
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

describe("VoiceLabPage", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
  });

  afterEach(() => {
    mock.restore();
  });

  it("shows recordings when present", async () => {
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    const { main } = renderVoiceLab();
    await waitFor(() => {
      expect(within(main).getByText("abc123.wav")).toBeInTheDocument();
    });
  });

  it("shows an empty state when there are no recordings", async () => {
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
    const { main } = renderVoiceLab();
    await waitFor(() => {
      expect(within(main).getByText("Noch keine Aufnahmen vorhanden")).toBeInTheDocument();
    });
  });

  it("opens a confirm dialog when clicking delete", async () => {
    const user = userEvent.setup();
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    renderVoiceLab();
    await waitFor(() => {
      expect(screen.getByText("abc123.wav")).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /abc123\.wav löschen/i }));
    expect(
      await screen.findByText("Aufnahme endgültig löschen?"),
    ).toBeInTheDocument();
  });

  it("calls the delete mutation when the dialog is confirmed", async () => {
    const user = userEvent.setup();
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    mock.setResponse("DELETE /api/recordings/abc123.wav", 200, {
      filename: "abc123.wav",
      deleted: true,
    });
    renderVoiceLab();
    await waitFor(() => {
      expect(screen.getByText("abc123.wav")).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /abc123\.wav löschen/i }));
    await user.click(await screen.findByRole("button", { name: "Löschen" }));
    await waitFor(() => {
      expect(
        mock.calls.some(
          (c) => c.method === "DELETE" && c.url.includes("abc123.wav"),
        ),
      ).toBe(true);
    });
  });

  it("does not call the delete mutation when the dialog is cancelled", async () => {
    const user = userEvent.setup();
    mock.setResponse("GET /api/recordings", 200, { recordings: [sampleRecording] });
    renderVoiceLab();
    await waitFor(() => {
      expect(screen.getByText("abc123.wav")).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /abc123\.wav löschen/i }));
    await user.click(await screen.findByRole("button", { name: "Abbrechen" }));
    expect(mock.calls.some((c) => c.method === "DELETE")).toBe(false);
  });
});
