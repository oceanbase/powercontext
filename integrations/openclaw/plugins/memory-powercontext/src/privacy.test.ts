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
import { isEligiblePrivateSession } from "./privacy.js";

describe("PowerContext privacy gate", () => {
  it("allows private sessions and rejects group/channel sessions", () => {
    expect(isEligiblePrivateSession("agent:main:telegram:direct:user-1")).toBe(true);
    expect(isEligiblePrivateSession("agent:main:discord:group:room-1")).toBe(false);
    expect(isEligiblePrivateSession("agent:main:slack:channel:room-1")).toBe(false);
  });

  it("rejects incognito sessions", () => {
    expect(isEligiblePrivateSession("agent:main:internal-session-effects:incognito-1")).toBe(false);
  });
});
