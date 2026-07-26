import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { waitFor, within } from "@testing-library/react";
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
    voice_cloning: "available",
    vod_analysis: "not_implemented",
    video_editor: "not_implemented",
  },
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
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
    mock.setResponse("GET /api/voice-profiles/scripts", 200, {
      pack: { pack_id: "pack1", locale: "de-DE", prompt_count: 0, title: "Test" },
      prompts: [],
    });
    mock.setResponse("GET /api/voice-profiles/holdout-scripts", 200, {
      pack: { pack_id: "holdout1", locale: "de-DE", prompt_count: 0, title: "Holdout" },
      prompts: [],
    });
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the Voice Profiles heading", async () => {
    const { main } = renderVoiceLab();
    await waitFor(() => {
      expect(
        within(main).getByRole("heading", { name: "Voice Profiles", level: 1 }),
      ).toBeInTheDocument();
    });
  });

  it("does not render the old recordings or voice-clone tabs", () => {
    const { main } = renderVoiceLab();
    expect(within(main).queryByRole("tab", { name: /Aufnahmen/i })).toBeNull();
    expect(within(main).queryByRole("tab", { name: /Voice Clone/i })).toBeNull();
    expect(within(main).queryByRole("tab", { name: /Generierungen/i })).toBeNull();
  });
});
