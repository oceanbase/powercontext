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

import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import type {
  ActiveView,
  OpsInspectionReply,
  HealthResponse,
  RuntimeCapabilities,
  RuntimeFact,
} from "../src/generated/ipc";
import { Runtime } from "../src/app/Runtime";
import { desktopApi } from "../src/shared/ipc";
import { opsApi } from "../src/shared/ops";

vi.mock("../src/shared/ipc", () => ({
  desktopApi: {
    observeRuntimeLiveness: vi.fn(),
    observeRuntimeCapabilities: vi.fn(),
    probeRuntimeReadiness: vi.fn(),
    cancelRuntime: vi.fn().mockResolvedValue(undefined),
    diagnostics: vi.fn(),
  },
}));
vi.mock("../src/shared/ops", () => ({ opsApi: { inspect: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const at = 1793500000;
const blank = { value: null, error: null };
const active: ActiveView = {
  connectionId: "server-a",
  generation: 1,
  scope: null,
  report: {
    connectionId: "server-a",
    revision: 1,
    checkedAt: at,
    liveness: blank,
    readiness: blank,
    identity: blank,
    capabilities: blank,
    anonymousAccess: true,
    compatibilityVerified: true,
    supportedOperations: ["get_readiness"],
  },
};
const sample = <T,>(value: T) => ({
  checkedAt: at,
  fact: { value, error: null },
});
const observations: {
  liveness: RuntimeFact<HealthResponse>;
  capabilities: RuntimeFact<RuntimeCapabilities>;
} = {
  liveness: sample({ status: "ok" }),
  capabilities: sample({
    memory_extraction: true,
    handoff_generation: false,
    search_modes: ["fts"],
    extraction: {
      configuration: "configured",
      background: {
        location: "local",
        state: "running",
        automatic_processing_enabled: true,
      },
      observation: {
        status: "observed",
        since: "2026-11-01T00:00:00Z",
        last_success_at: "2026-11-01T02:00:00Z",
        last_failure: {
          occurred_at: "2026-11-01T01:00:00Z",
          code: "worker_timeout",
          stage: "worker",
        },
      },
    },
  }),
};
const mounted = (
  selected: ActiveView | null,
  language: "en" | "zh" = "en",
  onManage = () => {},
) => (
  <Runtime
    active={selected}
    language={language}
    onManage={onManage}
    contextPending={false}
  />
);

test("refresh shows processing coverage and does not run or replace the explicit readiness result", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.observeRuntimeLiveness).mockResolvedValue(
    observations.liveness,
  );
  let finishCapabilities!: (sample: RuntimeFact<RuntimeCapabilities>) => void;
  vi.mocked(desktopApi.observeRuntimeCapabilities)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishCapabilities = resolve;
        }),
    )
    .mockResolvedValue(observations.capabilities);
  vi.mocked(desktopApi.probeRuntimeReadiness).mockResolvedValue(
    sample("degraded"),
  );
  render(mounted(active));
  expect(desktopApi.probeRuntimeReadiness).not.toHaveBeenCalled();
  const refresh = screen.getByRole("button", {
    name: "Refresh Server observations",
  });
  refresh.focus();
  await user.keyboard("{Enter}");
  expect(await screen.findByText("API process responded.")).toBeTruthy();
  expect(screen.queryByText("Last historical failure")).toBeNull();
  await act(async () => {
    finishCapabilities(observations.capabilities);
  });
  expect(await screen.findByText("Last historical failure")).toBeTruthy();
  expect(screen.getByText(/Worker timed out/)).toBeTruthy();
  expect(screen.getByText(/later success does not prove/)).toBeTruthy();
  expect(desktopApi.probeRuntimeReadiness).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Probe readiness" }));
  expect((await screen.findByText("Degraded")).getAttribute("role")).toBe(
    "status",
  );
  await user.click(refresh);
  expect(screen.getByText("Degraded")).toBeTruthy();
  expect(desktopApi.probeRuntimeReadiness).toHaveBeenCalledTimes(1);
});

test("denial leaves liveness and local inspection available; local CLI has an independent failure", async () => {
  const user = userEvent.setup();
  const onManage = vi.fn();
  vi.mocked(desktopApi.observeRuntimeLiveness).mockResolvedValue(
    observations.liveness,
  );
  vi.mocked(desktopApi.observeRuntimeCapabilities).mockResolvedValue({
    checkedAt: at,
    fact: {
      value: null,
      error: { code: "forbidden", requestId: null, dispatched: true },
    },
  });
  vi.mocked(opsApi.inspect).mockResolvedValue({
    outcome: "succeeded",
    components: {
      registration: "installed",
      manager: "inactive",
      managerOwnership: "foreign",
    },
  });
  vi.mocked(desktopApi.diagnostics).mockRejectedValue("not_found");
  render(mounted(active, "zh", onManage));
  await user.click(screen.getByRole("button", { name: "刷新 Server 观察" }));
  expect(await screen.findByText("API 进程有响应。")).toBeTruthy();
  expect(screen.getByRole("alert")).toBeTruthy();
  const local = screen.getByRole("region", { name: "此电脑" });
  await user.click(within(local).getByRole("button", { name: "检查本机安装" }));
  expect(await within(local).findByText("不属于此安装")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "检查本机服务" }));
  expect(desktopApi.diagnostics).toHaveBeenCalledWith("service");
  expect(screen.getByText("API 进程有响应。")).toBeTruthy();
  expect(screen.getByText("不属于此安装")).toBeTruthy();
  await user.click(
    within(local).getByRole("button", { name: "打开本机 Server 设置" }),
  );
  expect(onManage).toHaveBeenCalledTimes(1);
});

test("disconnect discards delayed Server and local results and still permits local inspection", async () => {
  const user = userEvent.setup();
  let completeRemote!: (reply: RuntimeFact<HealthResponse>) => void;
  let completeLocal!: (reply: OpsInspectionReply) => void;
  vi.mocked(desktopApi.observeRuntimeLiveness).mockImplementation(
    () =>
      new Promise((resolve) => {
        completeRemote = resolve;
      }),
  );
  vi.mocked(opsApi.inspect)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          completeLocal = resolve;
        }),
    )
    .mockResolvedValue({
      outcome: "succeeded",
      components: { registration: "not_installed" },
    });
  vi.mocked(desktopApi.observeRuntimeCapabilities).mockResolvedValue(
    observations.capabilities,
  );
  const view = render(mounted(active));
  await user.click(
    screen.getByRole("button", { name: "Refresh Server observations" }),
  );
  const inspect = () =>
    screen.getByRole("button", { name: "Inspect local installation" });
  await user.click(inspect());
  view.rerender(mounted(null));
  expect(desktopApi.cancelRuntime).toHaveBeenCalledWith(1);
  await act(async () => {
    completeRemote(observations.liveness);
    completeLocal({
      outcome: "succeeded",
      components: { registration: "installed" },
    });
  });
  expect(screen.queryByText("API process responded.")).toBeNull();
  expect(screen.queryByText("Registered", { exact: true })).toBeNull();
  await user.click(inspect());
  expect(await screen.findByText("Not registered")).toBeTruthy();
});

test("cancelling with the keyboard restores the initiating control and ignores a late response", async () => {
  const user = userEvent.setup();
  let complete!: (reply: RuntimeFact<HealthResponse>) => void;
  vi.mocked(desktopApi.observeRuntimeLiveness).mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  render(mounted(active));
  const refresh = screen.getByRole("button", {
    name: "Refresh Server observations",
  });
  refresh.focus();
  await user.keyboard("{Enter}{Tab}{Enter}");
  expect(document.activeElement).toBe(refresh);
  expect(desktopApi.cancelRuntime).toHaveBeenCalledWith(1);
  await act(async () => {
    complete(observations.liveness);
  });
  expect(await screen.findByText("Server request cancelled.")).toBeTruthy();
  expect(screen.queryByText("API process responded.")).toBeNull();
});
