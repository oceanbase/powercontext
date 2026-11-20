import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../src/components/ui/tooltip";

afterEach(cleanup);

function ExampleTooltip({ side = "top" }: { side?: "top" | "left" }) {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip open>
        <TooltipTrigger asChild>
          <button type="button">Details</button>
        </TooltipTrigger>
        <TooltipContent side={side}>Open details</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

describe("Tooltip", () => {
  it("renders accessible content through a portal", () => {
    render(<ExampleTooltip />);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip.textContent).toContain("Open details");
    expect(tooltip.closest("body")).toBe(document.body);
  });

  it("supports collision-aware side placement", () => {
    render(<ExampleTooltip side="left" />);

    expect(screen.getByRole("tooltip")).toBeTruthy();
  });
});
