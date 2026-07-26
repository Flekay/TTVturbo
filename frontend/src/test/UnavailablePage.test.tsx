import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { UnavailablePage } from "../pages/UnavailablePage";

describe("UnavailablePage", () => {
  it("renders the title and description", () => {
    render(
      <UnavailablePage
        title="VOD Explorer"
        description="Später werden hier neue Twitch-VODs erkannt."
        plannedFeatures={["VODs erkennen", "VODs herunterladen"]}
      />,
    );
    expect(screen.getByText("VOD Explorer")).toBeInTheDocument();
    expect(screen.getByText(/Twitch-VODs erkannt/)).toBeInTheDocument();
  });

  it("renders the planned features list", () => {
    render(
      <UnavailablePage
        title="VOD Explorer"
        description="desc"
        plannedFeatures={["Feature A", "Feature B", "Feature C"]}
      />,
    );
    expect(screen.getByText("Feature A")).toBeInTheDocument();
    expect(screen.getByText("Feature B")).toBeInTheDocument();
    expect(screen.getByText("Feature C")).toBeInTheDocument();
  });

  it("does not render any fake action buttons", () => {
    render(
      <UnavailablePage
        title="VOD Explorer"
        description="desc"
        plannedFeatures={["Feature A"]}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows the not-implemented status badge", () => {
    render(
      <UnavailablePage
        title="VOD Explorer"
        description="desc"
        plannedFeatures={[]}
      />,
    );
    expect(screen.getByText(/Noch nicht implementiert/)).toBeInTheDocument();
  });
});
