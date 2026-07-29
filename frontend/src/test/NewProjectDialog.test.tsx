import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NewProjectDialog } from "../components/projects/NewProjectDialog";
import { renderWithProviders } from "./test-utils";

describe("NewProjectDialog", () => {
  it("creates an empty mobile project with the selected format", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<NewProjectDialog open onOpenChange={() => undefined} onCreate={onCreate} />);

    await user.click(screen.getByRole("button", { name: /Mobile/ }));
    const name = screen.getByLabelText("Projektname");
    await user.clear(name);
    await user.type(name, "Mobile Short");
    await user.click(screen.getByRole("button", { name: "Projekt erstellen" }));

    expect(onCreate).toHaveBeenCalledWith({
      name: "Mobile Short",
      sequence: {
        name: "Mobile",
        width: 1080,
        height: 1920,
        fps_numerator: 60,
        fps_denominator: 1,
        format_profile: "MOBILE_9_16",
        safe_area_enabled: true,
        safe_area_margin_top: 250,
        safe_area_margin_right: 160,
        safe_area_margin_bottom: 340,
        safe_area_margin_left: 0,
      },
    });
  });

  it("switches to custom when dimensions are edited", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<NewProjectDialog open onOpenChange={() => undefined} onCreate={onCreate} />);

    const width = screen.getByLabelText("Breite");
    const height = screen.getByLabelText("Höhe");
    await user.clear(width);
    await user.type(width, "1440");
    await user.clear(height);
    await user.type(height, "1440");
    await user.click(screen.getByRole("button", { name: "Projekt erstellen" }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      sequence: expect.objectContaining({ width: 1440, height: 1440, format_profile: "CUSTOM" }),
    }));
  });
});
