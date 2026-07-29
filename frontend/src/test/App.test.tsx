import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, within, waitFor } from "@testing-library/react";
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
    vod_analysis: "not_implemented",
    video_editor: "not_implemented",
  },
};

describe("App routing", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
    mock.setResponse("GET /api/status", 200, statusResponse);
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders the dashboard route", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/dashboard"],
    });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Weiterarbeiten oder etwas Neues erstellen" })).toBeInTheDocument();
    });
  });

  it("renders the voice-profiles route", async () => {
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
    mock.setResponse("GET /api/voice-profiles/scripts", 200, {
      pack: { pack_id: "pack1", locale: "de-DE", prompt_count: 0, title: "Test" },
      prompts: [],
    });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/voice-profiles"],
    });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    await waitFor(() => {
      expect(within(main).getByRole("button", { name: /Neues Profil/ })).toBeInTheDocument();
    });
  });

  it("renders the voice-clone route", async () => {
    mock.setResponse("GET /api/voice-clone/status", 200, {
      available: true,
      busy: false,
      active_generation_id: null,
      model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    });
    mock.setResponse("GET /api/voice-clone/generations", 200, { generations: [] });
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/voice-clone"],
    });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Voice Clone (Qwen3-TTS)" })).toBeInTheDocument();
    });
  });

  it("shows the 404 page for unknown routes", () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/does-not-exist"],
    });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    expect(within(main).getByText("Seite nicht gefunden")).toBeInTheDocument();
  });

  it("shows only the consolidated primary navigation and marks it active", () => {
    renderWithProviders(<AppRouter />, { initialEntries: ["/create"] });
    const navigation = screen.getByRole("navigation", { name: "Hauptnavigation" });
    expect(within(navigation).getAllByRole("link")).toHaveLength(6);
    expect(within(navigation).queryByRole("link", { name: "Voice Profiles" })).not.toBeInTheDocument();
    const link = within(navigation).getByRole("link", { name: "Create" });
    expect(link.className).toContain("is-active");
  });

  it("redirects / to /dashboard", async () => {
    const { container } = renderWithProviders(<AppRouter />, { initialEntries: ["/"] });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Weiterarbeiten oder etwas Neues erstellen" })).toBeInTheDocument();
    });
  });
});
