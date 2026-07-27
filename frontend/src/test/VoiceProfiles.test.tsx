import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { waitFor, within } from "@testing-library/react";
import { VoiceProfilesPage } from "../pages/VoiceProfilesPage";
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

function renderVoiceProfiles() {
  const result = renderWithProviders(
    <AppLayout>
      <VoiceProfilesPage />
    </AppLayout>,
    { initialEntries: ["/voice-profiles"] },
  );
  const main =
    (result.container.querySelector("#main-content") as HTMLElement | null) ??
    result.container;
  return { ...result, main };
}

describe("VoiceProfilesPage", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, status);
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
    mock.setResponse("GET /api/voice-profiles/scripts", 200, {
      pack: { pack_id: "pack1", locale: "de-DE", prompt_count: 0, title: "Test" },
      prompts: [],
    });
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the Voice Profiles heading", async () => {
    const { main } = renderVoiceProfiles();
    await waitFor(() => {
      expect(
        within(main).getByRole("button", { name: /Neues Profil/ }),
      ).toBeInTheDocument();
    });
  });

  it("does not render the old recordings or voice-clone tabs", () => {
    const { main } = renderVoiceProfiles();
    expect(within(main).queryByRole("tab", { name: /Aufnahmen/i })).toBeNull();
    expect(within(main).queryByRole("tab", { name: /Voice Clone/i })).toBeNull();
    expect(within(main).queryByRole("tab", { name: /Generierungen/i })).toBeNull();
  });
});
