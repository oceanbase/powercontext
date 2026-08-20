import { useCallback, useEffect, useRef, useState, type ChangeEvent, type MouseEvent } from "react";

import type { EvaluationApi } from "../api";
import type { BatchTaskItem, BatchTaskPage, PairCategory } from "../types";

interface BatchTaskReportProps {
  api: EvaluationApi;
  batchId: string;
  search: string;
  navigate(path: string): void;
}

const categories: { value: PairCategory | ""; label: string }[] = [
  { value: "", label: "全部" },
  { value: "off_pass_on_fail", label: "OFF 通过 / ON 未通过" },
  { value: "off_fail_on_pass", label: "OFF 未通过 / ON 通过" },
  { value: "both_pass", label: "OFF / ON 均通过" },
  { value: "both_fail", label: "OFF / ON 均未通过" },
  { value: "execution_failure", label: "评测执行失败" },
];

const validCategories: ReadonlySet<string> = new Set(
  categories.map(({ value }) => value).filter((value) => value !== ""),
);
const validSorts = new Set(["source", "token_delta_asc", "token_delta_desc"]);

interface TaskQueryState {
  category: PairCategory | "";
  query: string;
  sort: "source" | "token_delta_asc" | "token_delta_desc";
  offset: number;
}

function number(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function queryState(search: string): TaskQueryState {
  const params = new URLSearchParams(search);
  const categoryValue = params.get("category") ?? "";
  const category: PairCategory | "" = validCategories.has(categoryValue) ? categoryValue as PairCategory : "";
  const query = params.get("q") ?? "";
  const sortValue = params.get("sort") ?? "source";
  const sort = validSorts.has(sortValue) ? sortValue as "source" | "token_delta_asc" | "token_delta_desc" : "source";
  const rawOffset = Number(params.get("offset") ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  return { category, query, sort, offset };
}

export function BatchTaskReport({ api, batchId, search, navigate }: BatchTaskReportProps) {
  const state = queryState(search);
  const [page, setPage] = useState<BatchTaskPage | null>(null);
  const [error, setError] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    const currentGeneration = ++generation.current;
    setError(false);
    api.listBatchTasks(
      batchId,
      {
        ...(state.category === "" ? {} : { category: state.category }),
        ...(state.query === "" ? {} : { query: state.query }),
        sort: state.sort,
        limit: 100,
        offset: state.offset,
      },
      nextController.signal,
    )
      .then((nextPage) => {
        if (!nextController.signal.aborted && currentGeneration === generation.current) setPage(nextPage);
      })
      .catch(() => {
        if (!nextController.signal.aborted && currentGeneration === generation.current) setError(true);
      });
  }, [api, batchId, state.category, state.offset, state.query, state.sort]);

  useEffect(() => {
    load();
    return () => {
      controller.current?.abort();
      generation.current += 1;
    };
  }, [load]);

  const basePath = `/report/${encodeURIComponent(batchId)}/tasks`;
  const update = (changes: { category?: string; query?: string; sort?: string; offset?: number }) => {
    const next = new URLSearchParams();
    const category = changes.category ?? state.category;
    const query = changes.query ?? state.query;
    const sort = changes.sort ?? state.sort;
    const offset = changes.offset ?? (changes.category !== undefined || changes.query !== undefined ? 0 : state.offset);
    if (category !== "") next.set("category", category);
    if (query !== "") next.set("q", query);
    if (sort !== "source") next.set("sort", sort);
    if (offset > 0) next.set("offset", String(offset));
    navigate(`${basePath}${next.size === 0 ? "" : `?${next.toString()}`}`);
  };
  const onLink = (event: MouseEvent<HTMLAnchorElement>, path: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(path);
  };

  return (
    <div className="batch-task-report">
      <div className="breadcrumb">
        <a href={`/report/${encodeURIComponent(batchId)}`} onClick={(event) => onLink(event, `/report/${encodeURIComponent(batchId)}`)}>
          总体报告
        </a>{" "}
        / 任务详细报告
      </div>
      <header className="report-page-head">
        <div>
          <h1>任务详细报告</h1>
          <p>逐项比较 OFF / ON 的官方结果和 Token 记录。</p>
        </div>
        {page !== null && <span className="batch-status">{number(page.total)} 个任务</span>}
      </header>

      <section className="task-report-toolbar" aria-label="任务筛选">
        <div className="filter-chips">
          {categories.map((category) => (
            <button
              type="button"
              className={state.category === category.value ? "filter-chip filter-chip--active" : "filter-chip"}
              aria-pressed={state.category === category.value}
              onClick={() => update({ category: category.value })}
              key={category.value}
            >
              {category.label}
            </button>
          ))}
        </div>
        <div className="task-search-sort">
          <label>
            <span className="visually-hidden">搜索仓库或任务 ID</span>
            <input
              aria-label="搜索仓库或任务 ID"
              type="search"
              placeholder="搜索仓库或任务 ID"
              value={state.query}
              onChange={(event: ChangeEvent<HTMLInputElement>) => update({ query: event.target.value })}
            />
          </label>
          <label>
            <span className="visually-hidden">Token 变化排序</span>
            <select
              aria-label="Token 变化排序"
              value={state.sort}
              onChange={(event) => update({ sort: event.target.value })}
            >
              <option value="source">任务集顺序</option>
              <option value="token_delta_desc">Token 差值从高到低</option>
              <option value="token_delta_asc">Token 差值从低到高</option>
            </select>
          </label>
        </div>
      </section>

      {error ? (
        <section className="panel empty-state">
          <p>任务数据暂时无法加载。</p>
          <button type="button" className="secondary-button" onClick={load}>重试</button>
        </section>
      ) : page === null ? (
        <section className="panel state-message">正在读取任务数据…</section>
      ) : page.items.length === 0 ? (
        <section className="panel empty-state">当前筛选条件下没有任务。</section>
      ) : (
        <>
          <div className="batch-table-wrap">
            <table className="batch-task-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>仓库</th>
                  <th>OFF</th>
                  <th>ON</th>
                  <th>实验对比结果</th>
                  <th>OFF Token</th>
                  <th>ON Token</th>
                  <th>Token 差值</th>
                  <th><span className="visually-hidden">操作</span></th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((task) => (
                  <TaskRow
                    task={task}
                    batchId={batchId}
                    search={search}
                    onLink={onLink}
                    key={task.task_id}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <span>{number(page.offset + 1)}–{number(Math.min(page.offset + page.items.length, page.total))} / {number(page.total)}</span>
            <button
              type="button"
              className="secondary-button"
              disabled={page.offset === 0}
              onClick={() => update({ offset: Math.max(0, page.offset - page.limit) })}
            >
              上一页
            </button>
            <button
              type="button"
              className="secondary-button"
              disabled={page.offset + page.items.length >= page.total}
              onClick={() => update({ offset: page.offset + page.limit })}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function TaskRow({
  task,
  batchId,
  search,
  onLink,
}: {
  task: BatchTaskItem;
  batchId: string;
  search: string;
  onLink(event: MouseEvent<HTMLAnchorElement>, path: string): void;
}) {
  const path = `/report/${encodeURIComponent(batchId)}/tasks/${encodeURIComponent(task.task_id)}${search}`;
  const executionFailure = task.pair_category === "execution_failure";
  const pendingLabel = task.status === "running"
    ? "运行中"
    : task.status === "queued"
      ? "排队中"
      : task.status === "cancelled"
        ? "已取消"
        : "";
  const category = categories.find(({ value }) => value === task.pair_category)?.label ?? pendingLabel;
  return (
    <tr>
      <td>
        <span className="task-cell-id">{task.task_id}</span>
        <small>{task.instance_id}</small>
        <small>
          {task.attempt_count} 次尝试
          {task.retryable ? " · 可重试" : ""}
        </small>
      </td>
      <td>{task.repository}</td>
      {executionFailure ? (
        <td colSpan={3}><strong className="execution-failure">评测执行失败</strong><small>{task.failure_summary}</small></td>
      ) : (
        <>
          <td><Resolution value={task.off?.resolved} /></td>
          <td><Resolution value={task.on?.resolved} /></td>
          <td>{category !== "" && <span className="pair-label">{category}</span>}</td>
        </>
      )}
      <td>{task.tokens.off === null ? "" : number(task.tokens.off)}</td>
      <td>{task.tokens.on === null ? "" : number(task.tokens.on)}</td>
      <td>{task.tokens.delta === null ? "" : `${task.tokens.delta > 0 ? "+" : ""}${number(task.tokens.delta)}`}</td>
      <td>
        <a href={path} onClick={(event) => onLink(event, path)} aria-label={`查看 ${task.task_id}`}>查看</a>
      </td>
    </tr>
  );
}

function Resolution({ value }: { value: boolean | undefined }) {
  if (value === undefined) return null;
  return <span className={value ? "resolution resolution--pass" : "resolution resolution--fail"}>{value ? "通过" : "未通过"}</span>;
}
