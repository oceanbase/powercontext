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
import { Diagnostics } from "../src/app/Diagnostics";
import { desktopApi } from "../src/shared/ipc";
vi.mock("../src/shared/ipc", () => ({ desktopApi: { diagnostics: vi.fn() } }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
test("diagnostics run only on explicit request and a missing CLI is local", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.diagnostics).mockRejectedValue("not_found");
  render(<Diagnostics language="en" />);
  expect(desktopApi.diagnostics).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Check local service" }));
  expect(desktopApi.diagnostics).toHaveBeenCalledWith("service");
  expect(screen.getByRole("alert").textContent).toContain(
    "remote connections are unaffected",
  );
});
test("valid unhealthy results retain their exit code and diagnostic states", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.diagnostics).mockResolvedValue({
    checkedAt: 1,
    exitCode: 1,
    items: [{ field: "manager", status: "inactive" }],
    hosts: [],
  });
  render(<Diagnostics language="zh" />);
  await user.click(screen.getByRole("button", { name: "检查本机服务" }));
  expect(screen.getByText("未运行")).toBeTruthy();
  expect(screen.getByText(/退出码: 1/)).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});
