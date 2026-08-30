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

import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

import type { EvaluationApi } from "../api";
import type { HealthResponse } from "../types";

interface AppShellProps {
  api: EvaluationApi;
  path: string;
  batchId: string | null;
  navigate(path: string): void;
  children: ReactNode;
}

export function AppShell({ api, path, batchId, navigate, children }: AppShellProps) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api
      .getHealth(controller.signal)
      .then((value) => {
        setHealth(value);
        setHealthError(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setHealthError(true);
      });
    return () => controller.abort();
  }, [api]);

  const encodedBatchId = batchId === null ? null : encodeURIComponent(batchId);
  const taskReport = path.match(/^\/report\/[^/]+\/tasks(?:\/|$)/) !== null;
  const runtimeReport = path.match(/^\/report\/[^/]+\/running$/) !== null;
  const links = [
    {
      href: encodedBatchId === null ? "/" : `/report/${encodedBatchId}`,
      label: "总体报告",
      current: path !== "/baselines" && !taskReport && !runtimeReport,
      disabled: false,
    },
    {
      href: encodedBatchId === null ? "/" : `/report/${encodedBatchId}/running`,
      label: "当前运行任务",
      current: runtimeReport,
      disabled: encodedBatchId === null,
    },
    {
      href: encodedBatchId === null ? "/" : `/report/${encodedBatchId}/tasks`,
      label: "任务详细报告",
      current: taskReport,
      disabled: encodedBatchId === null,
    },
    {
      href: "/baselines",
      label: "基线库",
      current: path === "/baselines",
      disabled: false,
    },
  ];
  const onLink = (event: MouseEvent<HTMLAnchorElement>, href: string, disabled = false) => {
    if (disabled) {
      event.preventDefault();
      return;
    }
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" onClick={(event) => onLink(event, "/")}>
          <span className="brand-mark" aria-hidden="true">
            PC
          </span>
          <span>PowerContext</span>
          <small>Evaluation Console</small>
        </a>
        <nav aria-label="报告导航">
          {links.map((link) => (
            <a
              className="nav-link"
              href={link.href}
              aria-current={link.current ? "page" : undefined}
              aria-disabled={link.disabled ? "true" : undefined}
              onClick={(event) => onLink(event, link.href, link.disabled)}
              key={link.label}
            >
              {link.label}
            </a>
          ))}
        </nav>
      </aside>
      <div className="app-body">
        <header className="environment-bar" aria-label="运行环境">
          <span className="environment-name">评测环境</span>
          {healthError ? (
            <span className="health health--error">服务状态未知</span>
          ) : health === null ? (
            <span className="health">正在检查服务…</span>
          ) : (
            <>
              <span className="health health--ok">服务正常</span>
              <span className={`health ${health.worker_lease_active ? "health--ok" : "health--idle"}`}>
                {health.worker_lease_active ? "Worker 工作中" : "Worker 空闲"}
              </span>
              <span className="queue-count">
                任务对 {health.active_task_pairs} / {health.task_parallelism}
              </span>
              <span className="queue-count">队列 {health.queued_tasks}</span>
              <span className={`health ${health.resource_admission_open ? "health--ok" : "health--error"}`}>
                {health.resource_admission_open ? "资源门禁开放" : "资源门禁关闭"}
              </span>
            </>
          )}
        </header>
        <main className="main-content">{children}</main>
      </div>
    </div>
  );
}
