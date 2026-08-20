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

import { copyFile } from "node:fs/promises";

import { expect, test, type Page } from "@playwright/test";

interface BatchTaskItem {
  task_id: string;
  attempt_count: number;
  retryable: boolean;
  instance_id: string;
  status: string;
}

interface BatchTaskPage {
  items: BatchTaskItem[];
}

interface BatchState {
  status: string;
  control: {
    intent: string;
    pause_reason: string | null;
    usage_pause_percent: number;
  };
}

async function batchState(page: Page, batchId: string): Promise<BatchState> {
  return page.evaluate(async (id) => {
    const response = await fetch(`/api/batches/${encodeURIComponent(id)}`);
    return response.json() as Promise<BatchState>;
  }, batchId);
}

async function batchTasks(page: Page, batchId: string): Promise<BatchTaskPage> {
  return page.evaluate(async (id) => {
    const response = await fetch(`/api/batches/${encodeURIComponent(id)}/tasks?limit=100`);
    return response.json() as Promise<BatchTaskPage>;
  }, batchId);
}

async function usedPercent(page: Page): Promise<number> {
  return page.evaluate(async () => {
    const response = await fetch("/api/account-usage");
    const body = await response.json() as { used_percent: number };
    return body.used_percent;
  });
}

async function waitForBatch(page: Page, batchId: string, status: string): Promise<void> {
  await expect.poll(
    async () => (await batchState(page, batchId)).status,
    { intervals: [50], timeout: 15_000 },
  ).toBe(status);
}

test.beforeEach(async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => browserErrors.push(`pageerror: ${error.message}`));
  (page as Page & { browserErrors?: string[] }).browserErrors = browserErrors;
});

test.afterEach(async ({ page }) => {
  expect((page as Page & { browserErrors?: string[] }).browserErrors ?? []).toEqual([]);
});

test("controls a serial batch at task boundaries and retains a retried attempt", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "报告导航" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "总体报告" })).toBeVisible();
  await expect(page.getByLabel("PowerContext 版本")).toHaveValue("latest");
  await expect(page.getByLabel("测试实例")).toHaveCount(0);

  await page.getByRole("button", { name: "预览评测" }).click();
  await expect(page.getByRole("region", { name: "评测确认" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "6 个基准任务" })).toBeVisible();
  await expect(page.getByText("当前用量 20%")).toBeVisible();
  await expect(page.getByRole("link", { name: "任务详细报告" })).toHaveAttribute("aria-disabled", "true");

  await page.getByRole("button", { name: "确认并开始评测" }).click();
  await expect(page).toHaveURL(/\/report\/batch-/);
  const batchId = decodeURIComponent(new URL(page.url()).pathname.split("/")[2] ?? "");
  expect(batchId).toMatch(/^batch-/);

  await expect.poll(
    async () => {
      const body = await batchTasks(page, batchId);
      return {
        running: body.items.filter((item) => item.status === "running").length,
        queued: body.items.filter((item) => item.status === "queued").length,
      };
    },
    { intervals: [30] },
  ).toEqual({ running: 1, queued: 5 });

  await page.getByRole("button", { name: "暂停" }).click();
  await expect(page.getByText("等待当前任务完成")).toBeVisible();
  await waitForBatch(page, batchId, "paused");
  const afterPause = await batchTasks(page, batchId);
  expect(afterPause.items[0]).toMatchObject({
    instance_id: "instance_e2e__repo-a",
    status: "failed",
    attempt_count: 1,
    retryable: true,
  });
  expect(afterPause.items.slice(1).every((task) => task.status === "queued")).toBe(true);

  await page.reload();
  await expect(page.getByText("Codex 账户用量").locator("..").getByText("40%")).toBeVisible();
  await expect(page.getByText("暂停原因：用户请求")).toBeVisible();
  await page.getByRole("button", { name: "继续运行" }).click();

  await expect.poll(
    async () => (await batchTasks(page, batchId)).items[1]?.status,
    { intervals: [30] },
  ).toBe("running");
  await waitForBatch(page, batchId, "paused");
  const thresholdPause = await batchState(page, batchId);
  expect(thresholdPause.control).toMatchObject({
    intent: "pause",
    pause_reason: "usage_threshold",
    usage_pause_percent: 80,
  });
  const afterThreshold = await batchTasks(page, batchId);
  expect(afterThreshold.items[1]?.status).toBe("succeeded");
  expect(afterThreshold.items[2]?.status).toBe("queued");

  await page.reload();
  await expect(page.getByText("Codex 账户用量").locator("..").getByText("81%")).toBeVisible();
  await expect(page.getByText("暂停原因：达到用量阈值")).toBeVisible();
  await page.getByLabel("批次暂停阈值").fill("90");
  await page.getByRole("button", { name: "保存阈值" }).click();
  await expect.poll(async () => (await batchState(page, batchId)).control.usage_pause_percent).toBe(90);
  expect((await batchState(page, batchId)).status).toBe("paused");
  expect((await batchTasks(page, batchId)).items[2]?.status).toBe("queued");
  expect(await usedPercent(page)).toBe(81);

  await page.getByRole("button", { name: "继续运行" }).click();
  await expect.poll(
    async () => (await batchTasks(page, batchId)).items[2]?.status,
    { intervals: [30] },
  ).toBe("running");
  await page.getByRole("button", { name: "取消批次" }).click();
  await expect(page.getByText("等待当前任务完成后取消")).toBeVisible();
  await waitForBatch(page, batchId, "cancelled");
  const afterCancel = await batchTasks(page, batchId);
  expect(afterCancel.items[2]?.status).toBe("succeeded");
  expect(afterCancel.items.slice(3).every((task) => task.status === "cancelled")).toBe(true);
  expect(await usedPercent(page)).toBe(50);

  await page.getByRole("navigation", { name: "报告导航" }).getByRole("link", { name: "任务详细报告" }).click();
  await page.reload();
  await page.getByRole("button", { name: "评测执行失败" }).click();
  await expect(page.getByText("e2e/repo-a")).toBeVisible();
  const failedRow = page.locator("tr", { hasText: "e2e/repo-a" });
  const taskId = (await failedRow.locator(".task-cell-id").textContent())?.trim() ?? "";
  expect(taskId).toMatch(/^run-/);
  await failedRow.getByRole("link", { name: `查看 ${taskId}` }).click();

  await expect(page.getByRole("heading", { name: "单任务详情" })).toBeVisible();
  await expect(page.getByText("评测执行失败", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "尝试 1" })).toBeVisible();
  await page.getByRole("button", { name: "重试此任务" }).click();
  await expect(page.getByText("已有尝试和证据不会被覆盖")).toBeVisible();
  await page.getByRole("button", { name: "确认重试" }).click();

  await expect.poll(
    async () => (await batchTasks(page, batchId)).items[0]?.status,
    { intervals: [50], timeout: 15_000 },
  ).toBe("succeeded");
  await waitForBatch(page, batchId, "completed");
  await page.reload();
  await expect(page.getByRole("button", { name: "尝试 1" })).toBeVisible();
  await expect(page.getByRole("button", { name: "尝试 2" })).toBeVisible();
  await page.getByRole("button", { name: "尝试 1" }).click();
  await expect(page.getByText("Codex execution failed.")).toBeVisible();
  await page.getByRole("button", { name: "尝试 2" }).click();
  await expect(page.getByText("OFF 未通过")).toBeVisible();
  await expect(page.getByText("ON 通过")).toBeVisible();
  await expect(page.getByRole("button", { name: /#3.*PowerContext 注入/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /#4.*PowerContext 注入/ })).toBeVisible();
  await page.getByRole("button", { name: /#3.*PowerContext 注入/ }).click();
  const eventDetail = page.getByLabel("事件详情");
  await expect(eventDetail.getByText("e2e/repo-a architecture")).toBeVisible();
  await expect(eventDetail.getByText(/memory:\/\/architecture\/1/)).toBeVisible();
  await expect(eventDetail.getByText("PowerContext recalled the repository service boundary.")).toBeVisible();

  await page.getByRole("button", { name: "OFF 时间线" }).click();
  await expect(page.getByRole("button", { name: /PowerContext 注入/ })).toHaveCount(0);
  await page.getByRole("button", { name: "ON 时间线" }).click();
  await expect(page.getByRole("button", { name: /#3.*PowerContext 注入/ })).toBeVisible();

  await page.getByRole("navigation", { name: "报告导航" }).getByRole("link", { name: "总体报告" }).click();
  await page.reload();
  const correctness = page.getByLabel("正确性汇总");
  await expect(correctness.getByText("6", { exact: true })).toBeVisible();
  await expect(page.getByText("可比较任务 3 / 6")).toBeVisible();
  await expect(page.getByRole("link", { name: /OFF 未通过.*ON 通过.*1/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /OFF 通过.*ON 未通过.*1/ })).toBeVisible();
  await expect(page.getByText("已取消 3")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/提升|退化|验收有效|验收无效|优先分析/);
  const controlEvents = await page.evaluate(async (id) => {
    const response = await fetch(`/api/batches/${encodeURIComponent(id)}/control-events`);
    const body = await response.json() as { event_type: string }[];
    return body.map((event) => event.event_type);
  }, batchId);
  expect(controlEvents).toEqual(expect.arrayContaining([
    "batch_created",
    "pause_requested",
    "paused",
    "resume_requested",
    "resumed",
    "usage_threshold_reached",
    "threshold_changed",
    "cancel_requested",
    "cancelled",
    "task_retry_requested",
    "batch_completed",
  ]));

  for (const width of [1440, 960]) {
    await page.setViewportSize({ width, height: 1000 });
    const dimensions = await page.evaluate(() => {
      window.scrollTo({ left: 10_000, top: 0 });
      const value = {
        pageScrollX: window.scrollX,
      };
      window.scrollTo({ left: 0, top: 0 });
      return value;
    });
    expect(dimensions.pageScrollX).toBe(0);
  }
  const reviewScreenshot = testInfo.outputPath("controlled-batch-overview-review.png");
  await page.screenshot({ path: reviewScreenshot, fullPage: true });
  await copyFile(reviewScreenshot, "/tmp/powercontext-batch-evaluation-console.png");
});

test("keeps task detail contextual at desktop width", async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 900 });
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "报告导航" });
  await expect(navigation.getByRole("link", { name: "总体报告" })).toHaveAttribute("aria-current", "page");
  await expect(navigation.getByRole("link", { name: "任务详细报告" })).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByRole("link", { name: /单任务详情/ })).toHaveCount(0);
  const layout = await page.evaluate(() => {
    window.scrollTo({ left: 10_000, top: 0 });
    const value = {
      sidebar: getComputedStyle(document.querySelector(".sidebar")!).position,
      pageScrollX: window.scrollX,
    };
    window.scrollTo({ left: 0, top: 0 });
    return value;
  });
  expect(layout.sidebar).toBe("fixed");
  expect(layout.pageScrollX).toBe(0);
});
