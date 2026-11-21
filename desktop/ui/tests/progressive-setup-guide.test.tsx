/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  canShowStartupGuide,
  getProgressiveGuideStep,
  GUIDE_SEQUENCE_DELAY_MS,
  hasAnyConfiguredModel,
  ProgressiveSetupGuide,
  StartupSuccessNotification,
  STARTUP_GUIDE_RETRY_DELAY_MS,
} from "../src/app/ProgressiveSetupGuide";

afterEach(cleanup);

test("shows startup guidance at most twice with a 24-hour retry", () => {
  const dismissedAt = 50_000;
  expect(
    canShowStartupGuide({
      prerequisitesMet: true,
      startOnLogin: false,
      disabledByUser: false,
      dismissCount: 0,
      dismissedAt: null,
    }),
  ).toBe(true);
  expect(
    canShowStartupGuide({
      prerequisitesMet: true,
      startOnLogin: false,
      disabledByUser: false,
      dismissCount: 1,
      dismissedAt,
      now: dismissedAt + STARTUP_GUIDE_RETRY_DELAY_MS - 1,
    }),
  ).toBe(false);
  expect(
    canShowStartupGuide({
      prerequisitesMet: true,
      startOnLogin: false,
      disabledByUser: false,
      dismissCount: 1,
      dismissedAt,
      now: dismissedAt + STARTUP_GUIDE_RETRY_DELAY_MS,
    }),
  ).toBe(true);
  expect(
    canShowStartupGuide({
      prerequisitesMet: true,
      startOnLogin: false,
      disabledByUser: false,
      dismissCount: 2,
      dismissedAt,
      now: dismissedAt + STARTUP_GUIDE_RETRY_DELAY_MS * 2,
    }),
  ).toBe(false);
});

test("requires a healthy local service, a configured Agent, and startup off", () => {
  expect(
    canShowStartupGuide({
      prerequisitesMet: false,
      startOnLogin: false,
      disabledByUser: false,
      dismissCount: 0,
      dismissedAt: null,
    }),
  ).toBe(false);
  expect(
    canShowStartupGuide({
      prerequisitesMet: true,
      startOnLogin: true,
      disabledByUser: false,
      dismissCount: 0,
      dismissedAt: null,
    }),
  ).toBe(false);
});

test("does not show startup guidance after the user turns startup off", () => {
  expect(
    canShowStartupGuide({
      prerequisitesMet: true,
      startOnLogin: false,
      disabledByUser: true,
      dismissCount: 0,
      dismissedAt: null,
    }),
  ).toBe(false);
});

test("prioritizes startup guidance and waits three minutes before models", () => {
  const endedAt = 10_000;
  expect(
    getProgressiveGuideStep({
      prerequisitesMet: true,
      startupGuideAllowed: true,
      anyModelConfigured: false,
      previousGuideEndedAt: null,
      now: endedAt,
    }),
  ).toBe("startup");
  expect(
    getProgressiveGuideStep({
      prerequisitesMet: true,
      startupGuideAllowed: false,
      anyModelConfigured: false,
      previousGuideEndedAt: endedAt,
      now: endedAt + GUIDE_SEQUENCE_DELAY_MS - 1,
    }),
  ).toBeNull();
  expect(
    getProgressiveGuideStep({
      prerequisitesMet: true,
      startupGuideAllowed: false,
      anyModelConfigured: false,
      previousGuideEndedAt: endedAt,
      now: endedAt + GUIDE_SEQUENCE_DELAY_MS,
    }),
  ).toBe("models");
});

test("shows a success message without another action", () => {
  render(<StartupSuccessNotification language="zh" onClose={vi.fn()} />);
  expect(screen.getByText("开机自动启动已开启")).toBeTruthy();
  expect(screen.getByText(/将在登录系统后自动启动/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "立即开启" })).toBeNull();
});

test("shows models immediately when no earlier guide event exists", () => {
  expect(
    getProgressiveGuideStep({
      prerequisitesMet: true,
      startupGuideAllowed: false,
      anyModelConfigured: false,
      previousGuideEndedAt: null,
    }),
  ).toBe("models");
});

test("treats either generation or embedding configuration as sufficient", () => {
  expect(hasAnyConfiguredModel({ generation: true, embedding: false })).toBe(
    true,
  );
  expect(hasAnyConfiguredModel({ generation: false, embedding: true })).toBe(
    true,
  );
  expect(hasAnyConfiguredModel({ generation: false, embedding: false })).toBe(
    false,
  );
});

test("skips guides when prerequisites are absent or any model is configured", () => {
  expect(
    getProgressiveGuideStep({
      prerequisitesMet: false,
      startupGuideAllowed: false,
      anyModelConfigured: false,
      previousGuideEndedAt: null,
    }),
  ).toBeNull();
  expect(
    getProgressiveGuideStep({
      prerequisitesMet: true,
      startupGuideAllowed: false,
      anyModelConfigured: true,
      previousGuideEndedAt: null,
    }),
  ).toBeNull();
});

test("renders the notification content and action", async () => {
  const user = userEvent.setup();
  const onAction = vi.fn();
  const onSecondaryAction = vi.fn();
  const onClose = vi.fn();
  render(
    <ProgressiveSetupGuide
      step="models"
      language="zh"
      onAction={onAction}
      onSecondaryAction={onSecondaryAction}
      onClose={onClose}
    />,
  );
  expect(screen.getByText("为本地服务配置模型能力")).toBeTruthy();
  expect(
    screen.getByText(/在本地 PowerContext 服务中配置生成与向量模型/),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "配置生成模型" }));
  await user.click(screen.getByRole("button", { name: "配置嵌入模型" }));
  await user.click(screen.getByRole("button", { name: "关闭通知" }));
  expect(onAction).toHaveBeenCalledOnce();
  expect(onSecondaryAction).toHaveBeenCalledOnce();
  expect(onClose).toHaveBeenCalledOnce();
});
