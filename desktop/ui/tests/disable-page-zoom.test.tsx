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

import { describe, expect, it } from "vitest";
import { disablePageZoom } from "../src/app/disable-page-zoom";

describe("disablePageZoom", () => {
  it("blocks trackpad pinch zoom without blocking ordinary scrolling", () => {
    const cleanup = disablePageZoom(document);
    const pinch = new WheelEvent("wheel", { cancelable: true, ctrlKey: true });
    const scroll = new WheelEvent("wheel", { cancelable: true });

    document.dispatchEvent(pinch);
    document.dispatchEvent(scroll);

    expect(pinch.defaultPrevented).toBe(true);
    expect(scroll.defaultPrevented).toBe(false);
    cleanup();
  });

  it("blocks native gesture zoom events", () => {
    const cleanup = disablePageZoom(document);
    const gestureStart = new Event("gesturestart", { cancelable: true });
    const gestureChange = new Event("gesturechange", { cancelable: true });

    document.dispatchEvent(gestureStart);
    document.dispatchEvent(gestureChange);

    expect(gestureStart.defaultPrevented).toBe(true);
    expect(gestureChange.defaultPrevented).toBe(true);
    cleanup();
  });
});
