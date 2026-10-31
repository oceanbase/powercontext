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
  fireEvent,
  cleanup,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/app/App";
import { TopBar } from "../src/app/TopBar";
import { Scopes } from "../src/app/Scopes";
import { MemoryWorkspace } from "../src/app/MemoryWorkspace";
import { Connections } from "../src/app/Connections";
import { Diagnostics } from "../src/app/Diagnostics";
import { desktopApi, getFoundationInfo } from "../src/shared/ipc";
import type {
  DesktopState,
  CheckReport,
  DiagnosticReport,
  ScopePage,
} from "../src/generated/ipc";

vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn(),
    check: vi.fn(),
    scopes: vi.fn(),
    selectScope: vi.fn(),
    search: vi.fn(),
    atomicEntry: vi.fn(),
    save: vi.fn(),
    diagnostics: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
    cancelScopes: vi.fn().mockResolvedValue(undefined),
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
const fact = { value: null, error: null };
const report: CheckReport = {
  connectionId: "local",
  revision: 1,
  checkedAt: 1,
  liveness: fact,
  readiness: { value: { status: "ready", checks: {} }, error: null },
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
  scope_id: "exact-scope-a",
  title: "Same title",
  summary: "",
  version: 1,
  context_references: [],
  external_references: [],
};
const active: DesktopState = {
  ...disconnected,
  generation: 1,
  active: { connectionId: "local", generation: 1, scope, report },
};
beforeEach(() => {
  vi.mocked(getFoundationInfo).mockRejectedValue("unavailable");
  vi.mocked(desktopApi.state).mockResolvedValue(active);
  vi.mocked(desktopApi.cancelMemory).mockResolvedValue(undefined);
  vi.mocked(desktopApi.cancelScopes).mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.restoreAllMocks();
});

test("startup distinguishes pending, failed, and known disconnected state; reload is local only", async () => {
  const user = userEvent.setup();
  let fail!: (error: unknown) => void;
  vi.mocked(desktopApi.state).mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  render(<App />);
  expect(
    screen.getByText("正在加载 Desktop 状态…", {
      selector: "main .feedback-title",
    }),
  ).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "尚未连接服务" })).toBeNull();
  await act(async () => {
    fail({ code: "storage_error" });
  });
  expect(screen.getByRole("alert").textContent).toContain(
    "无法加载 Desktop 状态",
  );
  expect(screen.queryByRole("heading", { name: "尚未连接服务" })).toBeNull();
  vi.mocked(desktopApi.state).mockResolvedValue(disconnected);
  await user.click(screen.getByRole("button", { name: "重新加载本机状态" }));
  expect(
    await screen.findByRole("heading", { name: "尚未连接服务" }),
  ).toBeTruthy();
  expect(desktopApi.check).not.toHaveBeenCalled();
});

test("exact Scope IDs distinguish identical titles and newest matching facts are global", () => {
  const props = {
    language: "en" as const,
    onManageConnections: vi.fn(),
    onOpenScopes: vi.fn(),
    onDisconnect: vi.fn(),
  };
  const latest = {
    ...report,
    checkedAt: 2,
    readiness: {
      value: { status: "degraded" as const, checks: {} },
      error: null,
    },
  };
  const { rerender } = render(
    <TopBar {...props} state={{ ...active, reports: [latest] }} />,
  );
  expect(
    screen.getByRole("button", { name: /Same title exact-scope-a/ }),
  ).toBeTruthy();
  const context = screen.getByRole("group", {
    name: "Current connection and exact Scope",
  });
  expect(context.textContent).toContain("Service not ready");
  expect(context.textContent).toContain("Compatibility");
  expect(context.textContent).toContain("Liveness");
  expect(context.textContent).toContain("Last checked");
  rerender(
    <TopBar
      {...props}
      state={{
        ...active,
        active: {
          ...active.active!,
          scope: { ...scope, scope_id: "exact-scope-b" },
        },
        reports: [{ ...latest, revision: 2 }],
      }}
    />,
  );
  expect(
    screen.getByRole("button", { name: /Same title exact-scope-b/ }),
  ).toBeTruthy();
  expect(context.textContent).toContain("Service ready");
  expect(context.textContent).not.toContain("Service not ready");
});

test("connection keyboard menu and Scope dialog restore focus without checks", async () => {
  const user = userEvent.setup();
  render(<App />);
  const trigger = await screen.findByRole("button", { name: /当前连接:/ });
  trigger.focus();
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement).toBe(
    screen.getByRole("menuitem", { name: "管理连接" }),
  );
  await user.keyboard("{End}");
  expect(document.activeElement).toBe(
    screen.getByRole("menuitem", { name: "断开桌面连接" }),
  );
  await user.keyboard("{Escape}");
  expect(document.activeElement).toBe(trigger);
  const scopeTrigger = screen.getByRole("button", {
    name: /Same title exact-scope-a/,
  });
  await user.click(scopeTrigger);
  const dialog = screen.getByRole("dialog");
  const close = within(dialog).getByRole("button", { name: "关闭" });
  expect(document.activeElement).toBe(close);
  await user.keyboard("{Shift>}{Tab}{/Shift}");
  expect(document.activeElement).toBe(
    within(dialog).getByRole("button", { name: "查找范围" }),
  );
  await user.keyboard("{Tab}{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(scopeTrigger);
  await user.click(screen.getByRole("button", { name: "观察" }));
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { level: 1, name: "观察" }),
  );
  expect(
    screen.getByRole("group", { name: "当前连接与精确 Scope" }),
  ).toBeTruthy();
  expect(desktopApi.check).not.toHaveBeenCalled();
});

test("Scope loading is distinct from empty; expired pagination retries from the first page", async () => {
  const user = userEvent.setup();
  let finish!: (page: ScopePage) => void;
  vi.mocked(desktopApi.scopes)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockRejectedValueOnce({ code: "cursor_expired" })
    .mockResolvedValueOnce({ items: [], next_cursor: null });
  render(
    <Scopes
      state={active}
      language="en"
      onState={vi.fn()}
      confirmSwitch={() => true}
    />,
  );
  expect(screen.getByText("No Scope search has run")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  expect(screen.getByRole("status").textContent).toBe("Finding scopes…");
  expect(screen.queryByText("No accessible scopes on this page.")).toBeNull();
  await act(async () => {
    finish({ items: [], next_cursor: "expired" });
  });
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(screen.getByRole("alert").textContent).toContain("first page");
  await user.click(
    screen.getByRole("button", { name: "Search again from the first page" }),
  );
  expect(desktopApi.scopes).toHaveBeenLastCalledWith(1, "", null);
  expect(screen.getByText("No accessible scopes on this page.")).toBeTruthy();
});

test("failed exact reads retry the original immutable revision", async () => {
  const user = userEvent.setup();
  const artifact = {
    family: "atomic-memory",
    artifact_id: "saved-artifact",
    revision: 7,
  };
  vi.mocked(desktopApi.search).mockResolvedValue({
    hits: [{ artifact, text: "excerpt", score: 1, matched_by: ["text"] }],
  });
  vi.mocked(desktopApi.atomicEntry)
    .mockRejectedValueOnce({ code: "network" })
    .mockResolvedValueOnce({
      artifact,
      kind: "note",
      text: "revision seven",
      source_refs: [],
    });
  render(
    <MemoryWorkspace
      state={active}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Keyword search"), "find");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await user.click(screen.getByRole("button", { name: "Read exact version" }));
  expect(screen.queryByText("excerpt")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Retry read" }));
  expect(desktopApi.atomicEntry).toHaveBeenLastCalledWith(1, artifact);
  expect(screen.getByText("revision seven")).toBeTruthy();
});

test("healthy Server facts do not hide access denial or clear a note draft", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.search).mockRejectedValue({ code: "forbidden" });
  render(
    <MemoryWorkspace
      state={active}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Memory text"), "unsaved draft");
  await user.type(screen.getByLabelText("Keyword search"), "private");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(screen.getByRole("alert").textContent).toContain("permission");
  expect(screen.getByRole("alert").textContent).toContain("Health");
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
  ).toBe("unsaved draft");
});

test("empty profiles, pending save and failed save stay distinct and preserve editor input", async () => {
  const user = userEvent.setup();
  let fail!: (error: unknown) => void;
  vi.mocked(desktopApi.save).mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  render(
    <Connections
      state={disconnected}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  expect(screen.getByText("No connections added")).toBeTruthy();
  await user.type(screen.getByLabelText("Connection name"), "My draft");
  await user.type(
    screen.getByLabelText("Server address"),
    "http://127.0.0.1:8000",
  );
  await user.click(screen.getByRole("button", { name: "Save configuration" }));
  expect(
    screen
      .getAllByRole("status")
      .some((status) => status.textContent === "Saving…"),
  ).toBe(true);
  await act(async () => {
    fail({ code: "storage_error" });
  });
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value,
  ).toBe("My draft");
  expect(screen.queryByRole("button", { name: "Retry read" })).toBeNull();
});

test("local diagnostic retry keeps the chosen check and separates idle, pending, failed, empty", async () => {
  const user = userEvent.setup();
  let finish!: (report: DiagnosticReport) => void;
  vi.mocked(desktopApi.diagnostics)
    .mockRejectedValueOnce("not_found")
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  render(<Diagnostics language="en" />);
  expect(screen.getByText("No local check has run")).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Check local Agent integrations" }),
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "remote connections are unaffected",
  );
  await user.click(
    screen.getByRole("button", { name: "Run local check again" }),
  );
  expect(desktopApi.diagnostics).toHaveBeenLastCalledWith("integrations");
  expect(screen.getByRole("status").textContent).toContain("Checking");
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => {
    finish({ checkedAt: 1, exitCode: 0, items: [], hosts: [] });
  });
  expect(
    screen.getByText("This check returned no diagnostic items"),
  ).toBeTruthy();
});

test.each(["disconnected", "unverified", "scope"] as const)(
  "Memory prerequisite explains %s and offers its own recovery",
  async (kind) => {
    const user = userEvent.setup();
    const manage = vi.fn();
    const choose = vi.fn();
    const state =
      kind === "disconnected"
        ? disconnected
        : {
            ...active,
            active: {
              ...active.active!,
              scope: kind === "scope" ? null : scope,
              report: {
                ...report,
                compatibilityVerified: kind !== "unverified",
              },
            },
          };
    render(
      <MemoryWorkspace
        state={state}
        language="en"
        onState={vi.fn()}
        onDirty={vi.fn()}
        onManageConnections={manage}
        onOpenScopes={choose}
      />,
    );
    const recovery =
      kind === "scope"
        ? "Select an exact Scope first"
        : kind === "disconnected"
          ? "Connect an existing Server"
          : "Manage connections and credentials";
    await user.click(screen.getByRole("button", { name: recovery }));
    expect(kind === "scope" ? choose : manage).toHaveBeenCalledOnce();
    expect(
      (screen.getByRole("button", { name: "Save" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
  },
);

test("identity errors and incompatible facts are independent of a healthy Server", () => {
  const props = {
    language: "en" as const,
    onManageConnections: vi.fn(),
    onOpenScopes: vi.fn(),
    onDisconnect: vi.fn(),
  };
  const failure = {
    code: "unauthorized" as const,
    requestId: null,
    dispatched: false,
  };
  render(
    <TopBar
      {...props}
      state={{
        ...active,
        reports: [
          {
            ...report,
            checkedAt: 2,
            compatibilityVerified: false,
            anonymousAccess: false,
            identity: { value: null, error: failure },
          },
        ],
      }}
    />,
  );
  expect(
    screen.getByRole("button", { name: /Identity not verified yet/ }),
  ).toBeTruthy();
  const context = screen.getByRole("group");
  expect(context.textContent).toContain("Service ready");
  expect(context.textContent).toContain("Not verified");
});

test("Scope recovery navigation focuses the destination title", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.state).mockResolvedValue(disconnected);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: /未选择/ }));
  await user.click(screen.getByRole("button", { name: "管理连接与凭据" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { level: 1, name: "连接" }),
  );
});

test.each(["zh", "en"])(
  "%s Scope recovery on Connections restores focus and preserves its draft",
  async (language) => {
    const user = userEvent.setup();
    vi.mocked(desktopApi.state).mockResolvedValue(disconnected);
    render(<App />);
    await screen.findByRole("button", { name: /未选择/ });
    if (language === "en") {
      await user.click(screen.getByRole("button", { name: "设置与诊断" }));
      await user.selectOptions(screen.getByLabelText("语言"), "en");
    }
    const title = language === "zh" ? "连接" : "Connections";
    await user.click(screen.getByRole("button", { name: title }));
    const input = screen.getByRole("textbox", {
      name: language === "zh" ? "连接名称" : "Connection name",
    });
    await user.type(input, "keep this draft");
    await user.click(
      screen.getByRole("button", {
        name: language === "zh" ? /未选择/ : /None selected/,
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name:
          language === "zh"
            ? "管理连接与凭据"
            : "Manage connections and credentials",
      }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: title }),
    );
    expect((input as HTMLInputElement).value).toBe("keep this draft");
    expect(desktopApi.check).not.toHaveBeenCalled();
  },
);

test("clicking Scope with body focus restores the actual trigger after Escape", async () => {
  const user = userEvent.setup();
  render(<App />);
  const trigger = await screen.findByRole("button", {
    name: /Same title exact-scope-a/,
  });
  (document.activeElement as HTMLElement).blur();
  expect(document.activeElement).toBe(document.body);
  fireEvent.click(trigger);
  expect(document.activeElement).toBe(
    within(screen.getByRole("dialog")).getByRole("button", { name: "关闭" }),
  );
  await user.keyboard("{Escape}");
  expect(document.activeElement).toBe(trigger);
});
