import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EditorSidePanel } from "../components/projects/EditorSidePanel";
import { renderWithProviders } from "./test-utils";

const baseProps = {
  checkoutCommitId: "commit-1",
  commits: [],
  totalCommits: 0,
  commitsLoading: false,
  hasMoreCommits: false,
  loadingMoreCommits: false,
  onCheckoutCommit: () => undefined,
  onLoadMoreCommits: () => undefined,
};

describe("EditorSidePanel", () => {
  it("submits a command and shows the success result from the handler", async () => {
    const user = userEvent.setup();
    const onExecuteCommand = vi.fn().mockResolvedValue("Clip zentriert.");
    renderWithProviders(<EditorSidePanel {...baseProps} onExecuteCommand={onExecuteCommand} />);

    const textarea = screen.getByPlaceholderText(/Verschiebe den Clip/);
    await user.type(textarea, "Zentriere den Clip");
    await user.click(screen.getByRole("button", { name: "Befehl anwenden" }));

    expect(onExecuteCommand).toHaveBeenCalledWith("Zentriere den Clip");
    expect(await screen.findByText("Clip zentriert.")).toBeInTheDocument();
  });

  it("shows an error entry when the handler rejects", async () => {
    const user = userEvent.setup();
    const onExecuteCommand = vi.fn().mockRejectedValue(new Error("Befehl nicht erkannt."));
    renderWithProviders(<EditorSidePanel {...baseProps} onExecuteCommand={onExecuteCommand} />);

    const textarea = screen.getByPlaceholderText(/Verschiebe den Clip/);
    await user.type(textarea, "mach was sinnloses");
    await user.click(screen.getByRole("button", { name: "Befehl anwenden" }));

    expect(await screen.findByText("Befehl nicht erkannt.")).toBeInTheDocument();
  });

  it("fills the textarea when an example chip is clicked", async () => {
    const user = userEvent.setup();
    const onExecuteCommand = vi.fn().mockResolvedValue("ok");
    renderWithProviders(<EditorSidePanel {...baseProps} onExecuteCommand={onExecuteCommand} />);

    await user.click(screen.getByRole("button", { name: "Stummschalten" }));
    await user.click(screen.getByRole("button", { name: "Befehl anwenden" }));

    expect(onExecuteCommand).toHaveBeenCalledWith("Stummschalten");
  });

  it("renders only commits in the version graph and checks out a selected commit", async () => {
    const user = userEvent.setup();
    const onCheckoutCommit = vi.fn();
    renderWithProviders(
      <EditorSidePanel
        {...baseProps}
        onExecuteCommand={vi.fn().mockResolvedValue("ok")}
        checkoutCommitId="commit-2"
        totalCommits={2}
        commits={[
          { id: "commit-2", message: "Clip getrimmt", created_at: "2026-07-29T18:34:13Z", parent_ids: ["commit-1"] },
          { id: "commit-1", message: "Projekt erstellt", created_at: "2026-07-29T18:30:00Z", parent_ids: [] },
        ]}
        onCheckoutCommit={onCheckoutCommit}
      />,
    );

    await user.click(screen.getByRole("tab", { name: "Versionen" }));

    expect(screen.queryByText("Varianten")).not.toBeInTheDocument();
    expect(screen.getByText("Clip getrimmt")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Projekt erstellt/ }));
    expect(onCheckoutCommit).toHaveBeenCalledWith("commit-1");
  });

  it("loads the next ten commits when the user scrolls down at the end", async () => {
    const user = userEvent.setup();
    const onLoadMoreCommits = vi.fn();
    renderWithProviders(
      <EditorSidePanel
        {...baseProps}
        onExecuteCommand={vi.fn().mockResolvedValue("ok")}
        totalCommits={20}
        hasMoreCommits
        commits={[
          { id: "commit-1", message: "Neueste Änderung", created_at: "2026-07-29T18:34:13Z", parent_ids: [] },
        ]}
        onLoadMoreCommits={onLoadMoreCommits}
      />,
    );

    await user.click(screen.getByRole("tab", { name: "Versionen" }));
    fireEvent.wheel(screen.getByLabelText("Versionsverlauf"), { deltaY: 120 });

    expect(onLoadMoreCommits).toHaveBeenCalledTimes(1);
  });
});
