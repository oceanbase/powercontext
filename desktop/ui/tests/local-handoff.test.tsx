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

import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "../src/app/App";
import { answerConfirmation } from "./confirmation";
import { opsApi } from "../src/shared/ops";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState } from "../src/generated/ipc";
vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
  },
}));
vi.mock("../src/shared/ops", () => ({
  subscribeOpsProgress: vi.fn().mockResolvedValue(() => {}),
  opsApi: {
    inspect: vi.fn(),
    references: vi.fn(),
    connect: vi.fn(),
    apply: vi.fn(),
  },
}));
const disconnected: DesktopState = {
  generation: 4,
  profiles: [],
  reports: [],
  active: null,
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};
function connected(): DesktopState {
  const fact = { value: null, error: null };
  return {
    ...disconnected,
    generation: 6,
    profiles: [
      {
        id: "local-profile",
        revision: 1,
        name: "Local Server",
        endpoint: "http://127.0.0.1:8123/",
        authentication: "unauthenticated_loopback",
        caPem: null,
        compatibility: "qualified",
        credentialState: "not_required",
      },
    ],
    active: {
      connectionId: "local-profile",
      generation: 6,
      scope: {
        scope_id: "server-returned-default",
        title: "Personal Scope",
        summary: "",
        context_references: [],
        external_references: [],
        version: 1,
      },
      report: {
        connectionId: "local-profile",
        revision: 1,
        checkedAt: 1,
        liveness: fact,
        readiness: fact,
        identity: fact,
        capabilities: fact,
        compatibilityVerified: true,
        anonymousAccess: true,
        supportedOperations: [
          "remember_memory",
          "search_memory",
          "get_artifact_revision",
        ],
      },
    },
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(desktopApi.state).mockResolvedValue(disconnected);
  vi.mocked(opsApi.references).mockResolvedValue({
    bindingAvailable: true,
    loginStartChoice: false,
    operations: [],
  });
  vi.mocked(opsApi.inspect).mockResolvedValue({
    outcome: "succeeded",
    components: {
      installationId: "owned",
      resources: "present",
      managerOwnership: "owned",
      manager: "active",
      registration: "installed",
      configuration: "valid",
      liveness: "live",
    },
  });
  vi.mocked(opsApi.connect).mockResolvedValue(connected());
});
afterEach(cleanup);
test.each(["en", "zh"] as const)(
  "explicit local handoff opens the exact returned Scope and never reactivates on reopen (%s)",
  async (language) => {
    const user = userEvent.setup();
    const view = render(<App />);
    await user.click(screen.getByRole("button", { name: "本地设置" }));
    await user.selectOptions(screen.getByLabelText("语言"), language);
    const action =
      language === "en"
        ? "Connect and open default Scope"
        : "连接并进入默认 Scope";
    const button = screen.getByRole("button", { name: action });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
    expect(opsApi.connect).not.toHaveBeenCalled();
    await user.click(button);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("button", {
        name: language === "en" ? "Confirm" : "确认",
      }),
    );
    expect(
      (await screen.findAllByText("server-returned-default")).length,
    ).toBeGreaterThan(0);
    expect(opsApi.connect).toHaveBeenCalledWith(4);
    expect(document.activeElement).toBe(
      screen.getByRole("heading", {
        level: 1,
        name: language === "en" ? "Memories" : "记忆",
      }),
    );
    expect(opsApi.apply).not.toHaveBeenCalled();
    view.unmount();
    render(<App />);
    await screen.findByRole("button", { name: "本地设置" });
    expect(opsApi.connect).toHaveBeenCalledOnce();
  },
);

test("a late handoff cannot navigate after a newer native context was received", async () => {
  const user = userEvent.setup();
  let finish!: (state: DesktopState) => void;
  vi.mocked(opsApi.connect).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<App />);
  await user.click(screen.getByRole("button", { name: "本地设置" }));
  await user.click(
    screen.getByRole("button", { name: "连接并进入默认 Scope" }),
  );
  await user.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "确认",
    }),
  );
  await user.click(screen.getByRole("button", { name: "总览" }));
  vi.mocked(desktopApi.state).mockResolvedValue({
    ...disconnected,
    generation: 20,
  });
  await user.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "刷新状态" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  finish(connected());
  await waitFor(() =>
    expect(screen.queryByText("正在更新连接状态…")).toBeNull(),
  );
  expect(screen.getByRole("heading", { level: 1, name: "总览" })).toBeTruthy();
  expect(screen.queryByText("server-returned-default")).toBeNull();
});

test("local service management remains usable while connection storage is unavailable", async () => {
  vi.mocked(desktopApi.state).mockRejectedValue({ code: "storage" });
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "本地设置" }));
  const connect = screen.getByRole("button", { name: "连接并进入默认 Scope" });
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "启动服务" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  expect((connect as HTMLButtonElement).disabled).toBe(true);
  expect(opsApi.connect).not.toHaveBeenCalled();
});

test("local handoff integrity failure is explicit and cannot activate or replay", async () => {
  vi.mocked(opsApi.connect).mockRejectedValue({ code: "integrity_failure" });
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "本地设置" }));
  await user.click(
    screen.getByRole("button", { name: "连接并进入默认 Scope" }),
  );
  await user.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "确认",
    }),
  );
  await screen.findByText(
    "可信资源校验失败。请核对发行包和固定资源，不能跳过校验执行。",
  );
  expect(screen.queryByText("server-returned-default")).toBeNull();
  expect(opsApi.apply).not.toHaveBeenCalled();
  expect(opsApi.connect).toHaveBeenCalledOnce();
});

test.each(["zh", "en"] as const)(
  "a late local handoff preserves a newer connection draft and its discard guard (%s)",
  async (language) => {
    const user = userEvent.setup();
    let finish!: (state: DesktopState) => void;
    vi.mocked(opsApi.connect).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App />);
    await user.click(screen.getByRole("button", { name: "本地设置" }));
    await user.selectOptions(screen.getByLabelText("语言"), language);
    await user.click(
      screen.getByRole("button", {
        name:
          language === "en"
            ? "Connect and open default Scope"
            : "连接并进入默认 Scope",
      }),
    );
    await answerConfirmation(user, true);
    await waitFor(() => expect(opsApi.connect).toHaveBeenCalledOnce());
    await user.click(
      screen.getByRole("button", {
        name: language === "en" ? "Connections" : "连接",
      }),
    );
    const input = screen.getByRole("textbox", {
      name: language === "en" ? "Connection name" : "连接名称",
    });
    await user.type(input, "new unsaved profile");
    await act(async () => finish(connected()));
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: language === "en" ? "Connections" : "连接",
      }),
    ).toBeTruthy();
    expect((input as HTMLInputElement).value).toBe("new unsaved profile");
    await user.click(
      screen.getByRole("button", {
        name: language === "en" ? "Overview" : "总览",
      }),
    );
    await answerConfirmation(user, false);
    expect((input as HTMLInputElement).value).toBe("new unsaved profile");
    expect(opsApi.connect).toHaveBeenCalledOnce();
  },
);

test("a late local handoff respects navigation even without a new draft", async () => {
  const user = userEvent.setup();
  let finish!: (state: DesktopState) => void;
  vi.mocked(opsApi.connect).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<App />);
  await user.click(screen.getByRole("button", { name: "本地设置" }));
  await user.click(
    screen.getByRole("button", { name: "连接并进入默认 Scope" }),
  );
  await answerConfirmation(user, true);
  await waitFor(() => expect(opsApi.connect).toHaveBeenCalledOnce());
  await user.click(screen.getByRole("button", { name: "总览" }));
  await act(async () => finish(connected()));
  expect(screen.getByRole("heading", { level: 1, name: "总览" })).toBeTruthy();
});
