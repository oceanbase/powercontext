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
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/app/App";
import { answerConfirmation } from "./confirmation";

vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn().mockResolvedValue({
      generation: 0,
      profiles: [],
      reports: [],
      active: null,
      compatibilityProfiles: [],
      pendingCredentialCleanup: 0,
      lastWrite: null,
    }),
  },
}));
afterEach(cleanup);

test.each(["zh", "en"])(
  "%s discard dialogs support keyboard cancellation and explicit confirmation",
  async (language) => {
    const user = userEvent.setup();
    render(<App />);
    if (language === "en") {
      await user.click(screen.getByRole("button", { name: "设置与诊断" }));
      await user.selectOptions(screen.getByLabelText("语言"), "en");
    }
    await user.click(
      screen.getByRole("button", {
        name: language === "zh" ? "连接" : "Connections",
      }),
    );
    const input = screen.getByRole("textbox", {
      name: language === "zh" ? "连接名称" : "Connection name",
    });
    await user.type(input, "unsaved Chinese 中文");
    await user.click(
      screen.getByRole("button", {
        name: language === "zh" ? "观察" : "Observation",
      }),
    );
    const dialog = screen.getByRole("alertdialog", {
      name: language === "zh" ? "确认操作" : "Confirm action",
    });
    expect(dialog.textContent).toContain(
      language === "zh" ? "丢弃尚未保存的输入？" : "Discard unsaved input?",
    );
    const cancel = within(dialog).getByRole("button", {
      name: language === "zh" ? "取消" : "Cancel",
    });
    const confirm = within(dialog).getByRole("button", {
      name: language === "zh" ? "确认" : "Confirm",
    });
    expect(document.activeElement).toBe(cancel);
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(document.activeElement).toBe(confirm);
    await user.keyboard("{Tab}");
    expect(document.activeElement).toBe(cancel);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect((input as HTMLInputElement).value).toBe("unsaved Chinese 中文");
    expect(screen.queryByRole("tablist")).toBeNull();
    await user.click(
      screen.getByRole("button", {
        name: language === "zh" ? "取消编辑" : "Cancel editing",
      }),
    );
    await answerConfirmation(user, true);
    expect((input as HTMLInputElement).value).toBe("");
    await user.click(
      screen.getByRole("button", {
        name: language === "zh" ? "观察" : "Observation",
      }),
    );
    expect(screen.getByRole("tablist")).toBeTruthy();
  },
);
