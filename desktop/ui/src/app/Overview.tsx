/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { useEffect, useMemo, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import type {
  Authentication,
  CheckReport,
  DesktopState,
  ExactScopeStatistics,
  ProfileView,
  ScopeDescriptor,
  StatsPeriod,
  StorageChoice,
} from "../generated/ipc";
import { contractSha256 } from "../generated/operations";
import { desktopApi } from "../shared/ipc";
import { messages, type Language } from "./messages";
import { activeCheckReport } from "./context";
import { connectionError, connectionMessages } from "./connection-messages";
import { PageFeedback } from "@/components/ui/feedback";
import {
  Dialog,
  DialogBody,
  DialogCloseButton,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  previewDataScopeId,
  previewEmptyScopeId,
  previewLocalConnectionId,
  previewWorkspaceMemoryCount,
} from "./preview-data";
import { Button } from "@/components/ui/button";
import { PageActionButton } from "@/components/ui/page-action-button";
import { ActionNotification } from "@/components/ui/action-notification";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Bot,
  CircleCheck,
  Check,
  Copy,
  Eye,
  EyeOff,
  Laptop,
  Plug,
} from "lucide-react";
import { AgentConnectionMenu } from "./AgentConnectionMenu";
import localAgentIllustration from "../assets/local-agent-gradient-c.png";

type Props = {
  state: DesktopState | null;
  language: Language;
  onState?: (state: DesktopState) => boolean | void;
  onOpenLocalServer: () => void;
  contextPending: boolean;
  preview?: boolean;
};

function AgentSetupIllustration() {
  return (
    <img
      src={localAgentIllustration}
      alt=""
      width="160"
      height="160"
      draggable="false"
    />
  );
}

export type AgentBinding = {
  agent: string;
  serviceId: string;
  scopeId: string;
};

function AgentConnectionGuide({
  language,
  agent,
  onConnect,
  onManage,
  onCaptureSaved,
  onDeleted,
}: {
  language: Language;
  agent?: string;
  onConnect: () => void;
  onManage: () => void;
  onCaptureSaved: () => void;
  onDeleted: () => void;
}) {
  const t = copy[language];
  const [hiddenAgents, setHiddenAgents] = useState<string[]>([]);
  const [agentsNotCapturing, setAgentsNotCapturing] = useState<string[]>([]);
  if (agent) {
    const connectedAgents = [
      {
        name: "Codex",
        project: "PowerContext Desktop",
        lastWrite:
          language === "zh" ? "最近写入 10 分钟前" : "Last write 10m ago",
      },
      {
        name: "Cursor",
        project: "个人知识库",
        lastWrite:
          language === "zh" ? "最近写入 1 小时前" : "Last write 1h ago",
      },
    ];
    return (
      <section
        className="agent-connected-guide"
        aria-labelledby="agent-connected-title"
      >
        <header className="agent-connected-heading">
          <h2 id="agent-connected-title">{t.connectedLocalAgents}</h2>
        </header>
        <ul className="connected-agent-list">
          {connectedAgents
            .filter((item) => !hiddenAgents.includes(item.name))
            .map((item) => (
              <li key={item.name}>
                <span className="connected-agent-identity">
                  <span className="connected-agent-name">
                    <span className="connected-agent-name-line">
                      <strong>{item.name}</strong>
                      <span className="connected-agent-status connected-agent-detail">
                        <span className="status-dot success" />
                        {t.connectedAgent}
                        {agentsNotCapturing.includes(item.name) &&
                          (language === "zh"
                            ? " | 未采集消息"
                            : " | Messages not captured")}
                      </span>
                    </span>
                    <span className="connected-agent-project connected-agent-detail">
                      {t.boundProject}：{item.project}
                    </span>
                  </span>
                </span>
                <span className="connected-agent-basic-info">
                  <span className="connected-agent-time connected-agent-detail">
                    {item.lastWrite}
                  </span>
                </span>
                <AgentConnectionMenu
                  agent={item.name}
                  language={language}
                  onChangeBinding={onManage}
                  onDelete={() =>
                    setHiddenAgents((current) => [...current, item.name])
                  }
                  onDeleted={onDeleted}
                  onCaptureChange={(enabled) =>
                    setAgentsNotCapturing((current) =>
                      enabled
                        ? current.filter((name) => name !== item.name)
                        : [...new Set([...current, item.name])],
                    )
                  }
                  onCaptureSaved={onCaptureSaved}
                />
              </li>
            ))}
        </ul>
      </section>
    );
  }
  return (
    <section className="agent-setup-guide" aria-labelledby="agent-setup-title">
      <div className="agent-setup-mark" aria-hidden="true">
        <AgentSetupIllustration />
      </div>
      <div className="agent-setup-copy">
        <h2 id="agent-setup-title">{t.agentSetupTitle}</h2>
        <p>{t.agentSetupHint}</p>
        <Button size="sm" onClick={onConnect}>
          {t.configureAgent}
        </Button>
      </div>
    </section>
  );
}

export function ConnectAgentDialog({
  state,
  language,
  onState,
  onClose,
  onConnected,
  initialServiceId,
  profilesOverride,
  mode = "connect",
}: Pick<Props, "state" | "language" | "onState"> & {
  onClose: () => void;
  onConnected: (binding: AgentBinding) => void;
  initialServiceId?: string;
  profilesOverride?: ProfileView[];
  mode?: "connect" | "change";
}) {
  const t = copy[language];
  const profiles = profilesOverride ?? state?.profiles ?? [];
  const active = state?.active;
  // Native Agent discovery lands with the provisioning IPC. Keep release builds
  // honest until then; the desktop development preview exposes Codex for review.
  const agents = isTauri() && import.meta.env.DEV ? ["Codex"] : [];
  const [agent, setAgent] = useState(agents[0] ?? "");
  const [serviceId, setServiceId] = useState(
    initialServiceId ?? active?.connectionId ?? "",
  );
  const [spaces, setSpaces] = useState<ScopeDescriptor[]>([]);
  const [scopeId, setScopeId] = useState(active?.scope?.scope_id ?? "");
  const [copied, setCopied] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [loadingSpaces, setLoadingSpaces] = useState(false);
  const [authorizing, setAuthorizing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const selectedProfile = profiles.find((profile) => profile.id === serviceId);
  const selectedScope = spaces.find((space) => space.scope_id === scopeId);
  const dialogTitle =
    mode === "change" ? t.changeAgentDialog : t.connectAgentDialog;
  const configurationPrompt =
    mode === "change"
      ? language === "zh"
        ? `请更新当前 ${agent || "Agent"} 的 PowerContext 配置：\n- 服务地址：${selectedProfile?.endpoint ?? ""}\n- 记忆空间：${selectedScope?.title ?? ""}\n- 空间 ID：${selectedScope?.scope_id ?? ""}\n\n请确认 PowerContext 插件已安装；如未安装，请先完成安装。配置完成后请告诉我结果，不要在回复中输出任何访问凭据。`
        : `Update the PowerContext configuration for the current ${agent || "Agent"}:\n- Service address: ${selectedProfile?.endpoint ?? ""}\n- Memory space: ${selectedScope?.title ?? ""}\n- Space ID: ${selectedScope?.scope_id ?? ""}\n\nConfirm that the PowerContext plugin is installed, and install it first if needed. Report the result when configuration is complete, without exposing any access credentials.`
      : language === "zh"
        ? `请为 ${agent || "Agent"} 安装 PowerContext 插件，并配置服务地址 ${selectedProfile?.endpoint ?? ""}、记忆空间 ${selectedScope?.title ?? ""}。完成后回到 PowerContext Desktop 授权。`
        : `Install the PowerContext plugin for ${agent || "your Agent"}, configure service ${selectedProfile?.endpoint ?? ""} and memory space ${selectedScope?.title ?? ""}, then return to PowerContext Desktop to authorize it.`;
  const visiblePrompt =
    mode === "change"
      ? configurationPrompt
      : language === "zh"
        ? `为 ${agent || "Agent"} 安装 PowerContext 插件，完成后回到 Desktop 授权。`
        : `Install the PowerContext plugin for ${agent || "your Agent"}, then return to Desktop to authorize it.`;
  const fieldErrors = {
    service: submitted && !serviceId,
    scope: submitted && !scopeId,
  };

  useEffect(() => {
    if (!active || active.connectionId !== serviceId) return;
    if (
      isTauri() &&
      import.meta.env.DEV &&
      active.connectionId.startsWith("preview-")
    ) {
      setSpaces(active.scope ? [active.scope] : []);
      return;
    }
    let cancelled = false;
    setLoadingSpaces(true);
    void (async () => {
      const [defaultResult, listResult] = await Promise.allSettled([
        desktopApi.defaultScope(active.generation),
        (async () => {
          const items: ScopeDescriptor[] = [];
          let cursor: string | null = null;
          do {
            const page = await desktopApi.scopes(active.generation, "", cursor);
            items.push(...page.items);
            cursor = page.next_cursor ?? null;
          } while (cursor && !cancelled);
          return items;
        })(),
      ]);
      const items = listResult.status === "fulfilled" ? listResult.value : [];
      const defaultScope =
        defaultResult.status === "fulfilled" ? defaultResult.value : null;
      if (defaultResult.status === "fulfilled") {
        items.unshift(defaultResult.value);
      }
      if (items.length === 0 && listResult.status === "rejected") {
        throw listResult.reason;
      }
      return { items, defaultScopeId: defaultScope?.scope_id ?? "" };
    })()
      .then(({ items, defaultScopeId }) => {
        if (cancelled) return;
        const current = active.scope ? [active.scope] : [];
        const next = [...current, ...items].filter(
          (scope, index, all) =>
            all.findIndex((item) => item.scope_id === scope.scope_id) === index,
        );
        setSpaces(next);
        setScopeId((currentScope) =>
          currentScope && next.some((space) => space.scope_id === currentScope)
            ? currentScope
            : defaultScopeId || next[0]?.scope_id || "",
        );
      })
      .catch((cause) => {
        if (!cancelled) setError(cause);
      })
      .finally(() => {
        if (!cancelled) setLoadingSpaces(false);
      });
    return () => {
      cancelled = true;
      void desktopApi.cancelScopes(active.generation).catch(() => {});
    };
  }, [active, serviceId]);

  async function selectService(nextId: string) {
    if (nextId === serviceId || loadingSpaces) return;
    setServiceId(nextId);
    setScopeId("");
    setSpaces([]);
    setSubmitted(false);
    setError(null);
    if (nextId === active?.connectionId) return;
    setLoadingSpaces(true);
    try {
      const next = await desktopApi.check(nextId, true);
      onState?.(next);
    } catch (cause) {
      setError(cause);
    } finally {
      setLoadingSpaces(false);
    }
  }

  async function copyPrompt() {
    await navigator.clipboard?.writeText(configurationPrompt);
    setCopied(true);
    globalThis.setTimeout(() => setCopied(false), 1800);
  }

  async function authorize() {
    if (!agent || !serviceId || !scopeId) {
      setSubmitted(true);
      return;
    }
    setAuthorizing(true);
    setError(null);
    try {
      // The native provisioning command owns credential storage. This preview
      // preserves the same pending/success/error interaction without exposing it.
      await new Promise((resolve) => globalThis.setTimeout(resolve, 480));
      onConnected({ agent, serviceId, scopeId });
      onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setAuthorizing(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form
          className="agent-connect-form contents"
          onSubmit={(event) => {
            event.preventDefault();
            void authorize();
          }}
        >
          <DialogHeader>
            <DialogTitle>{dialogTitle}</DialogTitle>
            <DialogCloseButton label={t.closeDialog} />
          </DialogHeader>
          {agents.length === 0 ? (
            <DialogBody className="agent-detection-empty">
              <Bot aria-hidden="true" />
              <strong>{t.noLocalAgent}</strong>
              <p>{t.noLocalAgentHint}</p>
            </DialogBody>
          ) : (
            <>
              <DialogBody>
                <fieldset
                  className="form-fields"
                  disabled={authorizing || loadingSpaces}
                >
                  <div className="form-field">
                    <label htmlFor="agent-connection-agent">
                      {t.localAgent}
                    </label>
                    {mode === "change" ? (
                      <Input
                        id="agent-connection-agent"
                        value={agent}
                        readOnly
                        disabled
                        aria-readonly="true"
                      />
                    ) : (
                      <Select value={agent} onValueChange={setAgent}>
                        <SelectTrigger id="agent-connection-agent">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {agents.map((item) => (
                            <SelectItem key={item} value={item}>
                              {item}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </div>
                  <div className="form-field">
                    <label htmlFor="agent-connection-service">
                      {t.service}
                    </label>
                    <Select
                      value={serviceId}
                      onValueChange={(value) => void selectService(value)}
                    >
                      <SelectTrigger
                        id="agent-connection-service"
                        aria-invalid={
                          fieldErrors.service || error != null || undefined
                        }
                        aria-describedby={
                          error != null
                            ? "agent-connection-service-error"
                            : undefined
                        }
                      >
                        <SelectValue placeholder={t.selectService} />
                      </SelectTrigger>
                      <SelectContent>
                        {profiles.map((profile) => (
                          <SelectItem key={profile.id} value={profile.id}>
                            {profile.name} · {profile.endpoint}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {error != null && (
                      <FieldError
                        id="agent-connection-service-error"
                        role="status"
                      >
                        {connectionError(error, language)}
                      </FieldError>
                    )}
                  </div>
                  <div className="form-field">
                    <label htmlFor="agent-connection-space">{t.space}</label>
                    <Select value={scopeId} onValueChange={setScopeId}>
                      <SelectTrigger
                        id="agent-connection-space"
                        aria-invalid={fieldErrors.scope || undefined}
                      >
                        <SelectValue
                          placeholder={
                            loadingSpaces ? t.loading : t.selectSpace
                          }
                        />
                      </SelectTrigger>
                      <SelectContent empty={spaces.length === 0}>
                        {spaces.length > 0 ? (
                          spaces.map((space) => (
                            <SelectItem
                              key={space.scope_id}
                              value={space.scope_id}
                            >
                              {space.title}
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem
                            variant="empty"
                            value="__no-memory-spaces__"
                            disabled
                          >
                            {t.noSpaces}
                          </SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                    {fieldErrors.scope && (
                      <FieldError>{t.selectSpace}</FieldError>
                    )}
                  </div>
                </fieldset>
                <section
                  className="agent-install-prompt"
                  aria-labelledby="install-prompt-title"
                >
                  <div>
                    <h3 id="install-prompt-title">
                      {mode === "change" ? t.changePrompt : t.installPrompt}
                    </h3>
                    <p>
                      {mode === "change"
                        ? t.changePromptHint
                        : t.installPromptHint}
                    </p>
                  </div>
                  <code>{visiblePrompt}</code>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="justify-self-start"
                    onClick={() => void copyPrompt()}
                  >
                    {copied ? (
                      <Check aria-hidden="true" />
                    ) : (
                      <Copy aria-hidden="true" />
                    )}
                    {copied ? t.copiedPrompt : t.copyPrompt}
                  </Button>
                </section>
              </DialogBody>
              <DialogFooter>
                <Button
                  variant="outline"
                  type="button"
                  onClick={onClose}
                  disabled={authorizing}
                >
                  {t.cancel}
                </Button>
                <Button type="submit" disabled={authorizing || loadingSpaces}>
                  {authorizing ? t.authorizing : t.completeSetup}
                </Button>
              </DialogFooter>
            </>
          )}
        </form>
      </DialogContent>
    </Dialog>
  );
}

const copy = {
  zh: {
    title: "空间概览",
    currentSpace: "当前空间",
    overview: "使用成效",
    period7: "最近7天",
    period30: "最近30天",
    period: "统计周期",
    token: "Token 节约",
    recall: "召回命中",
    activeDays: "活跃天数",
    activity: "活跃情况",
    activityHint: "颜色越深，表示当天返回的上下文越多。",
    activityQuiet: "不活跃",
    activityActive: "活跃",
    agents: "Agent 使用情况",
    agentHint:
      "仅展示配置在当前空间下的 Agent。切换浏览空间不会修改 Agent 绑定。",
    noAgents: "当前空间暂无可展示的 Agent 活动",
    noAgentsHint: "完成 Agent 接入并开始新会话后，读取和写入活动会显示在这里。",
    connectAgent: "接入 Agent",
    agentSetupTitle: "尚未连接本地 Agent",
    agentSetupHint: "配置一个 Agent 后，它会在当前空间开始读取和写入记忆。",
    configureAgent: "连接本地 Agent",
    connectedAgent: "已连接",
    connectedLocalAgents: "已连接的本地 Agent",
    connectLocalAgent: "连接本地Agent",
    boundProject: "项目",
    manageAgent: "管理连接",
    connectAgentDialog: "连接本地 Agent",
    changeAgentDialog: "更改服务与空间",
    localAgent: "本机 Agent",
    service: "服务地址",
    space: "记忆空间",
    installPrompt: "复制提示词",
    installPromptHint:
      "这是一段安装 PowerContext 插件的提示词，把这一段提示词发给 Agent，确认完成安装后，再点击授权服务凭据。",
    changePrompt: "复制配置提示词",
    changePromptHint:
      "复制提示词并发送给当前 Agent，指导它更新 PowerContext 配置，绑定上方选择的服务与记忆空间。完成后再授权服务凭据。",
    copyPrompt: "复制提示词",
    copiedPrompt: "已复制",
    completeSetup: "授权服务凭据",
    authorizing: "正在授权…",
    noLocalAgent: "未检测到可用 Agent",
    noLocalAgentHint: "请先在本机安装受支持的 Agent，随后重新打开此窗口。",
    selectSpace: "请选择记忆空间",
    noSpaces: "暂无记忆空间",
    selectService: "请选择服务地址",
    cancel: "取消",
    closeDialog: "关闭弹窗",
    memory: "记忆概况",
    active: "使用中",
    inactive: "已停用",
    loading: "正在读取当前空间的统计…",
    initial: "读取统计后，可查看当前空间的使用价值和活跃情况。",
    unavailable:
      "当前服务暂不支持空间统计。Agent 接入和记忆功能仍可按实际能力使用。",
    noActivity: "当前周期还没有使用记录",
  },
  en: {
    title: "Space overview",
    currentSpace: "Current space",
    overview: "Usage performance",
    period7: "Last 7 days",
    period30: "Last 30 days",
    period: "Statistics period",
    token: "Token reduction",
    recall: "Recall hits",
    activeDays: "Active days",
    activity: "Activity",
    activityHint: "Darker cells indicate more context returned that day.",
    activityQuiet: "Less active",
    activityActive: "More active",
    agents: "Agent usage",
    agentHint:
      "Only Agents configured for this Scope appear here. Browsing another Scope never changes Agent bindings.",
    noAgents: "No Agent activity is available for this Scope yet",
    noAgentsHint:
      "After connecting an Agent and starting a new session, read and write activity will appear here.",
    connectAgent: "Connect Agent",
    agentSetupTitle: "No local Agent connected",
    agentSetupHint:
      "Configure an Agent to start reading and writing memory in this Space.",
    configureAgent: "Connect local Agent",
    connectedAgent: "Connected",
    connectedLocalAgents: "Connected local Agents",
    connectLocalAgent: "Connect local Agent",
    boundProject: "Project",
    manageAgent: "Manage connection",
    connectAgentDialog: "Connect local Agent",
    changeAgentDialog: "Change service and memory space",
    localAgent: "Local Agent",
    service: "Service address",
    space: "Memory space",
    installPrompt: "PowerContext plugin installation prompt",
    installPromptHint:
      "Copy this prompt to your Agent. After installation is complete, authorize service credentials here.",
    changePrompt: "Copy configuration prompt",
    changePromptHint:
      "Send this prompt to the current Agent to update PowerContext and bind the selected service and memory space. Then authorize service credentials here.",
    copyPrompt: "Copy prompt",
    copiedPrompt: "Copied",
    completeSetup: "Authorize service credentials",
    authorizing: "Authorizing…",
    noLocalAgent: "No local Agent detected",
    noLocalAgentHint:
      "Install a supported Agent on this computer, then reopen this window.",
    selectSpace: "Select a memory space",
    noSpaces: "No memory spaces",
    selectService: "Select a service address",
    cancel: "Cancel",
    closeDialog: "Close dialog",
    memory: "Memory overview",
    active: "Active",
    inactive: "Inactive",
    loading: "Loading statistics for this Scope…",
    initial: "Load statistics to see value and activity for this Scope.",
    unavailable:
      "This service does not currently support Space statistics. Agent connection and memory features remain available according to actual capabilities.",
    noActivity: "No usage has been recorded in this period",
  },
} as const;

export function Overview(props: Props) {
  if (!props.state) return null;
  if (!props.state.active?.scope) return <FirstRunHome {...props} />;
  return <UsageDashboard {...props} />;
}

const firstRunCopy = {
  zh: {
    title: "你已经有可用的 PowerContext 服务了吗?",
    intro: "根据你的实际情况，选择正确的下一步",
    localTitle: "没有服务",
    localHint: "需要先安装 PowerContext 服务。",
    existingTitle: "已有服务",
    existingHint: "连接已经在运行的 PowerContext 服务",
  },
  en: {
    title: "Do you already have a service?",
    intro:
      "Tell us where you are starting, and we will guide you to the right next step.",
    localTitle: "Install on this computer",
    localHint:
      "Check this device, then complete installation, setup, and startup.",
    existingTitle: "Connect an existing service",
    existingHint:
      "Connect a local or remote service using its connection details and access token.",
  },
} as const;

function FirstRunHome(props: Props) {
  const t = firstRunCopy[props.language];
  const [connectOpen, setConnectOpen] = useState(false);
  return (
    <>
      <div className="first-run-home" aria-labelledby="first-run-title">
        <header className="first-run-hero">
          <h1 id="first-run-title">{t.title}</h1>
          <p>{t.intro}</p>
        </header>
        <div className="first-run-choices">
          <Button
            variant="outline"
            className="first-run-choice"
            onClick={props.onOpenLocalServer}
          >
            <Laptop aria-hidden="true" />
            <strong>{t.localTitle}</strong>
            <span>{t.localHint}</span>
          </Button>
          <Button
            variant="outline"
            className="first-run-choice"
            onClick={() => setConnectOpen(true)}
          >
            <Plug aria-hidden="true" />
            <strong>{t.existingTitle}</strong>
            <span>{t.existingHint}</span>
          </Button>
        </div>
      </div>
      {connectOpen && (
        <QuickConnectDialog {...props} onClose={() => setConnectOpen(false)} />
      )}
    </>
  );
}

export function QuickConnectDialog({
  state,
  language,
  onState,
  preview = false,
  onClose,
  title,
}: Pick<Props, "state" | "language" | "onState" | "preview"> & {
  onClose: () => void;
  title?: string;
}) {
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("http://127.0.0.1:8000");
  const [authentication, setAuthentication] =
    useState<Authentication>("bearer");
  const [compatibility, setCompatibility] = useState("");
  const [credential, setCredential] = useState("");
  const [storage, setStorage] = useState<StorageChoice>("persistent");
  const [savedProfile, setSavedProfile] = useState<ProfileView | null>(null);
  const [credentialVisible, setCredentialVisible] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const zh = language === "zh";
  const t = connectionMessages[language];
  const dialogTitle = title ?? (zh ? "连接服务" : "Connect service");
  const duplicate = state?.profiles.some(
    (profile) =>
      profile.id !== savedProfile?.id && profile.name.trim() === name.trim(),
  );
  const requiresCredential = authentication === "bearer";
  const profiles =
    state?.compatibilityProfiles.filter(
      (profile) =>
        profile.contractSha256 === contractSha256 ||
        (requiresCredential &&
          profile.authenticatedClients?.includes(contractSha256)),
    ) ?? [];
  const qualified = profiles.some((profile) => profile.id === compatibility);
  const fieldErrors = {
    name: submitted && !name.trim(),
    endpoint: submitted && !endpoint.trim(),
    credential: submitted && requiresCredential && !credential.trim(),
    compatibility: submitted && !preview && !qualified,
  };

  async function connect() {
    if (busy) return;
    if (
      !name.trim() ||
      !endpoint.trim() ||
      (requiresCredential && !credential.trim()) ||
      (!preview && !qualified)
    ) {
      setSubmitted(true);
      setError(null);
      return;
    }
    setSubmitted(false);
    if (duplicate) {
      setError({ code: "duplicate_name" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (preview) {
        onState?.(
          demoConnectedState(
            name.trim(),
            endpoint.trim(),
            authentication,
            (state?.generation ?? 0) + 1,
          ),
        );
        onClose();
        return;
      }
      const saved = await desktopApi.save({
        id: savedProfile?.id ?? null,
        revision: savedProfile?.revision ?? null,
        name: name.trim(),
        endpoint: endpoint.trim(),
        authentication,
        caPem: null,
        compatibility,
        keepCredential: false,
        credential: requiresCredential ? { secret: credential, storage } : null,
      });
      const profile = saved.profiles.find(
        (candidate) => candidate.name === name.trim(),
      );
      if (!profile) throw { code: "not_found" };
      setSavedProfile(profile);
      onState?.(saved);
      const checked = await desktopApi.check(profile.id, true);
      onState?.(checked);
      if (
        checked.active?.connectionId !== profile.id ||
        !checked.active.report.compatibilityVerified
      )
        throw { code: "compatibility_unverified" };
      const defaultScope = await desktopApi.defaultScope(
        checked.active.generation,
      );
      const selected = await desktopApi.selectScope(
        checked.active.generation,
        defaultScope.scope_id,
      );
      onState?.(selected);
      onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form
          className="quick-connect-form contents"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <DialogHeader>
            <DialogTitle>{dialogTitle}</DialogTitle>
            <DialogCloseButton label={zh ? "关闭弹窗" : "Close dialog"} />
          </DialogHeader>
          <DialogBody>
            {error != null && (
              <PageFeedback
                compact
                kind="error"
                title={zh ? "连接失败" : "Connection failed"}
              >
                <p>{connectionError(error, language)}</p>
              </PageFeedback>
            )}
            <fieldset disabled={busy} className="form-fields">
              <Field>
                <FieldLabel>
                  {zh ? "连接名称" : "Connection name"}
                  <Input
                    autoFocus
                    value={name}
                    maxLength={128}
                    aria-invalid={fieldErrors.name || undefined}
                    aria-describedby={
                      fieldErrors.name ? "connection-name-error" : undefined
                    }
                    onChange={(event) => {
                      setName(event.target.value);
                      setError(null);
                    }}
                  />
                </FieldLabel>
                {fieldErrors.name && (
                  <FieldError id="connection-name-error">
                    {zh ? "请输入连接名称" : "Enter a connection name"}
                  </FieldError>
                )}
              </Field>
              <Field>
                <FieldLabel>
                  {zh ? "Server 地址" : "Server address"}
                  <Input
                    value={endpoint}
                    maxLength={2048}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="http://127.0.0.1:8000"
                    aria-invalid={fieldErrors.endpoint || undefined}
                    aria-describedby={
                      fieldErrors.endpoint
                        ? "connection-endpoint-error"
                        : undefined
                    }
                    onChange={(event) => {
                      setEndpoint(event.target.value);
                      setError(null);
                    }}
                  />
                </FieldLabel>
                {fieldErrors.endpoint && (
                  <FieldError id="connection-endpoint-error">
                    {zh ? "请输入 Server 地址" : "Enter a Server address"}
                  </FieldError>
                )}
                <FieldDescription>
                  {zh
                    ? "本机回环地址支持 HTTP，远程连接使用 HTTPS"
                    : "Loopback addresses allow HTTP; remote connections use HTTPS"}
                </FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="quick-connection-authentication">
                  {t.authentication}
                </FieldLabel>
                <Select
                  value={authentication}
                  onValueChange={(value) => {
                    setAuthentication(value as Authentication);
                    setCompatibility("");
                    setError(null);
                  }}
                >
                  <SelectTrigger id="quick-connection-authentication">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="bearer">{t.bearer}</SelectItem>
                    <SelectItem value="unauthenticated_loopback">
                      {t.anonymous}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              {!preview && (
                <Field>
                  <FieldLabel htmlFor="quick-connection-compatibility">
                    {t.compatibility}
                  </FieldLabel>
                  <Select
                    value={compatibility}
                    onValueChange={setCompatibility}
                  >
                    <SelectTrigger
                      id="quick-connection-compatibility"
                      aria-invalid={fieldErrors.compatibility || undefined}
                      aria-describedby="quick-connection-compatibility-help"
                    >
                      <SelectValue placeholder={t.compatibility} />
                    </SelectTrigger>
                    <SelectContent>
                      {profiles.map((profile) => (
                        <SelectItem key={profile.id} value={profile.id}>
                          {profile.id}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FieldDescription id="quick-connection-compatibility-help">
                    {t.compatibilityHint}
                  </FieldDescription>
                  {fieldErrors.compatibility && (
                    <FieldError>
                      {zh
                        ? "请选择已验证兼容配置"
                        : "Select a verified compatibility profile"}
                    </FieldError>
                  )}
                </Field>
              )}
              {requiresCredential && (
                <Field>
                  <FieldLabel>
                    {zh ? "凭据" : "Credential"}
                    <span className="password-field">
                      <Input
                        adornment="trailing"
                        type={credentialVisible ? "text" : "password"}
                        value={credential}
                        maxLength={2048}
                        autoComplete="new-password"
                        onChange={(event) => {
                          setCredential(event.target.value);
                          setError(null);
                        }}
                        aria-invalid={fieldErrors.credential || undefined}
                        aria-describedby={
                          fieldErrors.credential
                            ? "connection-credential-error"
                            : undefined
                        }
                      />
                      <Button
                        variant="field-icon"
                        size="icon-sm"
                        type="button"
                        className="password-toggle"
                        aria-label={
                          credentialVisible
                            ? zh
                              ? "隐藏凭据"
                              : "Hide credential"
                            : zh
                              ? "显示凭据"
                              : "Show credential"
                        }
                        aria-pressed={credentialVisible}
                        onClick={() =>
                          setCredentialVisible((visible) => !visible)
                        }
                      >
                        {credentialVisible ? (
                          <Eye aria-hidden="true" />
                        ) : (
                          <EyeOff aria-hidden="true" />
                        )}
                      </Button>
                    </span>
                  </FieldLabel>
                  {fieldErrors.credential && (
                    <FieldError id="connection-credential-error">
                      {zh ? "请输入凭据" : "Enter a credential"}
                    </FieldError>
                  )}
                </Field>
              )}
              {requiresCredential && !preview && (
                <Field>
                  <FieldLabel htmlFor="quick-connection-storage">
                    {t.storage}
                  </FieldLabel>
                  <Select
                    value={storage}
                    onValueChange={(value) =>
                      setStorage(value as StorageChoice)
                    }
                  >
                    <SelectTrigger id="quick-connection-storage">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="persistent">{t.persistent}</SelectItem>
                      <SelectItem value="session_only">{t.session}</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              )}
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              type="button"
              onClick={onClose}
              disabled={busy}
            >
              {zh ? "取消" : "Cancel"}
            </Button>
            <Button type="submit" disabled={busy}>
              {busy
                ? zh
                  ? "正在连接…"
                  : "Connecting…"
                : zh
                  ? "确认并连接"
                  : "Confirm and connect"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function demoConnectedState(
  name: string,
  endpoint: string,
  authentication: Authentication,
  generation: number,
): DesktopState {
  const connectionId = previewLocalConnectionId;
  const scope: ScopeDescriptor = {
    scope_id: previewEmptyScopeId,
    title: "默认空间",
    summary: "暂无数据",
    parent_scope_id: null,
    context_references: [],
    external_references: [],
    version: 1,
  };
  const report: CheckReport = {
    connectionId,
    revision: 1,
    checkedAt: Date.now(),
    liveness: { value: { status: "ok" }, error: null },
    readiness: { value: { status: "ready", checks: {} }, error: null },
    identity: { value: null, error: null },
    capabilities: { value: null, error: null },
    compatibilityVerified: true,
    anonymousAccess: authentication === "unauthenticated_loopback",
    supportedOperations: [],
  };
  return {
    generation,
    profiles: [
      {
        id: connectionId,
        revision: 1,
        name,
        endpoint,
        authentication,
        caPem: null,
        compatibility: null,
        credentialState:
          authentication === "bearer" ? "stored" : "not_required",
      },
    ],
    reports: [report],
    active: { connectionId, generation, report, scope },
    compatibilityProfiles: [],
    pendingCredentialCleanup: 0,
    lastWrite: null,
  };
}

function UsageDashboard({ state, language, contextPending, onState }: Props) {
  const t = copy[language];
  const active = state?.active;
  const report = activeCheckReport(state);
  const generation = active?.generation;
  const preview =
    import.meta.env.DEV && active?.connectionId.startsWith("preview-");
  const enabled =
    !contextPending &&
    !!active?.scope &&
    (preview || !!report?.supportedOperations.includes("get_stats"));
  const [reload, setReload] = useState(0);
  const [period, setPeriod] = useState<StatsPeriod>("30d");
  const [data, setData] = useState<ExactScopeStatistics | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [agentDialogMode, setAgentDialogMode] = useState<
    "connect" | "change" | null
  >(null);
  const [agentBindings, setAgentBindings] = useState<AgentBinding[]>([]);
  const [agentSuccess, setAgentSuccess] = useState<
    "authorized" | "bindingChanged" | "captureSaved" | "deleted" | null
  >(null);
  const request = useRef(0);
  const currentBinding = preview
    ? active?.scope?.scope_id === previewDataScopeId
      ? {
          agent: "Codex",
          serviceId: active.connectionId,
          scopeId: active.scope.scope_id,
        }
      : undefined
    : agentBindings.find(
        (binding) =>
          binding.serviceId === active?.connectionId &&
          binding.scopeId === active?.scope?.scope_id,
      );

  useEffect(() => {
    setData(null);
    setError(null);
  }, [generation, period]);
  useEffect(() => {
    if (!enabled || generation === undefined || preview) return;
    const ticket = ++request.current;
    setBusy(true);
    setError(null);
    void desktopApi
      .stats(generation, period)
      .then((next) => {
        if (ticket === request.current) setData(next);
      })
      .catch((failure) => {
        if (ticket === request.current) setError(failure);
      })
      .finally(() => {
        if (ticket === request.current) setBusy(false);
      });
    return () => {
      request.current++;
      void desktopApi.cancelStats(generation).catch(() => {});
    };
  }, [enabled, generation, period, preview, reload]);

  const summary = useMemo(() => {
    if (preview && active?.scope) {
      if (active.scope.scope_id === previewEmptyScopeId)
        return emptySpaceStats(period);
      return previewSpaceStats(active.scope, period);
    }
    return data ? statsFromServer(data) : null;
  }, [active?.scope, data, period, preview]);
  const number = new Intl.NumberFormat(language, { notation: "compact" });
  const isEmptyOverview = Boolean(
    !currentBinding?.agent &&
      summary &&
      summary.activeMemory === 0 &&
      summary.inactiveMemory === 0,
  );

  return (
    <div className="usage-dashboard">
      <header className="dashboard-heading">
        <h1>{t.title}</h1>
        {currentBinding && (
          <PageActionButton onClick={() => setAgentDialogMode("connect")}>
            {t.connectLocalAgent}
          </PageActionButton>
        )}
      </header>
      <div
        className="dashboard-first-row"
        data-agent-connected={Boolean(currentBinding?.agent)}
        data-empty={isEmptyOverview}
      >
        <AgentConnectionGuide
          language={language}
          agent={currentBinding?.agent}
          onConnect={() => setAgentDialogMode("connect")}
          onManage={() => setAgentDialogMode("change")}
          onCaptureSaved={() => setAgentSuccess("captureSaved")}
          onDeleted={() => setAgentSuccess("deleted")}
        />
        {summary && (
          <section
            className="memory-summary-card"
            aria-labelledby="memory-title"
          >
            <h2 id="memory-title">{t.memory}</h2>
            <dl>
              <div>
                <dt>
                  <span className="status-dot info" />
                  {t.active}
                </dt>
                <dd>{number.format(summary.activeMemory)}</dd>
              </div>
              <div>
                <dt>
                  <span className="status-dot" />
                  {t.inactive}
                </dt>
                <dd>{number.format(summary.inactiveMemory)}</dd>
              </div>
            </dl>
          </section>
        )}
      </div>
      {!enabled ? (
        <PageFeedback
          className="rounded-none border-x-0 py-12"
          kind="unavailable"
          title={t.unavailable}
        />
      ) : busy && !data ? (
        <PageFeedback
          className="rounded-none border-x-0 py-12"
          kind="loading"
          title={t.loading}
        />
      ) : error != null ? (
        <PageFeedback
          className="rounded-none border-x-0 py-12"
          kind="error"
          title={connectionError(error, language)}
          action={
            <Button
              size="sm"
              variant="outline"
              onClick={() => setReload((value) => value + 1)}
            >
              {messages[language].retryRead}
            </Button>
          }
        />
      ) : summary ? (
        <>
          <section className="overview-card" aria-labelledby="overview-title">
            <header className="overview-card-heading">
              <h2 id="overview-title">{t.overview}</h2>
              <Select
                value={period}
                onValueChange={(value) => setPeriod(value as StatsPeriod)}
              >
                <SelectTrigger aria-label={t.period}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="7d">{t.period7}</SelectItem>
                  <SelectItem value="30d">{t.period30}</SelectItem>
                </SelectContent>
              </Select>
            </header>
            <div className="overview-card-content">
              <dl className="metric-strip">
                <div>
                  <dt>{t.token}</dt>
                  <dd>{number.format(summary.token)}</dd>
                </div>
                <div>
                  <dt>{t.recall}</dt>
                  <dd>{number.format(summary.hits)}</dd>
                </div>
                <div>
                  <dt>{t.activeDays}</dt>
                  <dd>{number.format(summary.activeDays)}</dd>
                </div>
              </dl>
              <section
                className="activity-overview"
                aria-labelledby="activity-title"
              >
                <div className="section-heading-line">
                  <h3 id="activity-title">{t.activity}</h3>
                </div>
                <ActivityGrid
                  days={summary.daily}
                  language={language}
                  empty={t.noActivity}
                  quiet={t.activityQuiet}
                  active={t.activityActive}
                />
              </section>
            </div>
          </section>
        </>
      ) : (
        <PageFeedback
          className="rounded-none border-x-0 py-12"
          kind="empty"
          title={t.initial}
        />
      )}
      {agentDialogMode && (
        <ConnectAgentDialog
          state={state}
          language={language}
          onState={onState}
          mode={agentDialogMode}
          onClose={() => setAgentDialogMode(null)}
          onConnected={(binding) => {
            setAgentBindings((current) => [
              ...current.filter((item) => item.serviceId !== binding.serviceId),
              binding,
            ]);
            setAgentSuccess(
              agentDialogMode === "change" ? "bindingChanged" : "authorized",
            );
          }}
        />
      )}
      {agentSuccess && (
        <ActionNotification
          icon={<CircleCheck />}
          title={
            {
              authorized:
                language === "zh"
                  ? "服务凭据授权成功"
                  : "Service credentials authorized",
              bindingChanged:
                language === "zh"
                  ? "服务与空间已更改"
                  : "Service and space updated",
              captureSaved:
                language === "zh"
                  ? "消息采集设置已保存"
                  : "Message capture settings saved",
              deleted:
                language === "zh"
                  ? "Agent 连接已删除"
                  : "Agent connection deleted",
            }[agentSuccess]
          }
          closeLabel={language === "zh" ? "关闭通知" : "Close notification"}
          onClose={() => setAgentSuccess(null)}
          tone="success"
        />
      )}
    </div>
  );
}

type ActivityDay = { date: string; ready_preparations: number };
type DashboardStats = {
  token: number;
  hits: number;
  activeDays: number;
  activeMemory: number;
  inactiveMemory: number;
  daily: ActivityDay[];
};

function statsFromServer(data: ExactScopeStatistics): DashboardStats {
  const daily = data.recall.daily.map((day) => ({
    date: day.date,
    ready_preparations: day.ready_preparations,
  }));
  return {
    token: data.recall.totals.token_reduction,
    hits: data.recall.totals.ready_preparations,
    activeDays: daily.filter((day) => day.ready_preparations > 0).length,
    activeMemory: data.inventory.memory.entries.active,
    inactiveMemory: data.inventory.memory.entries.inactive,
    daily,
  };
}

function previewSpaceStats(
  scope: ScopeDescriptor,
  period: StatsPeriod,
): DashboardStats {
  const count = period === "7d" ? 7 : 30;
  const daily = Array.from({ length: count }, (_, index) => {
    const day = new Date("2026-11-11T12:00:00Z");
    day.setUTCDate(day.getUTCDate() - (count - index - 1));
    const hasData = scope.scope_id !== previewEmptyScopeId;
    const activity = hasData ? [0, 1, 0, 2, 4, 1, 3][index % 7] : 0;
    return {
      date: day.toISOString().slice(0, 10),
      ready_preparations: activity,
    };
  });
  const hits = daily.reduce((total, day) => total + day.ready_preparations, 0);
  const activeDays = daily.filter((day) => day.ready_preparations > 0).length;
  const hasData = scope.scope_id !== previewEmptyScopeId;
  return {
    token: hasData ? hits * 184 : 0,
    hits,
    activeDays,
    activeMemory: hasData ? previewWorkspaceMemoryCount : 0,
    inactiveMemory: 0,
    daily,
  };
}

function emptySpaceStats(period: StatsPeriod): DashboardStats {
  const count = period === "7d" ? 7 : 30;
  const daily = Array.from({ length: count }, (_, index) => {
    const day = new Date("2026-11-11T12:00:00Z");
    day.setUTCDate(day.getUTCDate() - (count - index - 1));
    return {
      date: day.toISOString().slice(0, 10),
      ready_preparations: 0,
    };
  });
  return {
    token: 0,
    hits: 0,
    activeDays: 0,
    activeMemory: 0,
    inactiveMemory: 0,
    daily,
  };
}

function ActivityGrid({
  days,
  language,
  empty,
  quiet,
  active,
}: {
  days: ActivityDay[];
  language: Language;
  empty: string;
  quiet: string;
  active: string;
}) {
  const max = Math.max(...days.map((day) => day.ready_preparations), 0);
  if (max === 0) return <p className="activity-empty">{empty}</p>;
  return (
    <div className="activity-calendar">
      <div className="activity-grid" role="img" aria-label={empty}>
        {days.map((day) => {
          const level = Math.max(
            0,
            Math.min(4, Math.ceil((day.ready_preparations / max) * 4)),
          );
          const label = `${day.date}: ${new Intl.NumberFormat(language).format(day.ready_preparations)}`;
          return (
            <span
              key={day.date}
              className={`activity-cell level-${level}`}
              title={label}
              aria-label={label}
            />
          );
        })}
      </div>
      <ActivityLegend quiet={quiet} active={active} />
    </div>
  );
}

function ActivityLegend({ quiet, active }: { quiet: string; active: string }) {
  return (
    <div className="activity-legend" aria-label={`${quiet} 到 ${active}`}>
      <span>{quiet}</span>
      {[0, 1, 2, 3, 4].map((level) => (
        <i
          className={`activity-cell level-${level}`}
          key={level}
          aria-hidden="true"
        />
      ))}
      <span>{active}</span>
    </div>
  );
}
