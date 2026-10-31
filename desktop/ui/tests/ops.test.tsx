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

import { describe, expect, it, vi } from "vitest";
import { opsApi, subscribeOpsProgress } from "../src/shared/ops";
import type { OpsProgress } from "../src/generated/ipc";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/webviewWindow", () => ({
  getCurrentWebviewWindow: () => ({ listen: mocks.listen }),
}));

describe("constrained Ops client", () => {
  it("uses only fixed commands and typed references", async () => {
    mocks.invoke.mockResolvedValue({ outcome: "unknown" });
    await opsApi.references();
    await opsApi.inspect();
    await opsApi.configValidate();
    await opsApi.preflight({ operation: "stop", options: {} });
    const approval = { approvalId: "native-approval", planId: "native-plan" };
    await opsApi.apply(approval);
    await opsApi.operationStatus({ operationId: "retained-operation" });
    await opsApi.cancel({ operationId: "retained-operation", attempt: 1 });
    await opsApi.resume(approval);
    expect(mocks.invoke.mock.calls).toEqual([
      ["ops_references"],
      ["ops_inspect"],
      ["ops_config_validate"],
      ["ops_preflight", { input: { operation: "stop", options: {} } }],
      ["ops_apply", { input: approval }],
      [
        "ops_operation_status",
        { input: { operationId: "retained-operation" } },
      ],
      [
        "ops_cancel",
        { input: { operationId: "retained-operation", attempt: 1 } },
      ],
      ["ops_resume", { input: approval }],
    ]);
  });
  it("subscribes to the current window and exposes cleanup", async () => {
    const received = vi.fn();
    const stop = vi.fn();
    mocks.listen.mockResolvedValue(stop);
    const cleanup = await subscribeOpsProgress(received);
    const event: OpsProgress = {
      requestId: "request",
      operationId: "operation",
      attempt: 1,
      sequence: 1,
      phase: "service",
      accepted: false,
    };
    expect(mocks.listen.mock.calls.at(-1)?.[0]).toBe("ops-progress");
    mocks.listen.mock.calls.at(-1)?.[1]({ payload: event });
    expect(received).toHaveBeenCalledWith(event);
    cleanup();
    expect(stop).toHaveBeenCalledOnce();
  });
});
