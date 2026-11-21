import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../src/components/ui/tooltip";

afterEach(cleanup);

function ExampleTooltip() {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip open>
        <TooltipTrigger asChild>
          <button type="button">Details</button>
        </TooltipTrigger>
        <TooltipContent>Open details</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

describe("Tooltip", () => {
  it("exposes the tooltip text to assistive technology", () => {
    render(<ExampleTooltip />);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip.textContent).toContain("Open details");
  });
});
