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
      expect(within(main).getByRole("heading", { name: "Dashboard", level: 1 })).toBeInTheDocument();
    });
  });

  it("renders the voice-lab route", async () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/voice-lab"],
    });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Voice Lab", level: 1 })).toBeInTheDocument();
    });
    expect(within(main).getByText("Recorder")).toBeInTheDocument();
  });

  it("shows the 404 page for unknown routes", () => {
    const { container } = renderWithProviders(<AppRouter />, {
      initialEntries: ["/does-not-exist"],
    });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    expect(within(main).getByText("Seite nicht gefunden")).toBeInTheDocument();
  });

  it("marks the active sidebar route", () => {
    renderWithProviders(<AppRouter />, { initialEntries: ["/voice-lab"] });
    const link = screen.getByRole("link", { name: "Voice Lab" });
    expect(link.className).toContain("is-active");
  });

  it("redirects / to /dashboard", async () => {
    const { container } = renderWithProviders(<AppRouter />, { initialEntries: ["/"] });
    const main = (container.querySelector("#main-content") as HTMLElement | null) ?? container;
    await waitFor(() => {
      expect(within(main).getByRole("heading", { name: "Dashboard", level: 1 })).toBeInTheDocument();
    });
  });
});
