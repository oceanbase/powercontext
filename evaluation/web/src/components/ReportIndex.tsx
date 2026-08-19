import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";

import type { EvaluationApi } from "../api";
import { batchStatusLabel } from "../batchStatus";
import type { BatchRecord } from "../types";

export function ReportIndex({ api, navigate }: { api: EvaluationApi; navigate(path: string): void }) {
  const [batches, setBatches] = useState<BatchRecord[] | null>(null);
  const [error, setError] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const load = useCallback(() => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    const currentGeneration = ++generation.current;
    setError(false);
    api.listBatches(nextController.signal)
      .then((nextBatches) => {
        if (!nextController.signal.aborted && currentGeneration === generation.current) {
          setBatches([...nextBatches].sort(
            (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at)
              || right.batch_id.localeCompare(left.batch_id),
          ));
        }
      })
      .catch(() => {
        if (!nextController.signal.aborted && currentGeneration === generation.current) setError(true);
      });
  }, [api]);

  useEffect(() => {
    load();
    return () => {
      controller.current?.abort();
      generation.current += 1;
    };
  }, [load]);

  const onLink = (event: MouseEvent<HTMLAnchorElement>, path: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(path);
  };

  if (error) {
    return (
      <section className="panel empty-state">
        <p>评测批次暂时无法加载。</p>
        <button type="button" className="secondary-button" onClick={load}>重试</button>
      </section>
    );
  }
  if (batches === null) return <section className="panel state-message">正在加载评测批次…</section>;
  if (batches.length === 0) return <section className="panel empty-state">暂无评测批次。</section>;

  return (
    <section className="panel report-index" aria-labelledby="report-index-heading">
      <h2 id="report-index-heading">评测批次</h2>
      <ul>
        {batches.map((batch, index) => {
          const path = `/report/${encodeURIComponent(batch.batch_id)}`;
          return (
            <li key={batch.batch_id}>
              <div>
                <strong>{batch.batch_id}{index === 0 && <span className="latest-label">最新批次</span>}</strong>
                <span>
                  {batch.total_tasks} 个任务 · {batch.request.powercontext_ref} · {batch.request.model} ·{" "}
                  {batchStatusLabel[batch.status]}
                </span>
              </div>
              <a
                className="primary-link"
                href={path}
                aria-label={`查看 ${batch.batch_id} 的总体报告`}
                onClick={(event) => onLink(event, path)}
              >
                查看报告
              </a>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
