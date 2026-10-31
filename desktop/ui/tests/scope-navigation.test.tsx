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
  act,
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { answerConfirmation } from "./confirmation";
import { App } from "../src/app/App";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState } from "../src/generated/ipc";

vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn(),
    scopes: vi.fn(),
    selectScope: vi.fn(),
    check: vi.fn(),
    save: vi.fn(),
    remember: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
    cancelScopes: vi.fn().mockResolvedValue(undefined),
  },
}));
const profileA = {
  id: "a",
  revision: 1,
  name: "First",
  endpoint: "http://127.0.0.1:8000",
  authentication: "unauthenticated_loopback" as const,
  caPem: null,
  compatibility: null,
  credentialState: "not_required" as const,
};
const profileB = {
  ...profileA,
  id: "b",
  name: "Second",
  endpoint: "http://127.0.0.1:9000",
};
const fact = { value: null, error: null };
const report = {
  connectionId: "a",
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
    "list_scopes",
    "get_scope",
  ],
};
const scopeA = {
  scope_id: "scope-a",
  title: "Scope A",
  summary: "",
  version: 1,
  context_references: [],
  external_references: [],
};
const scopeB = { ...scopeA, scope_id: "scope-b", title: "Scope B" };
const state: DesktopState = {
  generation: 1,
  profiles: [profileA, profileB],
  reports: [report],
  active: { connectionId: "a", generation: 1, scope: scopeA, report },
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};
const selected: DesktopState = {
  ...state,
  generation: 2,
  active: { ...state.active!, generation: 2, scope: scopeB },
};
beforeEach(() => {
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  vi.mocked(desktopApi.scopes).mockResolvedValue({
    items: [scopeB],
    next_cursor: null,
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

async function beginScopeSelection(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "记忆" }));
  await user.click(
    await screen.findByRole("button", { name: /Scope A scope-a/ }),
  );
  await user.click(screen.getByRole("button", { name: "查找范围" }));
  await user.click(screen.getByRole("button", { name: "Scope B" }));
}
async function chooseProfile(
  user: ReturnType<typeof userEvent.setup>,
  name: "First" | "Second",
) {
  await user.click(screen.getByRole("button", { name: /当前连接:/ }));
  await user.click(screen.getByRole("menuitem", { name: new RegExp(name) }));
}

test("global Scope selection stays on the current page and restores selector focus", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.selectScope).mockResolvedValueOnce(selected);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "观察" }));
  await user.click(screen.getByRole("button", { name: /Scope A scope-a/ }));
  await user.click(screen.getByRole("button", { name: "查找范围" }));
  await user.click(screen.getByRole("button", { name: "Scope B" }));
  expect(screen.getByRole("heading", { level: 1, name: "观察" })).toBeTruthy();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: /Scope B scope-b/ }),
  );
});

test("global Scope confirmation preserves a cancelled connection draft and discards it only after success", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.selectScope).mockResolvedValueOnce(selected);
  render(<App />);
  await screen.findByRole("button", { name: /当前连接: First/ });
  await chooseProfile(user, "Second");
  await user.type(screen.getByLabelText("连接名称"), " draft");
  await user.click(screen.getByRole("button", { name: /Scope A scope-a/ }));
  await user.click(screen.getByRole("button", { name: "查找范围" }));
  await user.click(screen.getByRole("button", { name: "Scope B" }));
  await answerConfirmation(user, false);
  expect((screen.getByLabelText("连接名称") as HTMLInputElement).value).toBe(
    "Second draft",
  );
  expect(desktopApi.selectScope).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Scope B" }));
  await answerConfirmation(user, true);
  expect(screen.queryByRole("dialog")).toBeNull();
  await waitFor(() =>
    expect((screen.getByLabelText("连接名称") as HTMLInputElement).value).toBe(
      "Second",
    ),
  );
  expect(desktopApi.save).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "观察" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test.each(["Escape", "close button", "backdrop"])(
  "pending Scope selection protects Memory after closing with %s",
  async (close) => {
    const user = userEvent.setup();
    let finish!: (next: DesktopState) => void;
    vi.mocked(desktopApi.selectScope).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App />);
    await beginScopeSelection(user);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("textbox").matches(":disabled")).toBe(true);
    if (close === "Escape") await user.keyboard("{Escape}");
    else if (close === "close button")
      await user.click(within(dialog).getByRole("button", { name: "关闭" }));
    else await user.click(document.querySelector(".modal-backdrop")!);
    expect(screen.queryByRole("dialog")).toBeNull();
    const input = screen.getByRole("textbox", { name: "记忆内容" });
    expect(input.matches(":disabled")).toBe(true);
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: "记忆" }),
    );
    expect(
      screen.getByRole("heading", { name: "正在核对并选择精确 Scope…" }),
    ).toBeTruthy();
    await user.type(input, "must not enter the old Scope");
    expect((input as HTMLTextAreaElement).value).toBe("");
    expect(
      screen
        .getByRole("button", { name: /Scope A scope-a/ })
        .matches(":disabled"),
    ).toBe(true);
    await act(async () => {
      finish(selected);
    });
    expect(
      screen.queryByRole("heading", { name: "正在核对并选择精确 Scope…" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: /Scope B scope-b/ }),
    ).toBeTruthy();
    const current = screen.getByRole("textbox", { name: "记忆内容" });
    expect(current.matches(":enabled")).toBe(true);
    await user.type(current, "draft for Scope B");

    await user.click(screen.getByRole("button", { name: "观察" }));
    await answerConfirmation(user, false);
    expect((current as HTMLTextAreaElement).value).toBe("draft for Scope B");
    expect(desktopApi.selectScope).toHaveBeenCalledTimes(1);
    expect(desktopApi.remember).not.toHaveBeenCalled();
  },
);

test("a Scope failure after closing reports the error and releases the old-context pause", async () => {
  const user = userEvent.setup();
  let fail!: (error: unknown) => void;
  vi.mocked(desktopApi.selectScope).mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  render(<App />);
  await beginScopeSelection(user);
  await user.keyboard("{Escape}");
  await act(async () => {
    fail({ code: "forbidden" });
  });
  expect(screen.getByRole("alert").textContent).toContain(
    "当前身份无权执行此操作",
  );
  expect(
    screen.getByRole("textbox", { name: "记忆内容" }).matches(":enabled"),
  ).toBe(true);
  expect(screen.getByRole("button", { name: /Scope A scope-a/ })).toBeTruthy();
});

test("a late Scope result cannot dismiss a connection editor draft created after closing the dialog", async () => {
  const user = userEvent.setup();
  let finish!: (next: DesktopState) => void;
  vi.mocked(desktopApi.selectScope).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<App />);
  await beginScopeSelection(user);
  await user.keyboard("{Escape}");
  await chooseProfile(user, "Second");
  const input = screen.getByRole("textbox", { name: "连接名称" });
  await user.type(input, " new draft");
  await act(async () => {
    finish(selected);
  });

  await user.click(screen.getByRole("button", { name: "观察" }));
  await answerConfirmation(user, false);
  expect((input as HTMLInputElement).value).toBe("Second new draft");
});

test("the global menu opens the requested editor while preserving the active connection", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("button", { name: /当前连接: First/ });
  await chooseProfile(user, "Second");
  await waitFor(() =>
    expect(
      (screen.getByRole("textbox", { name: "连接名称" }) as HTMLInputElement)
        .value,
    ).toBe("Second"),
  );
  expect(
    (screen.getByRole("textbox", { name: "Server 地址" }) as HTMLInputElement)
      .value,
  ).toBe(profileB.endpoint);
  expect(screen.getByRole("button", { name: /当前连接: First/ })).toBeTruthy();
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { level: 1, name: "连接" }),
  );
  expect(desktopApi.check).not.toHaveBeenCalled();
  expect(desktopApi.save).not.toHaveBeenCalled();
});

test("same-page external profile navigation confirms drafts and supports repeated selection of the same ID", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("button", { name: /当前连接: First/ });
  await chooseProfile(user, "First");
  await user.type(screen.getByRole("textbox", { name: "连接名称" }), " draft");

  await chooseProfile(user, "Second");
  await answerConfirmation(user, false);
  expect(
    (screen.getByRole("textbox", { name: "连接名称" }) as HTMLInputElement)
      .value,
  ).toBe("First draft");
  await chooseProfile(user, "Second");
  await answerConfirmation(user, true);
  await user.type(screen.getByRole("textbox", { name: "连接名称" }), " draft");
  await chooseProfile(user, "Second");
  await answerConfirmation(user, false);
  expect(
    (screen.getByRole("textbox", { name: "连接名称" }) as HTMLInputElement)
      .value,
  ).toBe("Second draft");
  await chooseProfile(user, "Second");
  await answerConfirmation(user, true);
  await waitFor(() =>
    expect(
      (screen.getByRole("textbox", { name: "连接名称" }) as HTMLInputElement)
        .value,
    ).toBe("Second"),
  );
  expect(desktopApi.check).not.toHaveBeenCalled();
});

test("a cancelled cross-page profile navigation preserves the Memory draft", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "记忆" }));
  await screen.findByRole("button", { name: /Scope A scope-a/ });
  const input = screen.getByRole("textbox", { name: "记忆内容" });
  await user.type(input, "Memory draft");

  await chooseProfile(user, "Second");
  await answerConfirmation(user, false);
  expect((input as HTMLTextAreaElement).value).toBe("Memory draft");
  await chooseProfile(user, "Second");
  await answerConfirmation(user, true);
  await waitFor(() =>
    expect(
      (screen.getByRole("textbox", { name: "连接名称" }) as HTMLInputElement)
        .value,
    ).toBe("Second"),
  );
  expect(desktopApi.check).not.toHaveBeenCalled();
});

test("a late save from a replaced profile editor preserves the requested editor and its draft guard", async () => {
  const user = userEvent.setup();
  let finish!: (next: DesktopState) => void;
  vi.mocked(desktopApi.save).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<App />);
  await screen.findByRole("button", { name: /当前连接: First/ });
  await chooseProfile(user, "First");
  await user.type(screen.getByRole("textbox", { name: "连接名称" }), " saved");
  await user.click(screen.getByRole("button", { name: "保存配置" }));

  await chooseProfile(user, "Second");
  await answerConfirmation(user, true);
  const input = screen.getByRole("textbox", { name: "连接名称" });
  await user.type(input, " pending draft");
  await act(async () => {
    finish({
      ...state,
      generation: 2,
      profiles: [{ ...profileA, revision: 2, name: "First saved" }, profileB],
    });
  });
  await user.click(screen.getByRole("button", { name: "观察" }));
  await answerConfirmation(user, false);
  expect((input as HTMLInputElement).value).toBe("Second pending draft");
});

test.each([true, false])(
  "reopening the saving profile preserves a newer draft (active cleared: %s)",
  async (clearActive) => {
    const user = userEvent.setup();
    let finish!: (next: DesktopState) => void;
    vi.mocked(desktopApi.save).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App />);
    await screen.findByRole("button", { name: /当前连接: First/ });
    await chooseProfile(user, "First");
    await user.type(
      screen.getByRole("textbox", { name: "连接名称" }),
      " saved",
    );
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    await chooseProfile(user, "First");
    await answerConfirmation(user, true);
    const input = screen.getByRole("textbox", { name: "连接名称" });
    await user.type(input, " new draft");
    await act(async () => {
      finish({
        ...state,
        generation: 2,
        profiles: [{ ...profileA, revision: 2, name: "First saved" }, profileB],
        active: clearActive ? null : state.active,
      });
    });
    expect((input as HTMLInputElement).value).toBe("First new draft");
    await user.click(screen.getByRole("button", { name: "观察" }));
    await answerConfirmation(user, false);
    expect((input as HTMLInputElement).value).toBe("First new draft");
    expect(desktopApi.save).toHaveBeenCalledTimes(1);
    // Explicitly abandoning the new draft loads the saved revision.
    await user.click(screen.getByRole("button", { name: "取消编辑" }));
    await answerConfirmation(user, true);
    expect((input as HTMLInputElement).value).toBe("First saved");
  },
);

test("cancelling Scope selection keeps the current draft and never starts a context transition", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "记忆" }));
  await screen.findByRole("button", { name: /Scope A scope-a/ });
  const input = screen.getByRole("textbox", { name: "记忆内容" });
  await user.type(input, "keep the original Scope draft");

  await user.click(screen.getByRole("button", { name: /Scope A scope-a/ }));
  await user.click(screen.getByRole("button", { name: "查找范围" }));
  await user.click(screen.getByRole("button", { name: "Scope B" }));
  await answerConfirmation(user, false);
  expect(desktopApi.selectScope).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("heading", { name: "正在核对并选择精确 Scope…" }),
  ).toBeNull();
  await user.keyboard("{Escape}");
  expect(input.matches(":enabled")).toBe(true);
  expect((input as HTMLTextAreaElement).value).toBe(
    "keep the original Scope draft",
  );
});
