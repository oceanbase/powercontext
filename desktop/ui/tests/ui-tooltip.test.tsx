/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

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
