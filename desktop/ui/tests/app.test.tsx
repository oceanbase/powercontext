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
  },
}));
afterEach(() => {
  cleanup();
  globalThis.localStorage?.clear();
  vi.clearAllMocks();
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

  render(<App />);

  expect(await screen.findByRole("heading", { name: "空间概览" })).toBeTruthy();
  expect(desktopApi.check).toHaveBeenCalledWith(profile.id, true);
  expect(desktopApi.defaultScope).toHaveBeenCalledWith(1);
  expect(desktopApi.selectScope).toHaveBeenCalledWith(1, scope.scope_id);
  expect(screen.queryByText("开始使用 PowerContext")).toBeNull();
});
test("frameless shell exposes draggable regions without swallowing window controls", () => {
  const { container } = render(<App />);
  const regions = container.querySelectorAll("[data-tauri-drag-region]");
  expect(regions.length).toBeGreaterThanOrEqual(2);
  expect(container.querySelector(".window-drag-handle")).toBeTruthy();
  expect(
    container.querySelector('.brand[data-tauri-drag-region="deep"]'),
  ).toBeTruthy();
  expect(
    container.querySelector(".topbar-spacer[data-tauri-drag-region]"),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "关闭窗口" })
      .hasAttribute("data-tauri-drag-region"),
  ).toBe(false);
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
test("navigation and shadcn settings controls remain usable without the native host", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(screen.queryByRole("button", { name: "Agent" })).toBeNull();
  expect(screen.queryByText("服务待办")).toBeNull();
  await user.click(screen.getByRole("button", { name: "设置" }));
  expect(
    (
      screen.getByRole("switch", { name: "系统通知" }) as HTMLButtonElement
    ).getAttribute("data-state"),
  ).toBe("checked");
  expect(
    (
      screen.getByRole("switch", {
        name: "登录时打开 Desktop",
      }) as HTMLButtonElement
    ).getAttribute("data-state"),
  ).toBe("checked");
  expect(screen.getByText(/不会启动 PowerContext 服务/)).toBeTruthy();
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
    "false",
  );
  expect(globalThis.localStorage.getItem("desktop-launch-at-login")).toBe(
    "false",
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
      .getAttribute("data-state"),
  ).toBe("unchecked");
  expect(
    screen
      .getByRole("switch", { name: "Open Desktop at login" })
      .getAttribute("data-state"),
  ).toBe("unchecked");
});
