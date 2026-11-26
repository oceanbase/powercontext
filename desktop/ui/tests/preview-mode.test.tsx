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
import { resolvePreviewMode } from "../src/app/preview-mode";

describe("development preview boundary", () => {
  it("cannot enable mock connections in a production build, including preview mode", () => {
    for (const mode of ["production", "preview"])
      for (const search of [
        "",
        "?preview=connected",
        "?preview=disconnected",
        "?preview=remote-only",
        "?preview=local-only",
      ])
        expect(resolvePreviewMode(false, mode, search)).toBeNull();
  });

  it("uses real connections in ordinary development unless explicitly requested", () => {
    expect(resolvePreviewMode(true, "development", "")).toBeNull();
    expect(resolvePreviewMode(true, "development", "?preview=connected")).toBe(
      "connected",
    );
    expect(
      resolvePreviewMode(true, "development", "?preview=disconnected"),
    ).toBe("disconnected");
    expect(
      resolvePreviewMode(true, "development", "?preview=remote-only"),
    ).toBe("remote-only");
    expect(resolvePreviewMode(true, "development", "?preview=local-only")).toBe(
      "local-only",
    );
    expect(
      resolvePreviewMode(true, "development", "?preview=unknown"),
    ).toBeNull();
  });

  it("opens the dedicated preview in connected state and allows returning to real data", () => {
    expect(resolvePreviewMode(true, "preview", "")).toBe("connected");
    expect(resolvePreviewMode(true, "preview", "?preview=real")).toBeNull();
    expect(resolvePreviewMode(true, "test", "?preview=connected")).toBeNull();
  });
});
