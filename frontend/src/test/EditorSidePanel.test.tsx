import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EditorSidePanel } from "../components/projects/EditorSidePanel";
import { renderWithProviders } from "./test-utils";

const baseProps = {
  selectedLabel: "Clip A",
  branches: [],
  activeBranchId: undefined,
  checkoutCommitId: "commit-1",
  detachedCommitId: undefined,
  commits: [],
  onCheckoutBranch: () => undefined,
  onCheckoutCommit: () => undefined,
  onCreateVariant: () => undefined,
};

describe("EditorSidePanel", () => {
  it("submits a command and shows the success result from the handler", async () => {
    const user = userEvent.setup();
    const onExecuteCommand = vi.fn().mockResolvedValue("Clip zentriert.");
    renderWithProviders(<EditorSidePanel {...baseProps} onExecuteCommand={onExecuteCommand} />);

    const textarea = screen.getByPlaceholderText(/Verschiebe den Clip/);
    await user.type(textarea, "Zentriere den Clip");
    await user.click(screen.getByRole("button", { name: "Anwenden" }));

    expect(onExecuteCommand).toHaveBeenCalledWith("Zentriere den Clip");
    expect(await screen.findByText("Clip zentriert.")).toBeInTheDocument();
  });

  it("shows an error entry when the handler rejects", async () => {
    const user = userEvent.setup();
    const onExecuteCommand = vi.fn().mockRejectedValue(new Error("Befehl nicht erkannt."));
    renderWithProviders(<EditorSidePanel {...baseProps} onExecuteCommand={onExecuteCommand} />);

    const textarea = screen.getByPlaceholderText(/Verschiebe den Clip/);
    await user.type(textarea, "mach was sinnloses");
    await user.click(screen.getByRole("button", { name: "Anwenden" }));

    expect(await screen.findByText("Befehl nicht erkannt.")).toBeInTheDocument();
  });

  it("fills the textarea when an example chip is clicked", async () => {
    const user = userEvent.setup();
    const onExecuteCommand = vi.fn().mockResolvedValue("ok");
    renderWithProviders(<EditorSidePanel {...baseProps} onExecuteCommand={onExecuteCommand} />);

    await user.click(screen.getByRole("button", { name: "Stummschalten" }));
    await user.click(screen.getByRole("button", { name: "Anwenden" }));

    expect(onExecuteCommand).toHaveBeenCalledWith("Stummschalten");
  });
});
