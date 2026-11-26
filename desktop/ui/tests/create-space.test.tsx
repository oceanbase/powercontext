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

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { CreateSpaceDialog } from "../src/app/App";
import { previewDesktopState } from "../src/app/preview-data";

afterEach(cleanup);

test("rejects a duplicate space name in the active connection", async () => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  render(
    <CreateSpaceDialog
      language="zh"
      state={previewDesktopState(null, "zh")}
      onClose={() => {}}
      onCreated={onCreated}
      returnFocus={() => null}
    />,
  );

  await user.type(screen.getByLabelText("空间名称"), "  默认空间  ");
  await user.click(screen.getByRole("button", { name: "创建空间" }));

  expect(screen.getByText("当前连接下已存在同名空间")).toBeTruthy();
  expect(screen.getByLabelText("空间名称").getAttribute("aria-invalid")).toBe(
    "true",
  );
  expect(onCreated).not.toHaveBeenCalled();
});
