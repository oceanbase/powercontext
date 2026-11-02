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
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { answerConfirmation } from "./confirmation";
import { App } from "../src/app/App";
import { Observation } from "../src/app/Observation";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState } from "../src/generated/ipc";

vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn(),
    check: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
    diagnostics: vi.fn(),
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

test("Overview remains the initial page and all original entries remain reachable alongside Observation", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(screen.getByRole("heading", { level: 1, name: "总览" })).toBeTruthy();
  expect(
    within(screen.getByRole("navigation"))
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual(["总览", "连接", "记忆", "观察"]);
  expect(
    await screen.findByRole("heading", { name: "尚未连接服务" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "连接" }));
  expect(screen.getByRole("heading", { name: "添加连接" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "记忆" }));
  expect(screen.getByLabelText("记忆内容")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "观察" }));
  expect(screen.getByRole("tablist", { name: "观察" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "设置与诊断" }));
  expect(screen.getByLabelText("语言")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "本地诊断" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "总览" }));
  expect(
    await screen.findByRole("heading", { name: "尚未连接服务" }),
  ).toBeTruthy();
});

test("Observation tabs support the keyboard and stay unavailable without starting checks", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "观察" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: "观察" }),
    ),
  );
  await user.click(screen.getByRole("tab", { name: "运行状态" }));
  await user.keyboard("{ArrowRight}");
  expect(document.activeElement).toBe(
    screen.getByRole("tab", { name: "当前 Scope 库存与用量" }),
  );
  expect(screen.getByRole("tabpanel").textContent).toContain(
    "未知值不表示为零",
  );
  await user.keyboard("{End}");
  expect(document.activeElement).toBe(
    screen.getByRole("tab", { name: "本次会话" }),
  );
  await user.keyboard("{Home}{ArrowLeft}");
  expect(document.activeElement).toBe(
    screen.getByRole("tab", { name: "本次会话" }),
  );
  expect(
    screen.getByRole("heading", { name: "此观察视图尚未可用" }),
  ).toBeTruthy();
  expect(desktopApi.check).not.toHaveBeenCalled();
  expect(desktopApi.diagnostics).not.toHaveBeenCalled();
});

test("Observation follows the existing language setting and retains its selected tab", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "观察" }));
  await user.click(screen.getByRole("tab", { name: "本次会话" }));
  await user.click(screen.getByRole("button", { name: "设置与诊断" }));
  await user.selectOptions(screen.getByLabelText("语言"), "en");
  await user.click(screen.getByRole("button", { name: "Observation" }));
  expect(
    screen
      .getByRole("tab", { name: "This session" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(
    screen.getByRole("heading", {
      name: "This observation view is not available yet",
    }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Overview" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Settings & diagnostics" }),
  ).toBeTruthy();
});

test("entering Observation respects the existing Memory draft confirmation", async () => {
  const user = userEvent.setup();
  const fact = { value: null, error: null };
  vi.mocked(desktopApi.state).mockResolvedValue({
    ...disconnected,
    generation: 1,
    active: {
      connectionId: "fixture",
      generation: 1,
      scope: {
        scope_id: "scope-a",
        title: "Scope A",
        summary: "",
        version: 1,
        context_references: [],
        external_references: [],
      },
      report: {
        connectionId: "fixture",
        revision: 1,
        checkedAt: 1,
        liveness: fact,
        readiness: fact,
        identity: fact,
        capabilities: fact,
        compatibilityVerified: true,
        anonymousAccess: true,
        supportedOperations: ["remember_memory"],
      },
    },
  });

  render(<App />);
  await user.click(screen.getByRole("button", { name: "记忆" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: "记忆" }),
    ),
  );
  await user.type(screen.getByLabelText("记忆内容"), "unsaved draft");
  await user.click(screen.getByRole("button", { name: "观察" }));
  await answerConfirmation(user, false);
  expect((screen.getByLabelText("记忆内容") as HTMLTextAreaElement).value).toBe(
    "unsaved draft",
  );
  expect(screen.queryByRole("tablist")).toBeNull();
  await user.click(screen.getByRole("button", { name: "观察" }));
  await answerConfirmation(user, true);
  expect(screen.getByRole("tablist", { name: "观察" })).toBeTruthy();
});

test("each observation panel accepts content supplied by its owning feature", () => {
  render(
    <Observation
      language="en"
      tab="inventory"
      onTabChange={vi.fn()}
      panels={{ inventory: <p>Exact Scope inventory</p> }}
    />,
  );
  expect(screen.getByRole("tabpanel").textContent).toBe(
    "Exact Scope inventory",
  );
  expect(
    screen.queryByText("This observation view is not available yet"),
  ).toBeNull();
});
