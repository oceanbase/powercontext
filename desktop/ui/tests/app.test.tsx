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

import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/app/App";
import { TooltipProvider } from "../src/components/ui/tooltip";
import type { DesktopState, WriteRecord } from "../src/generated/ipc";
import { desktopApi } from "../src/shared/ipc";
vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn().mockResolvedValue({
      generation: 0,
      profiles: [],
      reports: [],
      active: null,
      compatibilityProfiles: [],
      pendingCredentialCleanup: 0,
      lastWrite: null,
    }),
    check: vi.fn(),
    defaultScope: vi.fn(),
    selectScope: vi.fn(),
    stats: vi.fn(),
    cancelStats: vi.fn().mockResolvedValue(undefined),
    remember: vi.fn(),
    search: vi.fn(),
    atomicEntry: vi.fn(),
  },
}));
afterEach(() => {
  cleanup();
  globalThis.localStorage?.clear();
  vi.clearAllMocks();
});

function connectedMemoryState(): DesktopState {
  const profile = {
    id: "memory-service",
    revision: 1,
    name: "Memory service",
    endpoint: "https://memory.example.com",
    authentication: "bearer" as const,
    caPem: null,
    compatibility: null,
    credentialState: "stored" as const,
  };
  const noFact = { value: null, error: null };
  const report = {
    connectionId: profile.id,
    revision: profile.revision,
    checkedAt: 1,
    liveness: { value: { status: "ok" }, error: null },
    readiness: noFact,
    identity: noFact,
    capabilities: noFact,
    compatibilityVerified: true,
    anonymousAccess: false,
    supportedOperations: [
      "search_memory",
      "remember_memory",
      "get_artifact_revision",
    ],
  };
  return {
    profiles: [profile],
    reports: [report],
    generation: 1,
    active: {
      connectionId: profile.id,
      generation: 1,
      scope: {
        scope_id: "original-space",
        title: "Original space",
        summary: "",
        context_references: [],
        external_references: [],
        version: 1,
      },
      report,
    },
    compatibilityProfiles: [],
    pendingCredentialCleanup: 0,
    lastWrite: null,
  };
}

function memoryReceipt(status: WriteRecord["status"]): WriteRecord {
  return {
    operationId: "write-1",
    context: {
      connectionId: "memory-service",
      endpoint: "https://memory.example.com",
      serverId: "original-deployment",
      principal: null,
      generation: 1,
      scopeId: "original-space",
    },
    status,
    error: null,
    references: [],
  };
}

async function enterMemory(
  user: ReturnType<typeof userEvent.setup>,
  text: string,
) {
  await user.click(
    await screen.findByRole("button", { name: "Record memory" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Memory content" }),
    text,
  );
  return screen.getByRole("button", {
    name: "Save to current space",
  }) as HTMLButtonElement;
}

test("removes private Memory content when search invalidates the native context", async () => {
  globalThis.localStorage.setItem("desktop-language", "en");
  const connected = connectedMemoryState();
  const reference = {
    family: "atomic-memory",
    artifact_id: "private-1",
    revision: 1,
  };
  vi.mocked(desktopApi.state)
    .mockResolvedValueOnce(connected)
    .mockResolvedValueOnce({ ...connected, generation: 2, active: null });
  vi.mocked(desktopApi.remember).mockResolvedValueOnce({
    record: { ...memoryReceipt("succeeded"), references: [reference] },
    result: {
      changed: true,
      records: [
        {
          artifact: reference,
          kind: "note",
          text: "Cached private memory",
          state: "active",
          state_version: 1,
          merged_into_id: null,
        },
      ],
    },
  });
  vi.mocked(desktopApi.atomicEntry).mockResolvedValueOnce({
    artifact: reference,
    kind: "note",
    text: "Exact private detail",
    source_refs: [],
  });
  vi.mocked(desktopApi.search).mockRejectedValueOnce({ code: "stale_context" });
  const user = userEvent.setup();
  render(
    <TooltipProvider>
      <App />
    </TooltipProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Memories" }));
  await user.click(await enterMemory(user, "Private input"));
  expect(await screen.findByText("Exact private detail")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Cached private memory" }),
  ).toBeTruthy();

  const search = screen.getByRole("textbox", { name: "Search memories" });
  await user.type(search, "identity change");
  expect(
    await screen.findByRole("heading", { name: "No accessible memory spaces" }),
  ).toBeTruthy();
  expect(screen.queryByText("Cached private memory")).toBeNull();
  expect(screen.queryByText("Exact private detail")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Search memories" })).toBeNull();
});

test("requires acknowledgement before resubmitting an uncertain save after navigation", async () => {
  globalThis.localStorage.setItem("desktop-language", "en");
  const connected = connectedMemoryState();
  const uncertain = memoryReceipt("unknown");
  vi.mocked(desktopApi.state)
    .mockResolvedValueOnce(connected)
    .mockResolvedValueOnce({ ...connected, lastWrite: uncertain });
  vi.mocked(desktopApi.remember).mockResolvedValueOnce({
    record: uncertain,
    result: null,
  });
  const user = userEvent.setup();
  render(
    <TooltipProvider>
      <App />
    </TooltipProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Memories" }));
  await user.click(await enterMemory(user, "An uncertain save"));
  expect(
    await screen.findByText(/review the original submission/),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.click(screen.getByRole("button", { name: "Home" }));
  await user.click(screen.getByRole("button", { name: "Memories" }));

  const recovered = screen.getByRole("textbox", {
    name: "Recover a memory submission",
  }) as HTMLTextAreaElement;
  expect(recovered.value).toBe("An uncertain save");
  expect(recovered.readOnly).toBe(true);
  expect(
    screen.getByText(
      /https:\/\/memory\.example\.com.*Original space \(original-space\)/,
    ),
  ).toBeTruthy();
  expect(screen.getByText("original-deployment")).toBeTruthy();
  expect((await enterMemory(user, "An uncertain save")).disabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.click(
    screen.getByRole("button", { name: "I checked. Allow another submission" }),
  );
  expect((await enterMemory(user, "An uncertain save")).disabled).toBe(false);
  expect(desktopApi.remember).toHaveBeenCalledTimes(1);
});

test("reactivates a saved connection from Services after losing the active context", async () => {
  globalThis.localStorage.setItem("desktop-language", "en");
  const connected = connectedMemoryState();
  const active = connected.active!;
  const disconnected = { ...connected, generation: 2, active: null };
  vi.mocked(desktopApi.state).mockResolvedValueOnce(disconnected);
  vi.mocked(desktopApi.check).mockResolvedValueOnce({
    ...connected,
    generation: 3,
    active: { ...active, generation: 3, scope: null },
  });
  vi.mocked(desktopApi.defaultScope).mockResolvedValueOnce(active.scope!);
  vi.mocked(desktopApi.selectScope).mockResolvedValueOnce({
    ...connected,
    generation: 4,
    active: { ...active, generation: 4 },
  });
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "Services & Agents" }));
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Use this connection" }),
  );

  expect(
    await screen.findByRole("heading", { name: "Space overview" }),
  ).toBeTruthy();
  expect(screen.getByText("Original space")).toBeTruthy();
  expect(desktopApi.check).toHaveBeenCalledWith(active.connectionId, true);
  expect(desktopApi.selectScope).toHaveBeenCalledWith(
    3,
    active.scope!.scope_id,
  );
});

test("restores a saved service directly into its default space", async () => {
  const profile = {
    id: "saved-service",
    revision: 1,
    name: "已保存服务",
    endpoint: "https://context.example.com",
    authentication: "bearer" as const,
    caPem: null,
    compatibility: null,
    credentialState: "stored" as const,
  };
  const report = {
    connectionId: profile.id,
    revision: 1,
    checkedAt: 1,
    liveness: { value: { status: "ok" as const }, error: null },
    readiness: {
      value: { status: "ready" as const, checks: {} },
      error: null,
    },
    identity: { value: null, error: null },
    capabilities: { value: null, error: null },
    compatibilityVerified: true,
    anonymousAccess: false,
    supportedOperations: [],
  };
  const saved = {
    generation: 0,
    profiles: [profile],
    reports: [],
    active: null,
    compatibilityProfiles: [],
    pendingCredentialCleanup: 0,
    lastWrite: null,
  };
  const checked = {
    ...saved,
    generation: 1,
    reports: [report],
    active: { connectionId: profile.id, generation: 1, report, scope: null },
  };
  const scope = {
    scope_id: "default-space",
    title: "默认空间",
    summary: "",
    parent_scope_id: null,
    context_references: [],
    external_references: [],
    version: 1,
  };
  const connected = { ...checked, active: { ...checked.active!, scope } };
  vi.mocked(desktopApi.state).mockResolvedValueOnce(saved);
  vi.mocked(desktopApi.check).mockResolvedValueOnce(checked);
  vi.mocked(desktopApi.defaultScope).mockResolvedValueOnce(scope);
  vi.mocked(desktopApi.selectScope).mockResolvedValueOnce(connected);
  globalThis.localStorage.setItem("active-connection-id", profile.id);

  render(<App />);

  expect(await screen.findByRole("heading", { name: "空间概览" })).toBeTruthy();
  expect(desktopApi.check).toHaveBeenCalledWith(profile.id, true);
  expect(desktopApi.defaultScope).toHaveBeenCalledWith(1);
  expect(desktopApi.selectScope).toHaveBeenCalledWith(1, scope.scope_id);
  expect(screen.queryByText("开始使用 PowerContext")).toBeNull();
});
test("first run starts with a simple service decision while disconnected features remain unavailable", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(
    await screen.findByRole("heading", {
      name: "你已经有可用的 PowerContext 服务了吗?",
    }),
  ).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
  const choices = screen.getAllByRole("button", {
    name: /没有服务|已有服务/,
  });
  expect(choices).toHaveLength(2);
  expect(choices[0].textContent).toContain("没有服务");
  expect(choices[1].textContent).toContain("已有服务");
  await user.click(screen.getByRole("button", { name: /没有服务/ }));
  expect(
    screen.getByRole("dialog", { name: "安装 PowerContext 服务" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "关闭弹窗" }));
  await user.click(screen.getByRole("button", { name: /已有服务/ }));
  expect(screen.getByRole("heading", { name: "连接服务" })).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "确认并连接" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  await user.click(screen.getByRole("button", { name: "确认并连接" }));
  expect(screen.getByText("请输入连接名称")).toBeTruthy();
  expect(screen.queryByText("请输入 Server 地址")).toBeNull();
  expect(screen.getByText("请输入凭据")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect((screen.getByLabelText("Server 地址") as HTMLInputElement).value).toBe(
    "http://127.0.0.1:8000",
  );
  await user.type(screen.getByLabelText("连接名称"), "本机服务");
  await user.click(screen.getByRole("button", { name: "确认并连接" }));
  expect(screen.queryByText("请输入 Server 地址")).toBeNull();
  expect(screen.getByText("请输入凭据")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "取消" }));
  await user.click(screen.getByRole("button", { name: "记忆" }));
  expect(
    screen.getByRole("heading", { name: "暂无可访问的记忆空间" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "安装服务" }));
  expect(
    screen.getByRole("dialog", { name: "安装 PowerContext 服务" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "关闭弹窗" }));
  expect(
    screen.getByRole("heading", { name: "暂无可访问的记忆空间" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "连接服务" }));
  expect(screen.getByRole("heading", { name: "连接服务" })).toBeTruthy();
});
test("appearance and language work while unavailable system settings stay disabled", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(screen.queryByRole("button", { name: "Agent" })).toBeNull();
  expect(screen.queryByText("服务待办")).toBeNull();
  await user.click(screen.getByRole("button", { name: "设置" }));
  expect(
    (screen.getByRole("switch", { name: "系统通知" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (
      screen.getByRole("switch", {
        name: "登录时打开 Desktop",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.queryByText("检查更新")).toBeNull();
  expect(screen.queryByText("关于 PowerContext")).toBeNull();
  expect(screen.getByRole("combobox", { name: "语言" })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "外观" })).toBeTruthy();
  await user.click(screen.getByRole("switch", { name: "系统通知" }));
  await user.click(screen.getByRole("switch", { name: "登录时打开 Desktop" }));
  await user.selectOptions(
    screen.getByRole("combobox", { name: "外观" }),
    "dark",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "语言" }),
    "en",
  );
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(globalThis.localStorage.getItem("desktop-language")).toBe("en");
  expect(globalThis.localStorage.getItem("desktop-theme")).toBe("dark");
  expect(globalThis.localStorage.getItem("desktop-notifications-enabled")).toBe(
    "true",
  );
  expect(globalThis.localStorage.getItem("desktop-launch-at-login")).toBe(
    "true",
  );
  await user.click(screen.getByRole("button", { name: "Memories" }));
  expect(
    screen.getByRole("heading", { name: "No accessible memory spaces" }),
  ).toBeTruthy();
});

test("restores the complete desktop preference model on a new app mount", async () => {
  globalThis.localStorage.setItem("desktop-language", "en");
  globalThis.localStorage.setItem("desktop-theme", "dark");
  globalThis.localStorage.setItem("desktop-notifications-enabled", "false");
  globalThis.localStorage.setItem("desktop-launch-at-login", "false");
  const user = userEvent.setup();

  render(<App />);

  expect(document.documentElement.lang).toBe("en");
  expect(document.documentElement.dataset.theme).toBe("dark");
  await user.click(screen.getByRole("button", { name: "Settings" }));
  expect(
    (screen.getByRole("combobox", { name: "Language" }) as HTMLSelectElement)
      .value,
  ).toBe("en");
  expect(
    (screen.getByRole("combobox", { name: "Theme" }) as HTMLSelectElement)
      .value,
  ).toBe("dark");
  expect(
    screen
      .getByRole("switch", { name: "System notifications" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(
    screen
      .getByRole("switch", { name: "Open Desktop at login" })
      .getAttribute("aria-checked"),
  ).toBe("false");
});
