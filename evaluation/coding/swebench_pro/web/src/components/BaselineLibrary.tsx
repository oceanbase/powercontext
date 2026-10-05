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
import type { BaselineRecord } from "../types";

export function BaselineLibrary({ api, navigate }: { api: EvaluationApi; navigate(path: string): void }) {
  const [baselines, setBaselines] = useState<BaselineRecord[] | null>(null);
  const [error, setError] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setError(false);
    api.listBaselines(next.signal)
      .then((items) => { if (!next.signal.aborted) setBaselines(items); })
      .catch(() => { if (!next.signal.aborted) setError(true); });
  }, [api]);

  useEffect(() => {
    load();
    return () => controller.current?.abort();
  }, [load]);

  const onLink = (event: MouseEvent<HTMLAnchorElement>, path: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(path);
  };

  if (error) {
    return <section className="panel empty-state"><p>基线库暂时无法加载。</p><button onClick={load}>重试</button></section>;
  }
  if (baselines === null) return <section className="panel state-message">正在加载基线库…</section>;

  return (
    <div className="batch-overview">
      <header className="report-page-head">
        <div>
          <p className="eyebrow">Historical baselines</p>
          <h1>基线库</h1>
          <p>每条基线冻结一个历史 Arm；默认按保存时间从新到旧排列。</p>
        </div>
      </header>
      <section className="panel report-index baseline-library" aria-label="基线列表">
        {baselines.length === 0 ? <p className="empty-state">暂无基线，请从已完成批次的总体报告保存。</p> : (
          <div className="baseline-table-wrap">
            <table className="baseline-table">
              <thead>
                <tr>
                  <th>名称</th><th>Arm</th><th>解决率</th><th>任务集</th><th>模型</th>
                  <th>PowerContext</th><th>保存时间</th><th>来源</th>
                </tr>
              </thead>
              <tbody>
                {baselines.map((baseline) => {
                  const path = `/report/${encodeURIComponent(baseline.source_batch_id)}`;
                  return (
                    <tr key={baseline.baseline_id}>
                      <td><strong>{baseline.name}</strong><small>{baseline.baseline_id}</small></td>
                      <td><span className={`arm-badge arm-badge--${baseline.source_arm}`}>{baseline.source_arm.toUpperCase()}</span></td>
                      <td>{baseline.resolved_tasks} / {baseline.total_tasks}</td>
                      <td>{baseline.task_set}</td>
                      <td>{baseline.model} · {baseline.reasoning_effort}</td>
                      <td>{baseline.powercontext_sha?.slice(0, 12) ?? "—"}</td>
                      <td>{new Date(baseline.created_at).toLocaleString("zh-CN", { hour12: false })}</td>
                      <td><a href={path} onClick={(event) => onLink(event, path)}>查看总体报告</a></td>
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
