import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../../test/test-utils";
import { PromptBrowser } from "../PromptBrowser";
import type { VoiceProfileReference, VoiceScript } from "../types";

const scripts: VoiceScript[] = [
  { id: "s2", order: 2, style: "neutral", category: "intro", text: "Zweites Skript." },
  { id: "s1", order: 1, style: "formal", category: "greeting", text: "Erstes Skript Hallo." },
  { id: "s3", order: 3, style: "neutral", category: "outro", text: "Drittes Skript." },
];

const references: VoiceProfileReference[] = [
  {
    script_id: "s1",
    recording_filename: "a.wav",
    status: "ACCEPTED",
    created_at: "2026-01-01T00:00:00+00:00",
  },
  {
    script_id: "s3",
    recording_filename: "b.wav",
    status: "REJECTED",
    created_at: "2026-01-01T00:00:00+00:00",
  },
];

function renderBrowser(props: Partial<Parameters<typeof PromptBrowser>[0]> = {}) {
  return renderWithProviders(
    <PromptBrowser
      scripts={scripts}
      references={references}
      selectedScriptId={null}
      onSelectScript={() => {}}
      {...props}
    />,
  );
}

describe("PromptBrowser", () => {
  it("renders scripts sorted by order", () => {
    renderBrowser();
    const items = screen.getAllByRole("button", { name: /Prompt \d+/ });
    // Three prompt rows (filter buttons also match btn class but not the name pattern).
    expect(items.length).toBe(3);
    // First item should be order 1 ("Erstes Skript Hallo.")
    expect(items[0]).toHaveAttribute("aria-label", "Prompt 1: Erstes Skript Hallo.");
  });

  it("filters by status MISSING", () => {
    renderBrowser();
    fireEvent.click(screen.getByRole("button", { name: "Fehlend" }));
    const items = screen.getAllByRole("button", { name: /Prompt \d+/ });
    // Only s2 has no reference (MISSING).
    expect(items.length).toBe(1);
    expect(items[0]).toHaveAttribute("aria-label", "Prompt 1: Zweites Skript.");
  });

  it("filters by status ACCEPTED", () => {
    renderBrowser();
    fireEvent.click(screen.getByRole("button", { name: "Akzeptiert" }));
    const items = screen.getAllByRole("button", { name: /Prompt \d+/ });
    expect(items.length).toBe(1);
    expect(items[0]).toHaveAttribute("aria-label", "Prompt 1: Erstes Skript Hallo.");
  });

  it("filters by status REJECTED", () => {
    renderBrowser();
    fireEvent.click(screen.getByRole("button", { name: "Abgelehnt" }));
    const items = screen.getAllByRole("button", { name: /Prompt \d+/ });
    expect(items.length).toBe(1);
    expect(items[0]).toHaveAttribute("aria-label", "Prompt 1: Drittes Skript.");
  });

  it("filters by text search", () => {
    renderBrowser();
    const search = screen.getByLabelText("Prompt-Text durchsuchen");
    fireEvent.change(search, { target: { value: "Erstes" } });
    const items = screen.getAllByRole("button", { name: /Prompt \d+/ });
    expect(items.length).toBe(1);
    expect(items[0]).toHaveAttribute("aria-label", "Prompt 1: Erstes Skript Hallo.");
  });

  it("filters by style", () => {
    renderBrowser();
    fireEvent.change(screen.getByLabelText("Stil filtern"), { target: { value: "formal" } });
    const items = screen.getAllByRole("button", { name: /Prompt \d+/ });
    expect(items.length).toBe(1);
  });

  it("calls onSelectScript when a prompt is clicked", () => {
    const onSelect = vi.fn();
    renderBrowser({ onSelectScript: onSelect });
    fireEvent.click(screen.getByRole("button", { name: "Prompt 1: Erstes Skript Hallo." }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("s1");
  });

  it("shows an empty result message when no scripts match", () => {
    renderBrowser();
    fireEvent.change(screen.getByLabelText("Prompt-Text durchsuchen"), {
      target: { value: "nichtvorhanden" },
    });
    expect(screen.getByText("Keine Treffer für die aktuellen Filter.")).toBeInTheDocument();
  });

  it("shows status text alongside colour", () => {
    renderBrowser();
    expect(screen.getAllByText("Akzeptiert").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Abgelehnt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fehlend").length).toBeGreaterThan(0);
  });
});
