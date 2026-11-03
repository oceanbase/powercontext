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

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { answerConfirmation } from "./confirmation";
import { App } from "../src/app/App";
import { desktopApi } from "../src/shared/ipc";
import { messages } from "../src/app/messages";
import type { DesktopState } from "../src/generated/ipc";

vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn(),
    check: vi.fn(),
    save: vi.fn(),
    diagnostics: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
  },
}));
const disconnected: DesktopState = {
  generation: 0,
  profiles: [],
  reports: [],
  active: null,
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};
beforeEach(() => {
  vi.mocked(desktopApi.state).mockResolvedValue(disconnected);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

test.each(["zh", "en"] as const)(
  "disconnected Memory provides local and existing-Server paths without changing Overview (%s)",
  async (language) => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: messages.zh.noActiveTitle });
    expect(
      screen.getByRole("heading", { level: 1, name: "总览" }),
    ).toBeTruthy();
    // Overview retains its original content; the two-path entry belongs to Memory.
    expect(
      screen.queryByRole("button", { name: messages.zh.setUpLocally }),
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "设置与诊断" }));
    await user.selectOptions(screen.getByLabelText("语言"), language);
    const t = messages[language];
    await user.click(screen.getByRole("button", { name: t.memories }));
    expect(screen.getByRole("button", { name: t.setUpLocally })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: t.connectExisting }),
    ).toBeTruthy();
    await user.click(screen.getByRole("button", { name: t.connectExisting }));
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: t.connections }),
    );
    expect(
      screen
        .getByRole("textbox", {
          name: language === "zh" ? "连接名称" : "Connection name",
        })
        .matches(":enabled"),
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: t.memories }));
    await user.click(screen.getByRole("button", { name: t.setUpLocally }));
    const region = screen.getByRole("region", { name: "Local Server" });
    expect(document.activeElement).toBe(
      within(region).getByRole("heading", { name: "Local Server", level: 2 }),
    );
    expect(
      within(region).getByRole("heading", { name: t.localServerUnavailable }),
    ).toBeTruthy();
    expect(within(region).getByText(t.localServerUnavailableHint)).toBeTruthy();
    expect(within(region).getAllByRole("listitem")).toHaveLength(3);
    expect(within(region).getAllByRole("button")).toHaveLength(1);
    expect(
      screen
        .getByRole("button", { name: t.settings })
        .closest(".sidebar-bottom"),
    ).toBeTruthy();
    await user.click(
      within(region).getByRole("button", { name: t.connectExisting }),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: t.connections }),
    ).toBeTruthy();
    expect(desktopApi.check).not.toHaveBeenCalled();
    expect(desktopApi.save).not.toHaveBeenCalled();
    expect(desktopApi.diagnostics).not.toHaveBeenCalled();
  },
);

test.each(["loading", "error"])(
  "Local Server guidance remains reachable when native state is %s",
  async (mode) => {
    const user = userEvent.setup();
    if (mode === "loading")
      vi.mocked(desktopApi.state).mockImplementationOnce(
        () => new Promise(() => {}),
      );
    else vi.mocked(desktopApi.state).mockRejectedValueOnce({ code: "storage" });
    render(<App />);
    await user.click(screen.getByRole("button", { name: "设置与诊断" }));
    const region = screen.getByRole("region", { name: "Local Server" });
    expect(
      within(region).getByText(messages.zh.localServerUnavailableHint),
    ).toBeTruthy();
    expect(
      within(region)
        .getByRole("button", { name: "连接已有 Server" })
        .matches(":enabled"),
    ).toBe(true);
    expect(screen.getByRole("heading", { name: "使用帮助" })).toBeTruthy();
    expect(desktopApi.check).not.toHaveBeenCalled();
    expect(desktopApi.diagnostics).not.toHaveBeenCalled();
  },
);

test("navigating to Settings retains the existing editor discard guard", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "连接" }));
  const input = screen.getByRole("textbox", { name: "连接名称" });
  await user.type(input, "unsaved connection");

  await user.click(screen.getByRole("button", { name: "设置与诊断" }));
  await answerConfirmation(user, false);
  expect(screen.queryByRole("region", { name: "Local Server" })).toBeNull();
  expect((input as HTMLInputElement).value).toBe("unsaved connection");
});

test("an active connection without a Scope still offers exact Scope selection", async () => {
  const user = userEvent.setup();
  const fact = { value: null, error: null };
  vi.mocked(desktopApi.state).mockResolvedValue({
    ...disconnected,
    generation: 1,
    active: {
      connectionId: "active",
      generation: 1,
      scope: null,
      report: {
        connectionId: "active",
        revision: 1,
        checkedAt: 1,
        liveness: fact,
        readiness: fact,
        identity: fact,
        capabilities: fact,
        compatibilityVerified: true,
        anonymousAccess: true,
        supportedOperations: [],
      },
    },
  });
  render(<App />);
  await screen.findByRole("button", { name: /当前连接: 连接已激活/ });
  await user.click(screen.getByRole("button", { name: "记忆" }));
  expect(
    screen.getByRole("button", { name: messages.zh.chooseScopeTitle }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "本地设置" })).toBeNull();
  expect(screen.queryByRole("button", { name: "连接已有 Server" })).toBeNull();
});
