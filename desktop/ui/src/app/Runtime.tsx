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

import { useEffect, useRef, useState, type ReactNode } from "react";
import type {
  ActiveView,
  RuntimeCapabilities,
  OpsComponents,
  OpsInspectionReply,
  ReadinessStatus,
  RuntimeFact,
  HealthResponse,
} from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import { opsApi } from "../shared/ops";
import { Diagnostics } from "./Diagnostics";
import { ErrorFeedback, PageFeedback } from "./PageFeedback";
import type { Language } from "./messages";

type Text = (en: string, zh: string) => string;

const statuses: Record<string, [string, string]> = {
  ready: ["Ready", "就绪"],
  auto: ["Automatic", "自动"],
  fts: ["Full text", "全文"],
  vector: ["Vector", "向量"],
  hybrid: ["Hybrid", "混合"],
  inference: ["Inference", "模型推理"],
  worker: ["Worker", "工作进程"],
  flush: ["Explicit processing", "显式处理"],
  supervisor: ["Supervisor", "监督进程"],
  lease_renewal: ["Lease renewal", "租约续期"],
  scope_discovery: ["Scope discovery", "Scope 发现"],
  model_configuration_error: ["Model configuration error", "模型配置错误"],
  model_timeout: ["Model timed out", "模型超时"],
  model_unavailable: ["Model unavailable", "模型不可用"],
  invalid_model_output: ["Invalid model output", "模型输出无效"],
  worker_timeout: ["Worker timed out", "工作进程超时"],
  worker_crash: ["Worker crashed", "工作进程崩溃"],
  invalid_worker_result: ["Invalid worker result", "工作进程结果无效"],
  missing_durable_acknowledgement: [
    "Missing durable acknowledgement",
    "缺少持久化确认",
  ],
  supervisor_failed: ["Supervisor failed", "监督进程失败"],
  lease_renewal_failed: ["Lease renewal failed", "租约续期失败"],
  scope_discovery_failed: ["Scope discovery failed", "Scope 发现失败"],
  processing_failed: ["Processing failed", "处理失败"],
  degraded: ["Degraded", "部分可用"],
  not_ready: ["Not ready", "未就绪"],
  configured: ["Configured", "已配置"],
  unconfigured: ["Unconfigured", "未配置"],
  unknown: ["Unknown", "未知"],
  local: ["Inside this Server", "此 Server 内"],
  external: ["External", "外部"],
  none: ["None", "无"],
  leader: ["Leader", "主节点"],
  standby: ["Standby", "备用节点"],
  running: ["Running", "运行中"],
  stopped: ["Stopped", "已停止"],
  observed: ["Outcome recorded", "已记录结果"],
  unverified: ["No outcome observed", "尚无观察结果"],
  supported: ["Supported", "支持"],
  unsupported: ["Unsupported", "不支持"],
  installed: ["Registered", "已注册"],
  not_installed: ["Not registered", "未注册"],
  invalid: ["Invalid", "无效"],
  current: ["Current", "有效"],
  stale: ["Out of date", "已过期"],
  missing_executable: ["Executable missing", "缺少可执行文件"],
  present: ["Present", "存在"],
  owned: ["Owned by PowerContext", "属于 PowerContext"],
  foreign: ["Foreign or unowned", "不属于此安装"],
  not_loaded: ["Not loaded", "未加载"],
  active: ["Active", "活动"],
  inactive: ["Inactive", "未运行"],
  failed: ["Failed", "失败"],
  live: ["Responding", "有响应"],
  unreachable: ["Unreachable", "无法访问"],
  valid: ["Valid", "有效"],
  missing_or_invalid: ["Missing or invalid", "缺失或无效"],
  manual_stopped: ["Stopped manually", "已手动停止"],
  start_failed: ["Start failed", "启动失败"],
  configuration_switching: ["Changing configuration", "正在切换配置"],
  manual_switching: ["Manual switch in progress", "正在手动切换"],
  runtime_repair: ["Runtime repair", "运行时修复"],
  stopping: ["Stopping", "正在停止"],
  switching: ["Switching", "正在切换"],
  database_ready: ["Database prepared", "数据库已准备"],
  accepted: ["Accepted", "已受理"],
  registration_dispatching: [
    "Dispatching service registration",
    "正在请求注册服务",
  ],
  service: ["Service operation", "服务操作"],
  preparing: ["Preparing", "正在准备"],
  prepared: ["Prepared", "已准备"],
  publishing: ["Publishing resources", "正在发布资源"],
  resources_published: ["Resources published", "资源已发布"],
  configuration_prepared: ["Configuration prepared", "配置已准备"],
  registering: ["Registering service", "正在注册服务"],
  complete: ["Complete", "已完成"],
};

function label(t: Text, value: string | null | undefined) {
  return t(...(statuses[value ?? "unknown"] ?? statuses.unknown));
}

function Time({ value, t }: { value: number | string; t: Text }) {
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (!Number.isFinite(date.getTime())) return t("Unknown time", "时间未知");
  const utc = date.toISOString();
  return <time dateTime={utc}>{utc}</time>;
}

function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl>
      {rows.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Processing({
  capabilities,
  t,
}: {
  capabilities: RuntimeCapabilities;
  t: Text;
}) {
  const extraction = capabilities.extraction;
  const yesNo = (value?: boolean | null) =>
    value == null
      ? t("Unknown", "未知")
      : value
        ? t("Yes", "是")
        : t("No", "否");
  return (
    <>
      <Rows
        rows={[
          [
            t("Memory extraction available", "支持记忆提取"),
            yesNo(capabilities.memory_extraction),
          ],
          [
            t("Handoff generation available", "支持生成交接"),
            yesNo(capabilities.handoff_generation),
          ],
          [
            t("Search modes", "搜索模式"),
            capabilities.search_modes.length
              ? capabilities.search_modes
                  .map((mode) => label(t, mode))
                  .join(" / ")
              : t("None reported", "未报告"),
          ],
        ]}
      />
      <h4>{t("Memory extraction observations", "记忆提取观察")}</h4>
      <p>
        {t(
          "Covers this Server process and its child Memory workers since startup. External workers, per-Scope progress, backlog and ETA are outside this view.",
          "覆盖此 Server 进程自启动以来及其记忆子进程的观察。此处不覆盖外部执行器、单个 Scope 进度、积压或预计完成时间。",
        )}
      </p>
      {extraction ? (
        <>
          <Rows
            rows={[
              [
                t("Extraction configuration", "提取配置"),
                label(t, extraction.configuration),
              ],
              [
                t("Background worker location", "后台执行位置"),
                label(t, extraction.background.location),
              ],
              [
                t("Supervisor state", "监督进程状态"),
                label(t, extraction.background.state),
              ],
              [
                t("Supervisor role", "监督进程角色"),
                extraction.background.role
                  ? label(t, extraction.background.role)
                  : t("Not reported", "未报告"),
              ],
              [
                t("Automatic scheduling enabled", "启用自动调度"),
                yesNo(extraction.background.automatic_processing_enabled),
              ],
              [
                t("Observation window started", "观察窗口起点"),
                <Time value={extraction.observation.since} t={t} />,
              ],
              [
                t("Window coverage", "窗口内记录"),
                label(t, extraction.observation.status),
              ],
              [
                t("Last observed success", "最近观察到的成功"),
                extraction.observation.last_success_at ? (
                  <Time value={extraction.observation.last_success_at} t={t} />
                ) : (
                  t("None observed", "未观察到")
                ),
              ],
              [
                t("Last historical failure", "最近一次历史失败"),
                extraction.observation.last_failure ? (
                  <>
                    <Time
                      value={extraction.observation.last_failure.occurred_at}
                      t={t}
                    />
                    {" · "}
                    {label(t, extraction.observation.last_failure.stage)}
                    {" · "}
                    {label(t, extraction.observation.last_failure.code)}
                  </>
                ) : (
                  t("None observed", "未观察到")
                ),
              ],
            ]}
          />
          <p className="small">
            {t(
              "Configuration does not verify provider access. A later success does not prove an earlier failure recovered. No recorded failure is not a health verdict.",
              "已配置不代表可连接模型服务。后续成功不能证明先前故障已恢复；没有观察到失败也不能证明健康。",
            )}
          </p>
        </>
      ) : (
        <PageFeedback
          compact
          kind="empty"
          title={t(
            "This Server does not report extraction observations.",
            "此 Server 未提供记忆提取观察。",
          )}
        />
      )}
    </>
  );
}

type SampleState<T> =
  | { status: "idle" | "loading" }
  | { status: "done"; sample: RuntimeFact<T> }
  | { status: "error"; error: unknown };

function Sample<T>({
  state,
  language,
  t,
  render,
}: {
  state: SampleState<T>;
  language: Language;
  t: Text;
  render: (value: T) => ReactNode;
}) {
  if (state.status === "loading")
    return (
      <PageFeedback
        compact
        kind="loading"
        title={t("Checking…", "正在检查…")}
      />
    );
  if (state.status === "error")
    return <ErrorFeedback language={language} error={state.error} />;
  if (state.status !== "done") return null;
  const { sample } = state;
  return (
    <>
      <p className="small">
        {t("Desktop checked at (UTC):", "桌面端检查时间（UTC）：")}{" "}
        <Time value={sample.checkedAt} t={t} />
      </p>
      {sample.fact.value != null ? (
        render(sample.fact.value)
      ) : (
        <ErrorFeedback language={language} error={sample.fact.error} />
      )}
    </>
  );
}

function ServerRuntime({
  active,
  pending,
  language,
  t,
}: {
  active: ActiveView | null;
  pending: boolean;
  language: Language;
  t: Text;
}) {
  const [liveness, setLiveness] = useState<SampleState<HealthResponse>>({
    status: "idle",
  });
  const [capabilities, setCapabilities] = useState<
    SampleState<RuntimeCapabilities>
  >({ status: "idle" });
  const [readiness, setReadiness] = useState<SampleState<ReadinessStatus>>({
    status: "idle",
  });
  const [running, setRunning] = useState<"observe" | "probe">();
  const [cancelling, setCancelling] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const inFlight = useRef(false);
  const cancelRequested = useRef(false);
  const current = useRef(0);
  const trigger = useRef<HTMLElement | null>(null);
  const generation = active?.generation;
  useEffect(
    () => () => {
      current.current++;
      if (generation != null && inFlight.current)
        void desktopApi.cancelRuntime(generation).catch(() => {});
    },
    [generation],
  );

  async function run(action: "observe" | "probe", button: HTMLButtonElement) {
    if (generation == null || pending || inFlight.current) return;
    inFlight.current = true;
    trigger.current = button;
    cancelRequested.current = false;
    const request = ++current.current;
    setRunning(action);
    setCancelled(false);
    async function collect<T>(
      read: () => Promise<RuntimeFact<T>>,
      receive: (state: SampleState<T>) => void,
    ) {
      receive({ status: "loading" });
      try {
        const sample = await read();
        if (request === current.current && !cancelRequested.current)
          receive({ status: "done", sample });
      } catch (error) {
        if (request === current.current && !cancelRequested.current)
          receive({ status: "error", error });
      }
    }
    try {
      if (action === "observe") {
        await Promise.all([
          collect(
            () => desktopApi.observeRuntimeLiveness(generation),
            setLiveness,
          ),
          collect(
            () => desktopApi.observeRuntimeCapabilities(generation),
            setCapabilities,
          ),
        ]);
      } else {
        await collect(
          () => desktopApi.probeRuntimeReadiness(generation),
          setReadiness,
        );
      }
    } finally {
      if (request === current.current) {
        inFlight.current = false;
        setRunning(undefined);
        setCancelling(false);
        setCancelled(cancelRequested.current);
        if (cancelRequested.current) {
          setLiveness((state) =>
            state.status === "loading" ? { status: "idle" } : state,
          );
          setCapabilities((state) =>
            state.status === "loading" ? { status: "idle" } : state,
          );
          setReadiness((state) =>
            state.status === "loading" ? { status: "idle" } : state,
          );
        }
      }
    }
  }

  function cancel() {
    if (generation == null || !inFlight.current || cancelRequested.current)
      return;
    cancelRequested.current = true;
    setCancelling(true);
    trigger.current?.focus();
    void desktopApi.cancelRuntime(generation).catch(() => {});
  }

  return (
    <section className="card stack" aria-labelledby="connected-runtime-title">
      <h2 id="connected-runtime-title">
        {t("Connected Server", "当前连接的 Server")}
      </h2>
      <p>
        {t(
          "These are snapshots of the selected Server, whether local or remote. Refresh updates liveness and capabilities without probing models.",
          "这里显示当前选中的本机或远程 Server 快照。刷新只更新存活和能力，不探测模型。",
        )}
      </p>
      {pending || generation == null ? (
        <PageFeedback
          compact
          kind={pending ? "loading" : "empty"}
          title={
            pending
              ? t("Changing connection or Scope…", "正在切换连接或 Scope…")
              : t(
                  "Activate a connection to observe its Server.",
                  "请先启用连接以观察 Server。",
                )
          }
        />
      ) : (
        <>
          <div className="actions">
            <button
              aria-disabled={!!running}
              onClick={(event) => void run("observe", event.currentTarget)}
            >
              {t("Refresh Server observations", "刷新 Server 观察")}
            </button>
            {running && (
              <button aria-disabled={cancelling} onClick={cancel}>
                {t("Cancel Server request", "取消 Server 请求")}
              </button>
            )}
          </div>
          {cancelled && (
            <PageFeedback
              compact
              kind="success"
              title={t("Server request cancelled.", "已取消 Server 请求。")}
            />
          )}
          {liveness.status === "idle" &&
            capabilities.status === "idle" &&
            !cancelled && (
              <p>
                {t(
                  "Refresh to collect a Server snapshot.",
                  "刷新后可获取 Server 快照。",
                )}
              </p>
            )}
          <h3>{t("Process liveness", "进程存活")}</h3>
          <p className="small">
            {t(
              "Source: Server liveness endpoint. A response only confirms the API process answered.",
              "来源：Server 存活接口。有响应只代表 API 进程能应答。",
            )}
          </p>
          <Sample
            state={liveness}
            t={t}
            language={language}
            render={() => (
              <p>{t("API process responded.", "API 进程有响应。")}</p>
            )}
          />
          <h3>
            {t("Runtime capabilities and processing", "运行时能力和处理观察")}
          </h3>
          <p className="small">
            {t(
              "Source: Server capabilities endpoint. Requires Server observation access.",
              "来源：Server 能力接口。需要 Server 观察权限。",
            )}
          </p>
          <Sample
            state={capabilities}
            t={t}
            language={language}
            render={(value) => <Processing capabilities={value} t={t} />}
          />
          <h3>{t("Readiness probe", "就绪探测")}</h3>
          <p id="runtime-probe-description">
            {t(
              "This action calls the Server readiness endpoint. It may contact configured models or other dependencies. Results describe the last explicit probe only.",
              "此操作调用 Server 就绪接口，可能访问已配置的模型或其他依赖。结果只代表最近一次显式探测。",
            )}
          </p>
          {active?.report.supportedOperations.includes("get_readiness") ? (
            <div className="actions">
              <button
                aria-disabled={!!running}
                aria-describedby="runtime-probe-description"
                onClick={(event) => void run("probe", event.currentTarget)}
              >
                {t("Probe readiness", "探测就绪状态")}
              </button>
            </div>
          ) : (
            <PageFeedback
              compact
              kind="unavailable"
              title={t(
                "Readiness is not qualified for this connection.",
                "此连接未获得就绪探测资格。",
              )}
            />
          )}
          <Sample
            state={readiness}
            t={t}
            language={language}
            render={(value) => <p role="status">{label(t, value)}</p>}
          />
        </>
      )}
    </section>
  );
}

function LocalRuntime({ t, onManage }: { t: Text; onManage: () => void }) {
  const [result, setResult] = useState<{
    reply: OpsInspectionReply;
    at: number;
  }>();
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  async function inspect() {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    setResult(undefined);
    try {
      setResult({ reply: await opsApi.inspect(), at: Date.now() / 1000 });
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }
  const fields: [keyof OpsComponents, string][] = [
    ["support", t("Platform support", "平台支持")],
    ["registration", t("Service registration", "服务注册")],
    ["definition", t("Service definition", "服务定义")],
    ["managerOwnership", t("Service ownership", "服务归属")],
    ["manager", t("Service manager state", "服务管理器状态")],
    ["liveness", t("Local service response", "本机服务响应")],
    ["resources", t("Runtime executable", "运行时程序")],
    ["configuration", t("Local configuration", "本机配置")],
    ["maintenance", t("Reported maintenance phase", "报告的维护阶段")],
    ["installationStage", t("Reported installation stage", "报告的安装阶段")],
  ];
  return (
    <section className="card stack" aria-labelledby="local-runtime-title">
      <h2 id="local-runtime-title">{t("This computer", "此电脑")}</h2>
      <p>
        {t(
          "Source: local service manager and trusted Ops helper. These facts are independent of the selected connection and remain available when Server HTTP is down.",
          "来源：本机服务管理器和可信 Ops 助手。此处与当前连接独立，Server HTTP 不可用时也可以检查。",
        )}
      </p>
      <div className="actions">
        <button aria-disabled={busy} onClick={() => void inspect()}>
          {t("Inspect local installation", "检查本机安装")}
        </button>
        <button onClick={onManage}>
          {t("Open Local Server settings", "打开本机 Server 设置")}
        </button>
      </div>
      {busy && (
        <PageFeedback
          compact
          kind="loading"
          title={t("Inspecting this computer…", "正在检查此电脑…")}
        />
      )}
      {result && (
        <p className="small">
          {t("Desktop checked at (UTC):", "桌面端检查时间（UTC）：")}{" "}
          <Time value={result.at} t={t} />
        </p>
      )}
      {result?.reply.components && (
        <Rows
          rows={fields.map(([field, name]) => [
            name,
            label(t, result.reply.components?.[field]),
          ])}
        />
      )}
      {(failed || (result && result.reply.outcome !== "succeeded")) && (
        <PageFeedback
          compact
          kind="unavailable"
          title={t(
            "Local inspection is unavailable or incomplete. Check Local Server settings for setup and recovery.",
            "本机检查不可用或不完整。请前往本机 Server 设置进行配置或恢复。",
          )}
        />
      )}
    </section>
  );
}

export function Runtime({
  active,
  contextPending,
  language,
  onManage,
}: {
  active: ActiveView | null;
  contextPending: boolean;
  language: Language;
  onManage: () => void;
}) {
  const t: Text = (en, zh) => (language === "zh" ? zh : en);
  return (
    <div className="stack">
      <ServerRuntime
        key={`${active?.generation ?? "none"}-${contextPending}`}
        active={active}
        pending={contextPending}
        language={language}
        t={t}
      />
      <LocalRuntime
        key={`ops-${active?.generation ?? "none"}-${contextPending}`}
        t={t}
        onManage={onManage}
      />
      <Diagnostics
        key={`cli-${active?.generation ?? "none"}-${contextPending}`}
        language={language}
        serviceOnly
      />
    </div>
  );
}
