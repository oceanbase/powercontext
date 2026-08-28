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
import { decodeCitation, encodeCitation } from "./types.js";

describe("PowerContext citations", () => {
  it("round trips exact citations", () => {
    const citation = {
      memory_ref: { family: "memory", artifact_id: "memory-1", revision: 3 },
      entry_id: "entry-1",
      entry_version_id: "entry-1-v2",
    };
    expect(decodeCitation(encodeCitation(citation))).toEqual(citation);
  });

  it("rejects model-authored arbitrary citation strings", () => {
    expect(() => decodeCitation("../MEMORY.md")).toThrow(/exact powercontext citation/u);
  });
});
