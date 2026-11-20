/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { afterEach, expect, test, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MemoryWorkspace } from "../src/app/MemoryWorkspace";
import { MemoryWorkspaceContent } from "../src/app/MemoryWorkspaceContent";
import { TooltipProvider } from "../src/components/ui/tooltip";
import type { DesktopState } from "../src/generated/ipc";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const emptyFact = { value: null, error: null };
const connectedState: DesktopState = {
  generation: 1,
  profiles: [],
  reports: [],
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
  active: {
    connectionId: "connection-a",
    generation: 1,
    scope: {
      scope_id: "scope-a",
      title: "Test scope",
      summary: "",
      context_references: [],
      external_references: [],
      version: 1,
    },
    report: {
      connectionId: "connection-a",
      revision: 1,
      checkedAt: 1,
      liveness: emptyFact,
      readiness: emptyFact,
      identity: emptyFact,
      capabilities: emptyFact,
      compatibilityVerified: true,
      anonymousAccess: true,
      supportedOperations: ["search_memory"],
    },
  },
};

test("offers a keyboard-accessible return to the top after scrolling", async () => {
  const user = userEvent.setup();
  const main = document.createElement("main");
  const scrollTo = vi.fn();
  main.scrollTo = scrollTo;
  Object.defineProperty(main, "scrollTop", { configurable: true, value: 600 });
  document.body.append(main);
  render(
    <TooltipProvider>
      <MemoryWorkspaceContent
        language="zh"
        scopeTitle="测试空间"
        serviceName="测试服务"
        serviceEndpoint="http://localhost"
        initialCount={20}
      />
    </TooltipProvider>,
    { container: main },
  );
  fireEvent.scroll(main);
  await user.click(await screen.findByRole("button", { name: "回到顶部" }));
  expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { name: "记忆" }),
  );
});

test("opens, expands, navigates and closes memory details", async () => {
  const user = userEvent.setup();
  render(
    <TooltipProvider>
      <MemoryWorkspaceContent
        language="zh"
        scopeTitle="测试空间"
        serviceName="测试服务"
        serviceEndpoint="http://localhost"
        initialCount={4}
      />
    </TooltipProvider>,
  );
  await user.click(screen.getByRole("button", { name: /用户偏好简洁/ }));
  const details = screen.getByRole("complementary", { name: "记忆详情" });
  expect(within(details).getByText("记忆内容")).toBeTruthy();
  expect(details.querySelectorAll(".memory-excerpt")).toHaveLength(6);
  await user.click(within(details).getByRole("button", { name: "查看更多" }));
  expect(within(details).getByRole("button", { name: "收起" })).toBeTruthy();
  await user.click(within(details).getByRole("button", { name: "展开详情" }));
  expect(within(details).getByText("1 / 4")).toBeTruthy();
  await user.click(within(details).getByRole("button", { name: "下一条记忆" }));
  expect(within(details).getByText("2 / 4")).toBeTruthy();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(
    within(details).getByRole("button", { name: "展开详情" }),
  ).toBeTruthy();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("complementary", { name: "记忆详情" })).toBeNull();
});

test.each([
  ["zh", "暂无可访问的记忆空间", "安装服务", "连接服务"],
  ["en", "No accessible memory spaces", "Install service", "Connect service"],
] as const)(
  "shows the disconnected memory empty state in %s",
  (language, title, deploy, connect) => {
    const onOpenLocalServer = vi.fn();
    const onOpenExistingServer = vi.fn();
    render(
      <MemoryWorkspace
        state={{ ...connectedState, active: null }}
        language={language}
        onOpenLocalServer={onOpenLocalServer}
        onOpenExistingServer={onOpenExistingServer}
      />,
    );
    expect(screen.getByRole("heading", { name: title })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: deploy }));
    fireEvent.click(screen.getByRole("button", { name: connect }));
    expect(onOpenLocalServer).toHaveBeenCalledOnce();
    expect(onOpenExistingServer).toHaveBeenCalledOnce();
  },
);

test("filters memories immediately while typing", async () => {
  const user = userEvent.setup();
  render(
    <TooltipProvider>
      <MemoryWorkspaceContent
        language="en"
        scopeTitle="Test scope"
        serviceName="Test service"
        serviceEndpoint="http://localhost"
        initialCount={4}
      />
    </TooltipProvider>,
  );
  await user.type(
    screen.getByRole("textbox", { name: "Search memories" }),
    "React",
  );
  expect(screen.getByText("1 memories")).toBeTruthy();
  expect(
    screen.getByRole("button", {
      name: /PowerContext Desktop is built with React\s*, TypeScript, and Tauri/,
    }),
  ).toBeTruthy();
});
