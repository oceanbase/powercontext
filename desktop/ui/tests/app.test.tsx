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

import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/app/App";
afterEach(cleanup);
test("disconnected shell prevents writes and search and explains the boundary", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(screen.getByRole("heading", { name: "尚未连接服务" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: /连接已有服务/ }));
  expect(screen.getByRole("heading", { name: "添加连接" })).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "保存配置" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "记忆" }));
  expect(
    (screen.getByRole("button", { name: "保存记忆" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "搜索" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByText(/正文和搜索结果只在内存中使用/)).toBeTruthy();
});
test("navigation, bilingual settings and theme remain usable without the native host", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "设置与诊断" }));
  await user.selectOptions(screen.getByLabelText("语言"), "en");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
    "Settings & diagnostics",
  );
  await user.selectOptions(screen.getByLabelText("Theme"), "dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(document.documentElement.lang).toBe("en");
  await user.click(screen.getByRole("button", { name: /Memories/ }));
  expect(
    screen.getByText("Enter keywords to find existing memories."),
  ).toBeTruthy();
  expect(screen.queryByRole("list")).toBeNull();
});
