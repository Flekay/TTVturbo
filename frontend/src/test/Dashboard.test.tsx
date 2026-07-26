import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DashboardPage } from "../pages/DashboardPage";
import { AppLayout } from "../components/layout/AppLayout";
import { renderWithProviders, installFetchMock } from "../test/test-utils";
import type { BackendStatus } from "../types/status";

const baseStatus: BackendStatus = {
  status: "online",
  app_name: "TTVturbo",
  version: "0.1.0",
  uptime_seconds: 100,
  recordings: { count: 4, total_duration_seconds: 38.4, total_size_bytes: 3712240 },
  storage: { free_bytes: 1820000000000 },
  features: {
    recording: "available",
    voice_cloning: "not_implemented",
    vod_analysis: "not_implemented",
    video_editor: "not_implemented",
  },
};

function renderDashboard() {
  const result = renderWithProviders(
    <AppLayout>
      <DashboardPage />
    </AppLayout>,
    { initialEntries: ["/dashboard"] },
  );
  const main =
    (result.container.querySelector("#main-content") as HTMLElement | null) ??
    result.container;
  return { ...result, main };
}

describe("DashboardPage", () => {
  let mock: ReturnType<typeof installFetchMock>;

  beforeEach(() => {
    mock = installFetchMock();
  });

  afterEach(() => {
    mock.restore();
  });

  it("renders real status values", async () => {
    mock.setResponse("GET /api/status", 200, baseStatus);
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
    const { main } = renderDashboard();
    await waitFor(() => {
      expect(within(main).getByText("online")).toBeInTheDocument();
    });
    expect(within(main).getByText("0.1.0")).toBeInTheDocument();
    expect(within(main).getByText("4")).toBeInTheDocument();
  });

  it("shows a loading state initially", () => {
    mock.setResponse("GET /api/status", 200, baseStatus);
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
    const { main } = renderDashboard();
    expect(within(main).getByText(/Lade Systemstatus/)).toBeInTheDocument();
  });

  it("shows an error state when the backend is unreachable", async () => {
    mock.setResponse("GET /api/status", 500, { detail: "boom" });
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
    const { main } = renderDashboard();
    await waitFor(
      () => {
        expect(within(main).getByText("Systemstatus nicht verfügbar")).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
  });

  it("retries on retry button click", async () => {
    const user = userEvent.setup();
    mock.setResponse("GET /api/status", 500, { detail: "boom" });
    mock.setResponse("GET /api/recordings", 200, { recordings: [] });
    const { main } = renderDashboard();
    await waitFor(
      () => {
        expect(within(main).getByText("Systemstatus nicht verfügbar")).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
    // Now make the backend respond successfully and click retry.
    mock.setResponse("GET /api/status", 200, baseStatus);
    await user.click(screen.getByRole("button", { name: /Erneut versuchen/i }));
    await waitFor(
      () => {
        expect(within(main).getByText("online")).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
  });
});
