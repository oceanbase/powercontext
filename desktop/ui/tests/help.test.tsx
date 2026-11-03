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
import { Help } from "../src/app/Help";
import { App } from "../src/app/App";
import { messages } from "../src/app/messages";
import { getFoundationInfo } from "../src/shared/ipc";
vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: { state: vi.fn().mockRejectedValue({ code: "storage" }) },
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

test.each(["zh", "en"] as const)(
  "help explains the journey and copies fixed addresses (%s)",
  async (language) => {
    const user = userEvent.setup();
    const clipboard = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    render(<Help language={language} />);
    const t = messages[language];
    expect(screen.getByRole("heading", { name: t.help })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText(t.helpRecovery)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: t.copyDocuments }));
    expect(clipboard).toHaveBeenLastCalledWith(
      language === "zh"
        ? "https://github.com/oceanbase/powercontext/blob/master/desktop/README.zh.md"
        : "https://github.com/oceanbase/powercontext/blob/master/desktop/README.md",
    );
    expect(screen.getByRole("status").textContent).toBe(t.addressCopied);
    await user.click(screen.getByRole("button", { name: t.copyIssues }));
    expect(clipboard).toHaveBeenLastCalledWith(
      "https://github.com/oceanbase/powercontext/issues",
    );
    expect(screen.getByRole("status").textContent).toBe(t.addressCopied);
  },
);

test.each(["zh", "en"] as const)(
  "failed copies explain manual recovery and keep both addresses visible (%s)",
  async (language) => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
      new Error("clipboard unavailable"),
    );
    render(<Help language={language} />);
    const t = messages[language];
    for (const name of [t.copyDocuments, t.copyIssues]) {
      await user.click(screen.getByRole("button", { name }));
      expect(screen.getByRole("alert").textContent).toBe(t.addressCopyFailed);
      const documents = screen.getByRole("textbox", {
        name: t.helpDocuments,
      }) as HTMLTextAreaElement;
      expect(documents.value).toContain("/desktop/README");
      expect(
        (
          screen.getByRole("textbox", {
            name: t.helpIssues,
          }) as HTMLTextAreaElement
        ).value,
      ).toBe("https://github.com/oceanbase/powercontext/issues");
      await user.click(documents);
      expect(documents.readOnly).toBe(true);
      expect(documents.selectionStart).toBe(0);
      expect(documents.selectionEnd).toBe(documents.value.length);
    }
  },
);

test.each([
  null,
  {
    version: "0.2.0-native",
    phase: "S3",
    credentialBackend: "unavailable",
    serverConnected: false,
  },
])(
  "help remains reachable offline and version uses native information (%j)",
  async (host) => {
    const user = userEvent.setup();
    vi.mocked(getFoundationInfo).mockResolvedValueOnce(host);
    render(<App />);
    const settings = screen.getByRole("button", { name: "设置与诊断" });
    expect(settings.closest(".sidebar-bottom")).toBeTruthy();
    await user.click(settings);
    await user.selectOptions(screen.getByLabelText("语言"), "en");
    expect(screen.getByRole("heading", { name: "Usage help" })).toBeTruthy();
    const version = screen
      .getByRole("heading", { name: messages.en.version })
      .closest("section")!;
    expect(within(version).getByText(host?.version ?? "Unknown")).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Copy documentation address" })
        .matches(":enabled"),
    ).toBe(true);
  },
);
