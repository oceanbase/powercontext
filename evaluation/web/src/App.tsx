import { useCallback, useEffect, useState } from "react";

import { EvaluationApi } from "./api";
import { AppShell } from "./components/AppShell";
import { BatchLauncher } from "./components/BatchLauncher";
import { AuthPanel } from "./components/AuthPanel";
import { BatchOverview } from "./components/BatchOverview";
import { BatchRuntime } from "./components/BatchRuntime";
import { BatchTaskReport } from "./components/BatchTaskReport";
import { ReportIndex } from "./components/ReportIndex";
import { TaskRunDetail } from "./components/TaskRunDetail";

interface AppProps {
  api?: EvaluationApi;
}

function useLocation(): [string, (next: string) => void] {
  const currentLocation = () => `${window.location.pathname}${window.location.search}`;
  const [location, setLocation] = useState(currentLocation);
  useEffect(() => {
    const onPop = () => setLocation(currentLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((next: string) => {
    if (next !== currentLocation()) window.history.pushState({}, "", next);
    setLocation(next);
  }, []);
  return [location, navigate];
}

interface Route {
  batchId: string | null;
  taskId: string | null;
  page: "overview" | "runtime" | "tasks" | "task";
}

function parseRoute(path: string): Route {
  const runtimeMatch = path.match(/^\/report\/([^/]+)\/running$/);
  if (runtimeMatch?.[1]) {
    return { batchId: decodeURIComponent(runtimeMatch[1]), taskId: null, page: "runtime" };
  }
  const taskMatch = path.match(/^\/report\/([^/]+)\/tasks\/([^/]+)$/);
  if (taskMatch?.[1] && taskMatch[2]) {
    return {
      batchId: decodeURIComponent(taskMatch[1]),
      taskId: decodeURIComponent(taskMatch[2]),
      page: "task",
    };
  }
  const tasksMatch = path.match(/^\/report\/([^/]+)\/tasks$/);
  if (tasksMatch?.[1]) {
    return { batchId: decodeURIComponent(tasksMatch[1]), taskId: null, page: "tasks" };
  }
  const overviewMatch = path.match(/^\/report\/([^/]+)$/);
  if (overviewMatch?.[1]) {
    return { batchId: decodeURIComponent(overviewMatch[1]), taskId: null, page: "overview" };
  }
  return { batchId: null, taskId: null, page: "overview" };
}

export function App({ api: injectedApi }: AppProps) {
  const [defaultApi] = useState(() => new EvaluationApi());
  const api = injectedApi ?? defaultApi;
  const [location, navigate] = useLocation();
  const [pathValue, searchValue = ""] = location.split("?", 2);
  const path = pathValue ?? "/";
  const search = searchValue === "" ? "" : `?${searchValue}`;
  const route = parseRoute(path);

  let content;
  if (route.page === "task" && route.batchId !== null && route.taskId !== null) {
    content = (
      <div className="page">
        <TaskRunDetail
          api={api}
          batchId={route.batchId}
          taskId={route.taskId}
          search={search}
          navigate={navigate}
        />
      </div>
    );
  } else if (route.page === "runtime" && route.batchId !== null) {
    content = (
      <div className="page">
        <BatchRuntime api={api} batchId={route.batchId} navigate={navigate} />
      </div>
    );
  } else if (route.page === "tasks" && route.batchId !== null) {
    content = (
      <div className="page">
        <BatchTaskReport api={api} batchId={route.batchId} search={search} navigate={navigate} />
      </div>
    );
  } else if (route.batchId !== null) {
    content = (
      <div className="page">
        <BatchOverview api={api} batchId={route.batchId} navigate={navigate} />
      </div>
    );
  } else {
    content = (
      <div className="page">
        <PageHeader
          eyebrow="PowerContext Evaluation"
          title="总体报告"
          description="选择已有批次，或提交一次固定 731 任务的完整 OFF / ON 评测。"
        />
        <div className="batch-home-grid">
          <div className="batch-home-actions">
            <BatchLauncher
              api={api}
              onCreated={(batch) => navigate(`/report/${encodeURIComponent(batch.batch_id)}`)}
            />
            <AuthPanel api={api} />
          </div>
          <ReportIndex api={api} navigate={navigate} />
        </div>
      </div>
    );
  }

  return (
    <AppShell api={api} path={path} batchId={route.batchId} navigate={navigate}>
      {content}
    </AppShell>
  );
}

function PageHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description?: string }) {
  return (
    <header className="page-header">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      {description && <p>{description}</p>}
    </header>
  );
}
