import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, installFetchMock } from "../../../test/test-utils";
import { VoiceProfilesPanel } from "../VoiceProfilesPanel";
import type { VoiceProfile } from "../types";

const script1 = {
  id: "s1",
  order: 1,
  style: "neutral",
  category: "greeting",
  text: "Hallo und willkommen.",
  recommended_duration_seconds: { min: 3, max: 5 },
  tags: [],
  recording_notes: "Ruhig sprechen.",
};
const script2 = {
  id: "s2",
  order: 2,
  style: "formal",
  category: "closing",
  text: "Vielen Dank und auf Wiedersehen.",
  recommended_duration_seconds: { min: 4, max: 6 },
  tags: [],
  recording_notes: null,
};
const holdoutScript = {
  id: "h1",
  order: 1,
  style: "holdout",
  category: "qa",
  text: "Holdout-Text für spätere Prüfung.",
  recommended_duration_seconds: { min: 5, max: 8 },
  tags: [],
  recording_notes: null,
};

const progress = {
  total: 2,
  accepted: 1,
  review: 1,
  rejected: 0,
  missing: 0,
  recorded: 2,
  percentage: 50,
  clone_ready: false,
  pack_complete: false,
};

function makeRef(scriptId: string, filename: string, status: string, scriptText: string) {
  return {
    script_id: scriptId,
    script_text: scriptText,
    category: "greeting",
    style: "neutral",
    recording_filename: filename,
    recording_sha256: "deadbeef",
    quality: { voice_clone_reference: { quality: "GOOD" } },
    quality_class: "GOOD",
    status,
    review_accepted: false,
    attached_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-01T00:00:00+00:00",
  };
}

const profileWithRefs: VoiceProfile = {
  id: "p1",
  name: "Meine Stimme",
  locale: "de-DE",
  created_at: "2026-01-01T00:00:00+00:00",
  references: {
    s1: makeRef("s1", "a.wav", "ACCEPTED", "Hallo und willkommen."),
    s2: makeRef("s2", "b.wav", "REVIEW", "Vielen Dank und auf Wiedersehen."),
  },
  progress,
};

function setProfileResponse(profile: VoiceProfile = profileWithRefs) {
  // The list and the single-profile endpoint both return the same shape.
  mock.setResponse("GET /api/voice-profiles", 200, { profiles: [profile] });
  mock.setResponse(`GET /api/voice-profiles/${profile.id}`, 200, profile);
}

let mock: ReturnType<typeof installFetchMock>;

function renderPanel() {
  return renderWithProviders(<VoiceProfilesPanel />);
}

beforeEach(() => {
  mock = installFetchMock();
  setProfileResponse();
  mock.setResponse("GET /api/voice-profiles/scripts", 200, {
    pack: { pack_id: "pack1", locale: "de-DE", prompt_count: 2, title: "Test" },
    prompts: [script1, script2],
  });
  mock.setResponse("GET /api/voice-profiles/holdout-scripts", 200, {
    pack: { pack_id: "holdout1", locale: "de-DE", prompt_count: 1, title: "Holdout" },
    prompts: [holdoutScript],
  });
});

afterEach(() => {
  mock.restore();
});

describe("VoiceProfilesPanel — empty / loading / error", () => {
  it("shows an empty state when there are no profiles", async () => {
    mock.setResponse("GET /api/voice-profiles", 200, { profiles: [] });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("Noch keine Voice-Profile")).toBeInTheDocument();
    });
  });

  it("shows a profile error state when the backend is offline", async () => {
    mock.setResponse("GET /api/voice-profiles", 500, { detail: "boom" });
    renderPanel();
    // retry:1 with exponential backoff can take ~1s before the query errors.
    expect(
      await screen.findByText(
        "Profile konnten nicht geladen werden",
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
  });
});

describe("VoiceProfilesPanel — profile list and selection", () => {
  it("shows the profile name and progress in the list", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("Meine Stimme")).toBeInTheDocument();
    });
    expect(screen.getByText(/Akzeptiert: 1/)).toBeInTheDocument();
  });

  it("selects a profile and shows the detail view", async () => {
    const user = userEvent.setup();
    renderPanel();
    const profileBtn = await screen.findByRole("button", {
      name: "Profil Meine Stimme auswählen",
    });
    await user.click(profileBtn);
    await waitFor(() => {
      // Detail header shows the locale.
      expect(screen.getByText("Locale: de-DE")).toBeInTheDocument();
    });
  });
});

describe("VoiceProfilesPanel — progress", () => {
  it("renders the progress breakdown from the server", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    const progress = await screen.findByRole("group", { name: "Aufnahmefortschritt" });
    expect(within(progress).getByText("Akzeptiert")).toBeInTheDocument();
    expect(within(progress).getByText("Review")).toBeInTheDocument();
    expect(within(progress).getByText("Abgelehnt")).toBeInTheDocument();
    expect(within(progress).getByText("Fehlend")).toBeInTheDocument();
    expect(within(progress).getByText("50%")).toBeInTheDocument();
  });
});

describe("VoiceProfilesPanel — prompts", () => {
  it("renders scripts sorted by order", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Prompt 1: Hallo und willkommen." })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Prompt 2: Vielen Dank und auf Wiedersehen." })).toBeInTheDocument();
  });

  it("selects the next missing script when no references are accepted", async () => {
    const user = userEvent.setup();
    const profileNoRefs = {
      ...profileWithRefs,
      references: {},
      progress: { ...progress, accepted: 0, missing: 2, review: 0, recorded: 0, percentage: 0 },
    };
    setProfileResponse(profileNoRefs);
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await user.click(await screen.findByRole("button", { name: /Nächstes fehlendes Skript/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Prompt 1: Hallo und willkommen." })).toBeInTheDocument();
    });
  });

  it("renders holdouts without record actions", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await waitFor(() => {
      expect(screen.getByText("Holdout-Skripte")).toBeInTheDocument();
    });
    expect(
      screen.getByText("Nur zur späteren Qualitätsprüfung. Nicht als Referenz aufnehmen."),
    ).toBeInTheDocument();
    expect(screen.getByText("Holdout-Text für spätere Prüfung.")).toBeInTheDocument();
    // No "Jetzt aufnehmen" button inside the holdout section.
    const holdoutSection = screen.getByText("Holdout-Skripte").closest("section")!;
    expect(within(holdoutSection).queryByRole("button", { name: /Jetzt aufnehmen/ })).toBeNull();
  });
});

describe("VoiceProfilesPanel — references", () => {
  it("shows ACCEPTED, REVIEW, REJECTED and MISSING statuses as text", async () => {
    const user = userEvent.setup();
    const profile = {
      ...profileWithRefs,
      references: {
        s1: makeRef("s1", "a.wav", "ACCEPTED", "Hallo und willkommen."),
        s2: makeRef("s2", "b.wav", "REJECTED", "Vielen Dank und auf Wiedersehen."),
      },
      progress: { ...progress, accepted: 1, review: 0, rejected: 1, missing: 0, recorded: 2, percentage: 50 },
    };
    setProfileResponse(profile);
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await waitFor(() => {
      expect(screen.getAllByText("Akzeptiert").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("Abgelehnt").length).toBeGreaterThan(0);
  });

  it("accepts a review reference", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    // Select the REVIEW script (s2).
    await user.click(await screen.findByRole("button", { name: "Prompt 2: Vielen Dank und auf Wiedersehen." }));
    // The accept-review endpoint returns the updated profile.
    const updatedProfile = {
      ...profileWithRefs,
      references: {
        ...profileWithRefs.references,
        s2: { ...profileWithRefs.references.s2, status: "ACCEPTED", review_accepted: true },
      },
      progress: { ...progress, accepted: 2, review: 0, percentage: 100 },
    };
    mock.setResponse(
      "POST /api/voice-profiles/p1/references/s2/accept-review",
      200,
      updatedProfile,
    );
    const acceptBtn = await screen.findByRole("button", { name: /Review ausdrücklich akzeptieren/ });
    await user.click(acceptBtn);
    await waitFor(() => {
      expect(
        mock.calls.some(
          (c) =>
            c.method === "POST" &&
            c.url.includes("/api/voice-profiles/p1/references/s2/accept-review"),
        ),
      ).toBe(true);
    });
  });

  it("detaches a reference", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await user.click(await screen.findByRole("button", { name: "Prompt 1: Hallo und willkommen." }));
    // The detach endpoint returns the updated profile (without the reference).
    const updatedProfile = {
      ...profileWithRefs,
      references: { s2: profileWithRefs.references.s2 },
      progress: { ...progress, accepted: 0, review: 1, missing: 1, recorded: 1, percentage: 0 },
    };
    mock.setResponse(
      "DELETE /api/voice-profiles/p1/references/s1",
      200,
      updatedProfile,
    );
    const detachBtn = await screen.findByRole("button", { name: /Verknüpfung entfernen/ });
    await user.click(detachBtn);
    await waitFor(() => {
      expect(
        mock.calls.some(
          (c) =>
            c.method === "DELETE" &&
            c.url.includes("/api/voice-profiles/p1/references/s1"),
        ),
      ).toBe(true);
    });
  });
});

describe("VoiceProfilesPanel — delete dialog", () => {
  it("opens a dialog that explains WAV files are preserved", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await user.click(screen.getByRole("button", { name: /Löschen/ }));
    expect(await screen.findByText("Profil löschen?")).toBeInTheDocument();
    expect(screen.getByText(/Die zugrunde liegenden WAV-Aufnahmen bleiben erhalten/)).toBeInTheDocument();
  });

  it("does not delete when the dialog is cancelled", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await user.click(screen.getByRole("button", { name: /Löschen/ }));
    await user.click(await screen.findByRole("button", { name: "Abbrechen" }));
    expect(mock.calls.some((c) => c.method === "DELETE" && c.url.includes("/api/voice-profiles/p1"))).toBe(false);
  });

  it("calls the delete mutation once when confirmed", async () => {
    const user = userEvent.setup();
    mock.setResponse("DELETE /api/voice-profiles/p1", 200, { id: "p1", deleted: true });
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Profil Meine Stimme auswählen" }),
    );
    await user.click(screen.getByRole("button", { name: /Löschen/ }));
    // The confirm dialog button is labelled "Löschen" too; the destructive
    // one lives inside the dialog. Use findByRole within the dialog.
    const dialog = await screen.findByText("Profil löschen?");
    const section = dialog.closest("div")!;
    const confirmBtn = within(section).getByRole("button", { name: "Löschen" });
    await user.click(confirmBtn);
    await waitFor(() => {
      expect(
        mock.calls.filter(
          (c) => c.method === "DELETE" && c.url === "/api/voice-profiles/p1",
        ).length,
      ).toBe(1);
    });
  });
});
