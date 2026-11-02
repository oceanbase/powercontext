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
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { answerConfirmation } from "./confirmation";
import { App } from "../src/app/App";
import { Connections } from "../src/app/Connections";
import { messages } from "../src/app/messages";
import { connectionMessages } from "../src/app/connection-messages";
import { contractSha256 } from "../src/generated/operations";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState } from "../src/generated/ipc";
vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn(),
    save: vi.fn(),
    check: vi.fn(),
    remember: vi.fn(),
    search: vi.fn(),
    scopes: vi.fn(),
    selectScope: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
    cancelScopes: vi.fn().mockResolvedValue(undefined),
  },
}));
const fact = { value: null, error: null };
const profile = {
  id: "a",
  name: "Original",
  revision: 1,
  endpoint: "http://127.0.0.1:8000",
  authentication: "unauthenticated_loopback" as const,
  caPem: null,
  compatibility: null,
  credentialState: "not_required" as const,
};
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
const scope = {
  scope_id: "scope-a",
  title: "Scope A",
  summary: "",
  version: 1,
  context_references: [],
  external_references: [],
};
const state: DesktopState = {
  generation: 1,
  profiles: [profile],
  reports: [report],
  active: { connectionId: "a", generation: 1, report, scope },
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};

test.each(["zh", "en"] as const)(
  "%s first use saves, checks, activates and selects an exact Scope with keyboard navigation",
  async (language) => {
    const user = userEvent.setup();
    const t = messages[language];
    const ct = connectionMessages[language];
    const qualification = {
      id: "synthetic-journey",
      serverCommit: "synthetic",
      artifactSha256: "synthetic",
      contractSha256,
      operations: report.supportedOperations,
      evidence: "UI fixture; not Server qualification",
    };
    const disconnected = {
      ...state,
      generation: 0,
      profiles: [],
      reports: [],
      active: null,
      compatibilityProfiles: [qualification],
    };
    const saved = {
      ...disconnected,
      generation: 1,
      profiles: [{ ...profile, compatibility: qualification.id }],
    };
    const checked = { ...saved, reports: [report] };
    const activated = {
      ...checked,
      generation: 2,
      active: { connectionId: "a", generation: 2, report, scope: null },
    };
    vi.mocked(desktopApi.state).mockResolvedValue(disconnected);
    vi.mocked(desktopApi.save).mockResolvedValueOnce(saved);
    vi.mocked(desktopApi.check)
      .mockResolvedValueOnce(checked)
      .mockResolvedValueOnce(activated);
    vi.mocked(desktopApi.scopes).mockResolvedValueOnce({
      items: [scope, { ...scope, scope_id: "same-title-other-id" }],
      next_cursor: null,
    });
    vi.mocked(desktopApi.selectScope).mockResolvedValueOnce({
      ...activated,
      generation: 3,
      active: { ...activated.active, generation: 3, scope },
    });
    const { unmount } = render(<App />);
    await user.click(await screen.findByRole("button", { name: "设置与诊断" }));
    await user.selectOptions(screen.getByLabelText("语言"), language);
    await user.click(screen.getByRole("button", { name: t.memories }));
    await user.click(screen.getByRole("button", { name: t.connectExisting }));
    await user.type(screen.getByLabelText(ct.name), profile.name);
    await user.type(screen.getByLabelText(ct.endpoint), profile.endpoint);
    await user.selectOptions(
      screen.getByLabelText(ct.compatibility),
      qualification.id,
    );
    await user.click(screen.getByRole("button", { name: ct.save }));
    expect(screen.getByText(ct.saved)).toBeTruthy();
    expect(desktopApi.check).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: ct.check }));
    expect(desktopApi.check).toHaveBeenLastCalledWith("a", false);
    expect(screen.getByText(ct.checkedComplete)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    screen.getByRole("button", { name: ct.activate }).focus();
    await user.keyboard("{Enter}");
    expect(desktopApi.check).toHaveBeenLastCalledWith("a", true);
    const dialog = screen.getByRole("dialog", { name: ct.scope });
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: t.closeDialog }),
    );
    await user.tab();
    expect(document.activeElement).toBe(
      within(dialog).getByLabelText(ct.scopeQuery),
    );
    await user.type(within(dialog).getByLabelText(ct.scopeQuery), "Scope");
    await user.click(within(dialog).getByRole("button", { name: ct.find }));
    expect(
      within(dialog).getAllByRole("button", { name: scope.title }),
    ).toHaveLength(2);
    within(dialog)
      .getByRole("button", { name: scope.title, description: scope.scope_id })
      .focus();
    await user.keyboard("{Enter}");
    expect(desktopApi.selectScope).toHaveBeenCalledWith(2, scope.scope_id);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: t.memories }),
    );
    expect(
      screen
        .getByRole("textbox", {
          name: language === "zh" ? "记忆内容" : "Memory text",
        })
        .matches(":enabled"),
    ).toBe(true);
    // Reopening consumes saved configuration without restoring a private session.
    unmount();
    vi.mocked(desktopApi.state).mockResolvedValue(saved);
    render(<App />);
    await screen.findByRole("heading", { name: messages.zh.noActiveTitle });
    expect(desktopApi.check).toHaveBeenCalledTimes(2);
  },
  15_000,
);

test("a rejected stale activation cannot open a Scope dialog or claim completion", async () => {
  const user = userEvent.setup();
  const onActivated = vi.fn();
  vi.mocked(desktopApi.check).mockResolvedValueOnce(state);
  render(
    <Connections
      state={state}
      language="en"
      initialProfileId="a"
      onState={() => false}
      onDirty={vi.fn()}
      onActivated={onActivated}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Use this connection" }));
  expect(screen.getByRole("alert").textContent).toContain("changed");
  expect(onActivated).not.toHaveBeenCalled();
});
test("activation guides exact Scope selection into Memory using the shared dialog", async () => {
  const user = userEvent.setup();
  const activated = {
    ...state,
    generation: 2,
    active: { ...state.active!, generation: 2, scope: null },
  };
  vi.mocked(desktopApi.check).mockResolvedValueOnce(activated);
  vi.mocked(desktopApi.scopes).mockResolvedValueOnce({
    items: [scope],
    next_cursor: null,
  });
  vi.mocked(desktopApi.selectScope).mockResolvedValueOnce({
    ...state,
    generation: 3,
    active: { ...state.active!, generation: 3 },
  });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "连接" }));
  await user.click(screen.getByRole("button", { name: /Original http/ }));
  await user.click(screen.getByRole("button", { name: "使用此连接" }));
  expect(desktopApi.check).toHaveBeenCalledWith("a", true);
  const dialog = screen.getByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "查找范围" }));
  await user.click(within(dialog).getByRole("button", { name: "Scope A" }));
  expect(desktopApi.selectScope).toHaveBeenCalledWith(2, "scope-a");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { level: 1, name: "记忆" }),
  );
  expect(
    screen.getByRole("textbox", { name: "记忆内容" }).matches(":enabled"),
  ).toBe(true);
});

test("a passive check reports completion without opening Scope selection", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.check).mockResolvedValueOnce(state);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "连接" }));
  await user.click(screen.getByRole("button", { name: /Original http/ }));
  await user.click(screen.getByRole("button", { name: "检查连接" }));
  expect(desktopApi.check).toHaveBeenCalledWith("a", false);
  expect(screen.getByText("连接检查已完成，请核对下方各项结果。")).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("closing the activation guide restores the activate button without choosing a Scope", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.check).mockResolvedValueOnce({
    ...state,
    generation: 2,
    active: { ...state.active!, generation: 2, scope: null },
  });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "连接" }));
  await user.click(screen.getByRole("button", { name: /Original http/ }));
  await user.click(screen.getByRole("button", { name: "使用此连接" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "使用此连接" }),
  );
  expect(desktopApi.selectScope).not.toHaveBeenCalled();
  expect(desktopApi.scopes).not.toHaveBeenCalled();
});

test("the activation guide preserves partial operation restrictions", async () => {
  const user = userEvent.setup();
  const partial = { ...report, supportedOperations: ["list_scopes"] };
  vi.mocked(desktopApi.check).mockResolvedValueOnce({
    ...state,
    generation: 2,
    reports: [partial],
    active: { ...state.active!, generation: 2, scope: null, report: partial },
  });
  vi.mocked(desktopApi.scopes).mockResolvedValueOnce({
    items: [scope],
    next_cursor: null,
  });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "连接" }));
  await user.click(screen.getByRole("button", { name: /Original http/ }));
  await user.click(screen.getByRole("button", { name: "使用此连接" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("范围选择不可用")).toBeTruthy();
  await user.click(within(dialog).getByRole("button", { name: "查找范围" }));
  expect(
    within(dialog)
      .getByRole("button", { name: scope.title })
      .matches(":disabled"),
  ).toBe(true);
  expect(desktopApi.selectScope).not.toHaveBeenCalled();
});

test.each(["network", "identity", "compatibility"])(
  "failed activation preserves the editor (%s)",
  async (failure) => {
    const user = userEvent.setup();
    if (failure === "network")
      vi.mocked(desktopApi.check).mockRejectedValueOnce({ code: "network" });
    else {
      const checked = {
        ...report,
        compatibilityVerified: failure !== "compatibility",
        anonymousAccess: failure !== "identity",
      };
      vi.mocked(desktopApi.check).mockResolvedValueOnce({
        ...state,
        generation: 2,
        reports: [checked],
        active: {
          ...state.active!,
          generation: 2,
          scope: null,
          report: checked,
        },
      });
    }
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "连接" }));
    await user.click(screen.getByRole("button", { name: /Original http/ }));
    await user.click(screen.getByRole("button", { name: "使用此连接" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((screen.getByLabelText("连接名称") as HTMLInputElement).value).toBe(
      "Original",
    );
    if (failure === "network")
      expect(screen.getByRole("alert").textContent).toContain("无法连接服务");
    else expect(screen.getByText(/连接尚不满足范围选择条件/)).toBeTruthy();
  },
);
beforeEach(() => {
  vi.mocked(desktopApi.state).mockResolvedValue(state);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

test.each(["使用此连接", "检查连接"])(
  "pending %s stays global after leaving the editor and prevents a draft in the stale context",
  async (action) => {
    const user = userEvent.setup();
    let finish!: (next: DesktopState) => void;
    vi.mocked(desktopApi.check).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "连接" }));
    await user.click(screen.getByRole("button", { name: /Original http/ }));
    await user.click(screen.getByRole("button", { name: action }));
    await user.click(screen.getByRole("button", { name: "记忆" }));
    expect(
      screen.getByRole("heading", { name: "正在更新连接状态…" }),
    ).toBeTruthy();
    const input = screen.getByRole("textbox", { name: "记忆内容" });
    expect(input.matches(":disabled")).toBe(true);
    await user.type(input, "this must not become a discarded draft");
    expect((input as HTMLTextAreaElement).value).toBe("");
    expect(
      screen.getByRole("button", { name: "保存" }).matches(":disabled"),
    ).toBe(true);
    expect(
      screen
        .getByRole("button", { name: /Scope A scope-a/ })
        .matches(":disabled"),
    ).toBe(true);
    expect(desktopApi.remember).not.toHaveBeenCalled();
    expect(desktopApi.search).not.toHaveBeenCalled();
    const next = {
      ...state,
      generation: 2,
      active: { ...state.active!, generation: 2, scope: null },
    };
    await act(async () => {
      finish(next);
    });
    expect(
      screen.queryByRole("heading", { name: "正在更新连接状态…" }),
    ).toBeNull();
    const selected = { ...scope, scope_id: "scope-b", title: "Scope B" };
    vi.mocked(desktopApi.scopes).mockResolvedValue({
      items: [selected],
      next_cursor: null,
    });
    vi.mocked(desktopApi.selectScope).mockResolvedValue({
      ...next,
      generation: 3,
      active: { ...next.active, generation: 3, scope: selected },
    });
    await user.click(screen.getByRole("button", { name: /未选择/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "查找范围" }));
    await user.click(within(dialog).getByRole("button", { name: "Scope B" }));
    const newInput = screen.getByRole("textbox", { name: "记忆内容" });
    await user.type(newInput, "new target draft");

    await user.click(screen.getByRole("button", { name: "观察" }));
    await answerConfirmation(user, false);
    expect((newInput as HTMLTextAreaElement).value).toBe("new target draft");
  },
  // This full navigation journey exceeds 5 seconds on shared Intel macOS runners.
  15_000,
);

test("a failed connection operation refreshes native state and releases the editing pause", async () => {
  const user = userEvent.setup();
  let fail!: (error: unknown) => void;
  vi.mocked(desktopApi.check).mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "连接" }));
  await user.click(screen.getByRole("button", { name: /Original http/ }));
  await user.click(screen.getByRole("button", { name: "使用此连接" }));
  await user.click(screen.getByRole("button", { name: "记忆" }));
  expect(
    screen.getByRole("textbox", { name: "记忆内容" }).matches(":disabled"),
  ).toBe(true);
  // A busy rejection can precede any native context change.
  await act(async () => {
    fail({ code: "busy" });
  });
  const input = screen.getByRole("textbox", { name: "记忆内容" });
  expect(input.matches(":enabled")).toBe(true);
  expect(
    screen.queryByRole("heading", { name: "正在更新连接状态…" }),
  ).toBeNull();
  await user.type(input, "draft after failure");

  await user.click(screen.getByRole("button", { name: "观察" }));
  await answerConfirmation(user, false);
  expect((input as HTMLTextAreaElement).value).toBe("draft after failure");
});

test("a second rejected check cannot end the first pending connection operation", async () => {
  const user = userEvent.setup();
  let finish!: (next: DesktopState) => void;
  vi.mocked(desktopApi.check)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockRejectedValueOnce({ code: "busy" });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "连接" }));
  await user.click(screen.getByRole("button", { name: /Original http/ }));
  await user.click(screen.getByRole("button", { name: "使用此连接" }));
  await user.click(screen.getByRole("button", { name: "观察" }));
  await user.click(screen.getByRole("button", { name: "连接" }));
  await user.click(screen.getByRole("button", { name: /Original http/ }));
  await user.click(screen.getByRole("button", { name: "使用此连接" }));
  await user.click(screen.getByRole("button", { name: "记忆" }));
  expect(
    screen.getByRole("textbox", { name: "记忆内容" }).matches(":disabled"),
  ).toBe(true);
  await act(async () => {
    finish({
      ...state,
      generation: 2,
      active: { ...state.active!, generation: 2, scope: null },
    });
  });
  expect(
    screen.queryByRole("heading", { name: "正在更新连接状态…" }),
  ).toBeNull();
});
