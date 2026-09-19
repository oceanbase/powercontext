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

import { useState } from "react";
import { desktopApi } from "../shared/ipc";
import type { DiagnosticKind, DiagnosticReport } from "../generated/ipc";
import type { Language } from "./messages";
import { connectionError } from "./connection-messages";
const labels: Record<string, [string, string]> = {
  support: ["平台支持", "Platform support"],
  registration: ["服务注册", "Service registration"],
  definition: ["服务定义", "Service definition"],
  manager_ownership: ["服务归属", "Service ownership"],
  manager: ["服务管理器", "Service manager"],
  server_liveness: ["本机服务响应", "Local service response"],
  integrations: ["集成检查", "Integration checks"],
  supported: ["支持", "Supported"],
  unsupported: ["不支持", "Unsupported"],
  installed: ["已注册", "Installed"],
  not_installed: ["未注册", "Not installed"],
  invalid: ["无效", "Invalid"],
  unknown: ["未知", "Unknown"],
  current: ["当前有效", "Current"],
  stale: ["需要更新", "Stale"],
  missing_executable: ["程序缺失", "Missing executable"],
  not_loaded: ["未加载", "Not loaded"],
  owned: ["属于 PowerContext", "Owned by PowerContext"],
  foreign: ["不属于 PowerContext", "Foreign"],
  active: ["运行中", "Active"],
  inactive: ["未运行", "Inactive"],
  failed: ["失败", "Failed"],
  live: ["有响应", "Live"],
  unreachable: ["无法访问", "Unreachable"],
  ok: ["通过", "Passed"],
  degraded: ["部分可用", "Degraded"],
  skipped: ["未检查", "Skipped"],
  present: ["已发现", "Present"],
  missing: ["未发现", "Missing"],
  plugin: ["插件", "Plugin"],
  package: ["扩展包", "Package"],
  skill: ["技能", "Skill"],
  settings: ["配置", "Settings"],
  transport: ["通信配置", "Transport configuration"],
  hooks: ["事件钩子", "Hooks"],
  mcp: ["MCP", "MCP"],
};
export function Diagnostics({ language }: { language: Language }) {
  const zh = language === "zh";
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<DiagnosticReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [kind, setKind] = useState<DiagnosticKind | null>(null);
  const label = (value: string) => labels[value]?.[zh ? 0 : 1] ?? value;
  async function run(next: DiagnosticKind) {
    setBusy(true);
    setError(null);
    setReport(null);
    setKind(next);
    try {
      setReport(await desktopApi.diagnostics(next));
    } catch (error) {
      setError(error);
    } finally {
      setBusy(false);
    }
  }
  const code = typeof error === "string" ? error : null;
  return (
    <section className="card stack">
      <h2>{zh ? "本地诊断" : "Local diagnostics"}</h2>
      <p>
        {zh
          ? "仅检查此电脑，与活动连接的远程状态分开。集成检查可能启动短暂的 Agent 辅助进程；不代表实际记忆写入或读取已通过。"
          : "Checks this computer independently of the active remote connection. Integration checks may start temporary Agent helper processes; they do not prove actual memory capture or recall."}
      </p>
      <p className="small">
        {zh
          ? "需要先按使用文档登记可信的本机 CLI。缺少 CLI 不影响远程业务。"
          : "Register a trusted local CLI using the usage guide first. Missing CLI diagnostics do not block remote operations."}
      </p>
      <div className="actions">
        <button disabled={busy} onClick={() => void run("service")}>
          {zh ? "检查本机服务" : "Check local service"}
        </button>
        <button disabled={busy} onClick={() => void run("integrations")}>
          {zh ? "检查本机 Agent 集成" : "Check local Agent integrations"}
        </button>
      </div>
      {busy && (
        <p role="status">
          {zh ? "正在检查本机，请稍候…" : "Checking this computer…"}
        </p>
      )}
      {error != null && (
        <p role="alert">
          {code === "not_found"
            ? zh
              ? "未找到已登记的本机 CLI；远程连接不受影响。"
              : "No registered local CLI was found; remote connections are unaffected."
            : code === "compatibility_unverified"
              ? zh
                ? "本机 CLI 的摘要或版本未通过核验，请重新核对登记信息。"
                : "The local CLI digest or version did not pass verification. Check its registration."
              : connectionError(error, language)}
        </p>
      )}
      {report && (
        <div>
          <h3>
            {kind === "service"
              ? zh
                ? "本机服务结果"
                : "Local service result"
              : zh
                ? "本机集成结果"
                : "Local integration result"}
          </h3>
          <p>
            {new Date(report.checkedAt * 1000).toLocaleString(
              zh ? "zh-CN" : "en-US",
            )}{" "}
            · {zh ? "退出码" : "Exit code"}: {report.exitCode}
          </p>
          <dl>
            {report.items.map((item) => (
              <div key={item.field}>
                <dt>{label(item.field)}</dt>
                <dd>{label(item.status)}</dd>
              </div>
            ))}
          </dl>
          {report.hosts.map((host) => (
            <section key={host.host}>
              <h4>
                {host.host} · {label(host.presence)}
              </h4>
              <dl>
                {host.checks.map((item) => (
                  <div key={item.field}>
                    <dt>{label(item.field)}</dt>
                    <dd>{label(item.status)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}
