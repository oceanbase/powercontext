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

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";

import type { EvaluationApi } from "../api";
import type { BatchRuntime as BatchRuntimeResponse, BatchRuntimeTask, TaskPhase } from "../types";

interface BatchRuntimeProps {
  api: EvaluationApi;
  batchId: string;
  navigate(path: string): void;
}

const phaseLabels: Record<TaskPhase, string> = {
  preparing: "准备环境",
  validating_gold: "验证 Gold",
  running_off: "OFF 执行",
  running_on: "ON 执行",
  official_evaluation: "官方评测",
  generating_report: "生成报告",
};

function duration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const remainder = seconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${remainder} 秒`;
  return `${remainder} 秒`;
}

function dateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function taskTiming(task: BatchRuntimeTask, now: number): string {
  if (task.status === "queued") {
    const eligible = Date.parse(task.eligible_at);
    return eligible <= now ? "已到领取时间" : `${duration(eligible - now)}后可重试`;
  }
  return task.started_at === null ? "刚刚领取" : `已运行 ${duration(now - Date.parse(task.started_at))}`;
}

export function BatchRuntime({ api, batchId, navigate }: BatchRuntimeProps) {
  const [runtime, setRuntime] = useState<BatchRuntimeResponse | null>(null);
  const [error, setError] = useState(false);
  const [now, setNow] = useState(Date.now());
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    const currentGeneration = ++generation.current;
    api.getBatchRuntime(batchId, nextController.signal)
      .then((value) => {
        if (nextController.signal.aborted || currentGeneration !== generation.current) return;
        setRuntime(value);
        setError(false);
      })
      .catch(() => {
        if (!nextController.signal.aborted && currentGeneration === generation.current) setError(true);
      });
  }, [api, batchId]);

  useEffect(() => {
    load();
    const refresh = window.setInterval(load, 5_000);
    const clock = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => {
      controller.current?.abort();
      generation.current += 1;
      window.clearInterval(refresh);
      window.clearInterval(clock);
    };
  }, [load]);

  const onTask = (event: MouseEvent<HTMLAnchorElement>, taskId: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(`/report/${encodeURIComponent(batchId)}/tasks/${encodeURIComponent(taskId)}`);
  };

  const overviewPath = `/report/${encodeURIComponent(batchId)}`;
  const onBack = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(overviewPath);
  };

  if (runtime === null && error) {
    return (
      <section className="panel empty-state">
        <p>当前运行任务暂时无法读取。</p>
        <button type="button" className="secondary-button" onClick={load}>重试</button>
      </section>
    );
  }
  if (runtime === null) return <section className="panel state-message">正在读取当前运行任务…</section>;

  const running = runtime.tasks.filter((task) => task.status === "running").length;
  const retrying = runtime.tasks.length - running;
  const counts = runtime.status_counts;
  return (
    <div className="batch-runtime">
      <div className="breadcrumb">
        <a href={overviewPath} onClick={onBack}>← 返回总体报告</a> / 当前运行任务
      </div>
      <header className="report-page-head">
        <div>
          <h1>当前运行任务</h1>
          <p>每 5 秒刷新，展示占用并发槽的任务和处于退避期的重试任务。</p>
        </div>
        <span className="batch-status">运行 {running} · 等待重试 {retrying}</span>
      </header>

      <section className="runtime-facts" aria-label="批次实时状态">
        <article><span>运行中</span><strong>{running}</strong></article>
        <article><span>等待重试</span><strong>{retrying}</strong></article>
        <article><span>普通排队</span><strong>{counts.queued - retrying}</strong></article>
        <article><span>已成功</span><strong>{counts.succeeded}</strong></article>
      </section>

      {error && <p className="error-message">刷新失败，正在保留上一次成功读取的数据。</p>}
      <section className="report-section runtime-table-section">
        <div className="section-heading">
          <div>
            <h2>任务明细</h2>
            <p>最近刷新 {dateTime(runtime.generated_at)}</p>
          </div>
        </div>
        {runtime.tasks.length === 0 ? (
          <p className="empty-state">当前没有运行中或等待重试的任务。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Source / 实例</th>
                  <th>状态 / 阶段</th>
                  <th>Attempt</th>
                  <th>运行或退避时间</th>
                  <th>上次失败</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {runtime.tasks.map((task) => {
                  const href = `/report/${encodeURIComponent(batchId)}/tasks/${encodeURIComponent(task.task_id)}`;
                  return (
                    <tr className={task.status === "running" ? "task-row--running" : "task-row--retry"} key={task.task_id}>
                      <td>
                        <strong>source{task.source_index}</strong>
                        <small className="runtime-instance" title={task.instance_id}>{task.instance_id}</small>
                      </td>
                      <td>
                        <span className="status">{task.status === "running" ? "运行中" : "等待重试"}</span>
                        <small>{task.phase === null ? "等待 Worker 领取" : phaseLabels[task.phase]}</small>
                      </td>
                      <td>
                        <strong>{task.attempt_number} / {task.attempt_count}</strong>
                        <small>{task.attempt_id}</small>
                      </td>
                      <td>
                        {taskTiming(task, now)}
                        <small>{task.status === "queued" ? `可领取 ${dateTime(task.eligible_at)}` : `开始 ${dateTime(task.started_at ?? task.created_at)}`}</small>
                      </td>
                      <td>
                        {task.last_failure === null ? "—" : (
                          <>
                            <span>{task.last_failure.code}</span>
                            <small>{task.last_failure.summary}</small>
                          </>
                        )}
                      </td>
                      <td><a href={href} onClick={(event) => onTask(event, task.task_id)}>查看详情</a></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
