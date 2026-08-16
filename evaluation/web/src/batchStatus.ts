import type { BatchStatus } from "./types";

export const batchStatusLabel: Record<BatchStatus, string> = {
  queued: "排队中",
  running: "进行中",
  pausing: "暂停中",
  paused: "已暂停",
  cancelling: "取消中",
  completed: "已完成",
  cancelled: "已取消",
};
