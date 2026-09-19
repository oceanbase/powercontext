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
import { Scopes } from "../src/app/Scopes";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState, ScopePage } from "../src/generated/ipc";
vi.mock("../src/shared/ipc", () => ({
  desktopApi: {
    scopes: vi.fn(),
    cancelScopes: vi.fn(),
    state: vi.fn(),
    selectScope: vi.fn(),
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
      supportedOperations: ["list_scopes"],
    },
  },
};
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

test("directory denial does not prevent explicitly authorized exact scope selection", async () => {
  const user = userEvent.setup();
  const onState = vi.fn();
  vi.mocked(desktopApi.scopes).mockRejectedValueOnce({ code: "forbidden" });
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  vi.mocked(desktopApi.selectScope).mockResolvedValue(state);
  render(
    <Scopes
      state={state}
      language="en"
      onState={onState}
      confirmSwitch={() => true}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Find scopes" }));
  expect(screen.getByRole("alert").textContent).toContain("cannot perform");
  await user.click(screen.getByText("Exact Scope ID", { selector: "summary" }));
  await user.type(screen.getByLabelText("Exact Scope ID"), "explicit-scope-b");
  await user.click(screen.getByRole("button", { name: "Select scope" }));
  expect(desktopApi.selectScope).toHaveBeenCalledWith(1, "explicit-scope-b");
  expect(desktopApi.scopes).toHaveBeenCalledTimes(1);
});
