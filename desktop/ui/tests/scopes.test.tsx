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
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/app/App";
import { Scopes } from "../src/app/Scopes";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState, ScopePage } from "../src/generated/ipc";
vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    scopes: vi.fn(),
    cancelScopes: vi.fn(),
    state: vi.fn(),
    selectScope: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
  },
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const emptyFact = { value: null, error: null };
const state: DesktopState = {
  generation: 1,
  profiles: [],
  reports: [],
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
  active: {
    connectionId: "test",
    generation: 1,
    scope: null,
    report: {
      connectionId: "test",
      revision: 1,
      checkedAt: 1,
      liveness: emptyFact,
      readiness: emptyFact,
      identity: emptyFact,
      capabilities: emptyFact,
      compatibilityVerified: true,
      anonymousAccess: true,
      supportedOperations: ["list_scopes", "get_scope"],
    },
  },
};

test("a stale Scope result cannot finish the journey after its state is rejected", async () => {
  const user = userEvent.setup();
  const selected = {
    scope_id: "scope-a",
    title: "Exact Scope",
    summary: "",
    context_references: [],
    external_references: [],
    version: 1,
  };
  const onSelected = vi.fn();
  vi.mocked(desktopApi.scopes).mockResolvedValueOnce({
    items: [selected],
    next_cursor: null,
  });
  vi.mocked(desktopApi.selectScope).mockResolvedValueOnce({
    ...state,
    generation: 2,
    active: { ...state.active!, generation: 2, scope: selected },
  });
  render(
    <Scopes
      state={state}
      language="en"
      onState={() => false}
      confirmSwitch={() => true}
      onSelected={onSelected}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  await user.click(screen.getByRole("button", { name: "Exact Scope" }));
  expect(screen.getByRole("alert").textContent).toContain("changed");
  expect(onSelected).not.toHaveBeenCalled();
});
test("unsupported Scope selection leaves lookup and pagination available", async () => {
  const user = userEvent.setup();
  const active = state.active!;
  const partial: DesktopState = {
    ...state,
    active: {
      ...active,
      report: {
        ...active.report,
        supportedOperations: ["list_scopes"],
        discovery: {
          status: "supported",
          reason: null,
          error: null,
          server: null,
          operations: {
            get_scope: {
              status: "unsupported",
              reason: "operation_missing",
              qualificationId: null,
            },
          },
        },
      },
    },
  };
  vi.mocked(desktopApi.scopes).mockResolvedValue({
    items: [
      {
        scope_id: "scope-a",
        title: "Listed scope",
        summary: "",
        context_references: [],
        external_references: [],
        version: 1,
      },
    ],
    next_cursor: "opaque-next",
  });
  render(
    <Scopes
      state={partial}
      language="zh"
      onState={vi.fn()}
      confirmSwitch={() => true}
    />,
  );
  expect(screen.getByText(/Server 未声明支持此操作/)).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "查找范围" }));
  const choice = screen.getByRole("button", {
    name: "Listed scope",
  }) as HTMLButtonElement;
  expect(choice.disabled).toBe(true);
  await user.click(choice);
  expect(desktopApi.selectScope).not.toHaveBeenCalled();
  const more = screen.getByRole("button", {
    name: "下一页",
  }) as HTMLButtonElement;
  expect(more.disabled).toBe(false);
  await user.click(more);
  expect(desktopApi.scopes).toHaveBeenLastCalledWith(1, "", "opaque-next");
});
test("missing Scope lookup is disabled without claiming selection is unsupported", async () => {
  const user = userEvent.setup();
  const active = state.active!;
  const partial: DesktopState = {
    ...state,
    active: {
      ...active,
      report: {
        ...active.report,
        supportedOperations: ["get_scope"],
        discovery: {
          status: "supported",
          reason: null,
          error: null,
          server: null,
          operations: {
            list_scopes: {
              status: "unsupported",
              reason: "operation_missing",
              qualificationId: null,
            },
          },
        },
      },
    },
  };
  render(
    <Scopes
      state={partial}
      language="en"
      onState={vi.fn()}
      confirmSwitch={() => true}
    />,
  );
  expect(
    (screen.getByLabelText("Find scopes by title") as HTMLInputElement)
      .disabled,
  ).toBe(true);
  const find = screen.getByRole("button", {
    name: "Find scopes",
  }) as HTMLButtonElement;
  expect(find.disabled).toBe(true);
  expect(screen.getByText("Scope search is unavailable")).toBeTruthy();
  expect(
    screen.getByText(/Server does not declare support for this operation/),
  ).toBeTruthy();
  expect(screen.queryByText("Scope selection is unavailable")).toBeNull();
  await user.click(find);
  expect(desktopApi.scopes).not.toHaveBeenCalled();
});
test("editing a query cancels the native read and hides a late response", async () => {
  const user = userEvent.setup();
  let finish!: (page: ScopePage) => void;
  vi.mocked(desktopApi.scopes).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  vi.mocked(desktopApi.cancelScopes).mockResolvedValue();
  render(
    <Scopes
      state={state}
      language="en"
      onState={vi.fn()}
      confirmSwitch={() => true}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  await user.type(screen.getByLabelText("Find scopes by title"), "new");
  expect(desktopApi.cancelScopes).toHaveBeenCalledWith(1);
  await act(async () => {
    finish({
      items: [
        {
          scope_id: "old",
          title: "Old private title",
          summary: "",
          parent_scope_id: null,
          context_references: [],
          external_references: [],
          version: 1,
        },
      ],
      next_cursor: "old-page",
    });
  });
  expect(screen.queryByText("Old private title")).toBeNull();
  expect(screen.queryByRole("button", { name: "Next page" })).toBeNull();
  vi.mocked(desktopApi.scopes).mockResolvedValue({
    items: [],
    next_cursor: null,
  });
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  expect(desktopApi.scopes).toHaveBeenLastCalledWith(1, "new", null);
  expect(screen.getByText("No accessible scopes on this page.")).toBeTruthy();
});

test("a late Scope page cannot populate a different connection or reuse its cursor", async () => {
  const user = userEvent.setup();
  let finish!: (page: ScopePage) => void;
  vi.mocked(desktopApi.scopes).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const props = {
    language: "en" as const,
    onState: vi.fn(),
    confirmSwitch: () => true,
  };
  const { rerender } = render(<Scopes {...props} state={state} />);
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  rerender(
    <Scopes
      {...props}
      state={{
        ...state,
        generation: 2,
        active: { ...state.active!, connectionId: "other", generation: 2 },
      }}
    />,
  );
  await act(async () => {
    finish({
      items: [
        {
          scope_id: "old-private",
          title: "Old private Scope",
          summary: "",
          context_references: [],
          external_references: [],
          version: 1,
        },
      ],
      next_cursor: "old-cursor",
    });
  });
  expect(screen.queryByText("Old private Scope")).toBeNull();
  expect(screen.queryByRole("button", { name: "Next page" })).toBeNull();
  vi.mocked(desktopApi.scopes).mockResolvedValueOnce({
    items: [],
    next_cursor: null,
  });
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  expect(desktopApi.scopes).toHaveBeenLastCalledWith(2, "", null);
});

test("failed scope identity verification propagates the disconnected native state", async () => {
  const user = userEvent.setup();
  const onState = vi.fn();
  const disconnected = { ...state, generation: 2, active: null };
  vi.mocked(desktopApi.scopes).mockRejectedValueOnce({ code: "unauthorized" });
  vi.mocked(desktopApi.state).mockResolvedValue(disconnected);
  render(
    <Scopes
      state={state}
      language="en"
      onState={onState}
      confirmSwitch={() => true}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  expect(screen.getByRole("alert").textContent).toContain("valid credential");
  expect(onState).toHaveBeenCalledWith(disconnected);
});

test("expired scope cursor discards the old page and restarts explicitly", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.cancelScopes).mockResolvedValue();
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  vi.mocked(desktopApi.scopes)
    .mockResolvedValueOnce({ items: [], next_cursor: "opaque-page" })
    .mockRejectedValueOnce({ code: "cursor_expired" })
    .mockResolvedValueOnce({ items: [], next_cursor: null });
  render(
    <Scopes
      state={state}
      language="en"
      onState={vi.fn()}
      confirmSwitch={() => true}
    />,
  );
  await user.type(screen.getByLabelText("Find scopes by title"), "team");
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(desktopApi.scopes).toHaveBeenLastCalledWith(1, "team", "opaque-page");
  expect(screen.queryByRole("button", { name: "Next page" })).toBeNull();
  expect(screen.getByRole("alert").textContent).toContain("first page");
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  expect(desktopApi.scopes).toHaveBeenLastCalledWith(1, "team", null);
});

test.each([false, true])(
  "scope dialog closes only after a successful selection (failure=%s)",
  async (failure) => {
    const user = userEvent.setup();
    const scope = {
      scope_id: "scope-b",
      title: "Selected team",
      summary: "",
      context_references: [],
      external_references: [],
      version: 1,
    };
    vi.mocked(desktopApi.state).mockResolvedValue(state);
    vi.mocked(desktopApi.scopes).mockResolvedValue({
      items: [scope],
      next_cursor: null,
    });
    if (failure)
      vi.mocked(desktopApi.selectScope).mockRejectedValueOnce({
        code: "forbidden",
      });
    else
      vi.mocked(desktopApi.selectScope).mockResolvedValue({
        ...state,
        generation: 2,
        active: { ...state.active!, generation: 2, scope },
      });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /未选择/ }));
    expect(screen.queryByRole("button", { name: "精确范围" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "查看默认范围建议" }),
    ).toBeNull();
    expect(screen.queryByLabelText("精确 Scope ID")).toBeNull();
    await user.click(screen.getByRole("button", { name: "查找范围" }));
    await user.click(screen.getByRole("button", { name: "Selected team" }));
    expect(desktopApi.selectScope).toHaveBeenCalledWith(1, "scope-b");
    if (failure) {
      expect(screen.getByRole("dialog")).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toContain("无权");
    } else {
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(
        screen.getByRole("button", { name: /Selected team/ }),
      ).toBeTruthy();
    }
  },
);
