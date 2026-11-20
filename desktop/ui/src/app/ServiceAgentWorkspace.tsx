/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { FieldError } from "@/components/ui/field";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  CircleCheck,
  CircleX,
  Copy,
  Ellipsis,
  Eye,
  EyeOff,
} from "lucide-react";
import type { DesktopState, ProfileView } from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import { Button, ButtonGroup } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ActionNotification } from "@/components/ui/action-notification";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { PageActionButton } from "@/components/ui/page-action-button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogBody,
  DialogCloseButton,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Language } from "./messages";
import { ConnectAgentDialog, QuickConnectDialog } from "./Overview";
import { PageFeedback } from "@/components/ui/feedback";
import { useConfirmation } from "./useConfirmation";
import { LocalServiceDeploymentDialog } from "./LocalServiceDeploymentDialog";
import { AgentConnectionMenu } from "./AgentConnectionMenu";
import { ConnectionRequiredGuide } from "./ConnectionRequiredGuide";
import {
  buildServiceAgentWorkspaceModel,
  type ServiceAgent,
  type ServiceAgentWorkspaceModel,
} from "./service-agent-model";

const generationModelOptions = ["qwen-plus", "qwen-max", "qwen-turbo"];
const embeddingModelOptions = [
  "text-embedding-v3",
  "text-embedding-v2",
  "text-embedding-v1",
];

function ModelNameCombobox({
  id,
  value,
  options,
  placeholder,
  disabled,
  invalid,
  describedBy,
  menuLabel,
  onChange,
}: {
  id: string;
  value: string;
  options: string[];
  placeholder: string;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
  menuLabel: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="model-name-combobox">
      <Input
        id={id}
        adornment="trailing"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={false}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
      {options.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="field-trigger"
              size="icon-sm"
              className="model-name-combobox-trigger"
              type="button"
              disabled={disabled}
              aria-label={menuLabel}
            >
              <ChevronDown aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="model-name-combobox-options"
            align="end"
          >
            {options.map((model) => (
              <DropdownMenuItem key={model} onSelect={() => onChange(model)}>
                {model}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

async function validateModelName(modelName: string) {
  await new Promise((resolve) => globalThis.setTimeout(resolve, 420));
  return !/(?:^|[-_.])(invalid|unavailable|unknown)(?:[-_.]|$)/i.test(
    modelName,
  );
}

const copy = {
  zh: {
    title: "服务与 Agent",
    firstRunTitle: "暂无可用的服务与 Agent",
    installLocalTitle: "在本机安装服务",
    installLocalDescription: "适合首次使用，将服务和数据保存在当前电脑。",
    connectExistingTitle: "连接已有服务",
    connectExistingDescription: "使用服务地址和访问凭据连接已经运行的服务。",
    connectExisting: "连接已有服务",
    local: "本机 PowerContext 服务",
    localType: "本机服务",
    localEmpty: "暂无本机服务",
    running: "运行中",
    restarting: "重启中",
    restartingUnavailable: "服务正在重启中，暂不支持这些操作",
    startupTitle: "开机自动启动",
    startupDescription: "登录系统后自动启动本机服务",
    modelConfiguration: "模型配置",
    modelConfigurationDescription: "分别配置生成与向量检索所需的模型能力",
    generationModel: "生成模型",
    embeddingModel: "嵌入模型",
    configured: "已配置",
    notConfigured: "未配置",
    configureModels: "配置模型",
    agentListDescription: "仅展示关联在此服务下的本机 Agent",
    deployLocal: "安装服务",
    remote: "远程服务",
    serviceType: "服务类型",
    serviceStatus: "状态",
    serviceAddressLabel: "地址",
    ready: "连接可用",
    attention: "需要检查",
    unchecked: "尚未检查",
    noLocalAgents: "暂无关联此服务的本机 Agent",
    connectLocalAgent: "连接本地 Agent",
    editConnection: "编辑连接",
    moreActions: "更多操作",
    checkConnection: "检查连接状态",
    deleteConnection: "删除连接",
    confirmDeleteConnection: "确认删除连接",
    confirmDelete: "删除",
    deleteConfirmation:
      "只删除 Desktop 中的连接配置，不停止远程服务，也不删除空间、Agent 或记忆。确定删除吗？",
    editTitle: "编辑连接",
    connectionName: "连接名称",
    serviceAddress: "服务地址",
    cancel: "取消",
    save: "保存",
    saving: "正在保存…",
    saveFailed: "保存连接失败，请稍后重试。",
    credential: "凭据",
    credentialHint: "留空将继续使用当前凭据",
    showCredential: "显示凭据",
    hideCredential: "隐藏凭据",
    checkingConnection: "正在检查服务连接状态…",
    checkingLocalConnection: "正在检查本机服务连接状态…",
    connectionChecked: "连接状态正常",
    connectionCheckedHint: "远程服务可访问，连接已准备就绪。",
    localConnectionCheckedHint: "本机服务运行正常，连接已准备就绪。",
    connectionIssueFound: "发现连接问题",
    connectionIssue: "当前无法正常连接到远程服务，请检查服务状态、网络或凭据。",
    localConnectionIssue: "当前无法连接到本机服务，请确认服务正在运行后重试。",
    connectionSuggestion: "检查连接信息后，再次检查连接状态。",
    suggestion: "建议处理",
    done: "完成",
    closeDialog: "关闭弹窗",
    startService: "启动服务",
    restartService: "重启服务",
    stopService: "停止服务",
    deleteLocalService: "卸载服务",
    stopped: "已停止",
    confirmStartTitle: "确认启动本机服务",
    confirmStart:
      "启动后，PowerContext 将在本机运行，Codex 可继续采集消息和调用记忆。",
    confirmStopTitle: "确认停止本机服务",
    confirmStop:
      "停止后，Codex 将无法继续采集消息或调用记忆，直到服务再次启动。",
    confirmRestartTitle: "确认重启服务",
    confirmRestart:
      "重启期间将暂时无法进行服务与 Agent 操作。服务恢复运行后可继续使用。",
    confirmDeleteLocalConnectionTitle: "确认删除连接",
    confirmDeleteLocalConnection:
      "只会从 Desktop 中删除连接，不会停止或卸载 PowerContext 服务，也不会删除记忆。之后仍可重新连接此服务。",
    confirmDeleteLocalServiceTitle: "确认卸载服务",
    confirmDeleteLocalService:
      "这会卸载本机 PowerContext 服务并永久删除其中的全部记忆。此操作无法撤销。",
    remoteEmpty: "尚未添加远程服务",
    closeNotification: "关闭通知",
    startSucceeded: (name: string) => `服务“${name}”已启动`,
    stopSucceeded: (name: string) => `服务“${name}”已停止`,
    restartSucceeded: (name: string) => `服务“${name}”已重启`,
    connectionDeleted: (name: string) => `服务“${name}”的连接已删除`,
    serviceUninstalled: (name: string) => `服务“${name}”已卸载`,
    agentAuthorized: (name: string) => `服务“${name}”的凭据授权成功`,
    agentBindingChanged: (name: string) => `服务“${name}”与空间已更改`,
    captureSettingsSaved: (
      serviceName: string,
      agentName: string,
      enabled: boolean,
    ) =>
      `${serviceName}下的 ${agentName} Agent 的消息采集已${enabled ? "打开" : "关闭"}`,
    agentConnectionDeleted: (name: string) =>
      `服务“${name}”的 Agent 连接已删除`,
    modelConfigurationSaved: (name: string) => `服务“${name}”的模型配置已保存`,
    modelConfigurationUpdated: (name: string) =>
      `服务“${name}”的模型配置已更新`,
  },
  en: {
    title: "Services & Agents",
    firstRunTitle: "No available Services & Agents",
    installLocalTitle: "Install on this computer",
    installLocalDescription:
      "Best for first-time use. Keep the service and its data on this computer.",
    connectExistingTitle: "Connect an existing service",
    connectExistingDescription:
      "Use a service address and access credential to connect a running service.",
    connectExisting: "Connect existing service",
    local: "Local service",
    localType: "Local service",
    localEmpty: "No local service",
    running: "Running",
    restarting: "Restarting",
    restartingUnavailable:
      "The service is restarting. These actions are temporarily unavailable.",
    startupTitle: "Start automatically",
    startupDescription: "Start the local service after signing in",
    modelConfiguration: "Model configuration",
    modelConfigurationDescription:
      "Configure models for generation and vector retrieval",
    generationModel: "Generation model",
    embeddingModel: "Embedding model",
    configured: "Configured",
    notConfigured: "Not configured",
    configureModels: "Configure models",
    agentListDescription:
      "Only local Agents associated with this service are shown",
    deployLocal: "Install service",
    remote: "Remote services",
    serviceType: "Service type",
    serviceStatus: "Status",
    serviceAddressLabel: "Address",
    ready: "Connection available",
    attention: "Needs attention",
    unchecked: "Not checked",
    noLocalAgents: "No local Agents are associated with this service",
    connectLocalAgent: "Connect local Agent",
    editConnection: "Edit connection",
    moreActions: "More actions",
    checkConnection: "Check connection status",
    deleteConnection: "Delete connection",
    confirmDeleteConnection: "Delete connection?",
    confirmDelete: "Delete",
    deleteConfirmation:
      "This only removes the Desktop connection. It does not stop the remote service or delete spaces, Agents, or memories. Delete it?",
    editTitle: "Edit connection",
    connectionName: "Connection name",
    serviceAddress: "Service address",
    cancel: "Cancel",
    save: "Save",
    saving: "Saving…",
    saveFailed: "Could not save the connection. Try again.",
    credential: "Credential",
    credentialHint: "Leave blank to keep the current credential",
    showCredential: "Show credential",
    hideCredential: "Hide credential",
    checkingConnection: "Checking the remote service connection…",
    checkingLocalConnection: "Checking the local service connection…",
    connectionChecked: "Connection is healthy",
    connectionCheckedHint:
      "The remote service is reachable and the connection is ready.",
    localConnectionCheckedHint:
      "The local service is running and the connection is ready.",
    connectionIssueFound: "Connection issue found",
    connectionIssue:
      "Desktop cannot connect to the remote service. Check the service, network, or credential.",
    localConnectionIssue:
      "Desktop cannot connect to the local service. Make sure the service is running, then try again.",
    connectionSuggestion:
      "Review the connection details, then check the connection again.",
    suggestion: "Suggested fix",
    done: "Done",
    closeDialog: "Close dialog",
    startService: "Start service",
    restartService: "Restart service",
    stopService: "Stop service",
    deleteLocalService: "Uninstall service",
    stopped: "Stopped",
    confirmStartTitle: "Start local service?",
    confirmStart:
      "PowerContext will run on this computer so Codex can capture messages and use memories.",
    confirmStopTitle: "Stop local service?",
    confirmStop:
      "Codex will be unable to capture messages or use memories until the service starts again.",
    confirmRestartTitle: "Restart service?",
    confirmRestart:
      "Service and Agent actions will be temporarily unavailable while the service restarts.",
    confirmDeleteLocalConnectionTitle: "Delete connection?",
    confirmDeleteLocalConnection:
      "This only removes the connection from Desktop. It does not stop or uninstall PowerContext or delete any memories. You can reconnect later.",
    confirmDeleteLocalServiceTitle: "Uninstall service?",
    confirmDeleteLocalService:
      "This uninstalls the local PowerContext service and permanently deletes all of its memories. This action cannot be undone.",
    remoteEmpty: "No remote services added",
    closeNotification: "Close notification",
    startSucceeded: (name: string) => `Service “${name}” started`,
    stopSucceeded: (name: string) => `Service “${name}” stopped`,
    restartSucceeded: (name: string) => `Service “${name}” restarted`,
    connectionDeleted: (name: string) =>
      `Connection for service “${name}” deleted`,
    serviceUninstalled: (name: string) => `Service “${name}” uninstalled`,
    agentAuthorized: (name: string) =>
      `Credentials for service “${name}” authorized`,
    agentBindingChanged: (name: string) =>
      `Service “${name}” and space updated`,
    captureSettingsSaved: (
      serviceName: string,
      agentName: string,
      enabled: boolean,
    ) =>
      `Message capture for ${agentName} Agent under ${serviceName} ${enabled ? "enabled" : "disabled"}`,
    agentConnectionDeleted: (name: string) =>
      `Agent connection for service “${name}” deleted`,
    modelConfigurationSaved: (name: string) =>
      `Model configuration for service “${name}” saved`,
    modelConfigurationUpdated: (name: string) =>
      `Model configuration for service “${name}” updated`,
  },
} as const;

type Props = {
  state: DesktopState | null;
  language: Language;
  onState: (state: DesktopState) => boolean | void;
  onDeployLocal?: () => void;
  onDeploymentComplete?: () => void;
  onCreateSpace?: (trigger: HTMLElement) => void;
  model?: ServiceAgentWorkspaceModel;
  startOnLogin?: boolean;
  onStartOnLoginChange?: (enabled: boolean) => void;
  configuredModels?: { generation: boolean; embedding: boolean };
  onConfiguredModelsChange?: (configured: {
    generation: boolean;
    embedding: boolean;
  }) => void;
  modelConfigurationRequest?: "generation" | "embedding" | null;
  onModelConfigurationRequestHandled?: () => void;
};

export function ServiceAgentWorkspace({
  state,
  language,
  onState,
  onDeployLocal,
  onDeploymentComplete,
  onCreateSpace,
  model = buildServiceAgentWorkspaceModel(state),
  startOnLogin: controlledStartOnLogin,
  onStartOnLoginChange,
  configuredModels: controlledConfiguredModels,
  onConfiguredModelsChange,
  modelConfigurationRequest = null,
  onModelConfigurationRequestHandled,
}: Props) {
  const t = copy[language];
  const localService = model.services.find(
    (service) => service.kind === "local",
  );
  const remoteServices = model.services.filter(
    (service) => service.kind === "remote",
  );
  const profiles = remoteServices.map((service) => service.profile);
  const [connectOpen, setConnectOpen] = useState(false);
  const [deployOpen, setDeployOpen] = useState(false);
  const [agentDialog, setAgentDialog] = useState<{
    profileId: string;
    mode: "connect" | "change";
  } | null>(null);
  const [internalStartOnLogin, setInternalStartOnLogin] = useState(true);
  const [localServicePresent, setLocalServicePresent] =
    useState(!!localService);
  const [localRunning, setLocalRunning] = useState(
    localService?.running ?? false,
  );
  const [localRestarting, setLocalRestarting] = useState(false);
  type SuccessNotification =
    | "start"
    | "stop"
    | "restart"
    | "deleteConnection"
    | "uninstall"
    | "agentAuthorized"
    | "agentBindingChanged"
    | "captureSettingsSaved"
    | "agentConnectionDeleted"
    | "modelConfigurationSaved"
    | "modelConfigurationUpdated";
  const [successNotification, setSuccessNotification] = useState<{
    kind: SuccessNotification;
    serviceName: string;
    agentName?: string;
    captureEnabled?: boolean;
  } | null>(null);
  const notifySuccess = (
    kind: SuccessNotification,
    serviceName = localConnectionName,
    agentName?: string,
    captureEnabled?: boolean,
  ) =>
    setSuccessNotification({
      kind,
      serviceName,
      agentName,
      captureEnabled,
    });
  const [localEditOpen, setLocalEditOpen] = useState(false);
  const [localConnectionName, setLocalConnectionName] = useState<string>(
    t.local,
  );
  const [localEndpoint, setLocalEndpoint] = useState(
    localService?.profile.endpoint ?? "http://127.0.0.1:8000",
  );
  const [localCheckResult, setLocalCheckResult] = useState<
    "idle" | "checking" | "passed" | "failed"
  >("idle");
  const [hiddenLocalAgents, setHiddenLocalAgents] = useState<string[]>([]);
  const [localAgentsNotCapturing, setLocalAgentsNotCapturing] = useState<
    string[]
  >([]);
  const localAgents = localService?.agents ?? [];
  const visibleLocalAgents = localAgents.filter(
    (agent) => !hiddenLocalAgents.includes(agent.id),
  );
  const localAgentConnected =
    localServicePresent && visibleLocalAgents.length > 0;
  const connectedLocalAgentNames = visibleLocalAgents.map(
    (agent) => agent.name,
  );
  const [modelConfigTarget, setModelConfigTarget] = useState<
    "generation" | "embedding" | null
  >(null);
  const modelListRef = useRef<HTMLDivElement>(null);
  const [internalConfiguredModels, setInternalConfiguredModels] = useState({
    generation: false,
    embedding: false,
  });
  const startOnLogin = controlledStartOnLogin ?? internalStartOnLogin;
  const configuredModels =
    controlledConfiguredModels ?? internalConfiguredModels;
  const setStartOnLogin = onStartOnLoginChange ?? setInternalStartOnLogin;
  const setConfiguredModels =
    onConfiguredModelsChange ?? setInternalConfiguredModels;
  const [generationModelName, setGenerationModelName] = useState("qwen-plus");
  const [modelEditTarget, setModelEditTarget] = useState<
    "generation" | "embedding" | null
  >(null);
  const { confirm: confirmLocal, confirmation: localConfirmation } =
    useConfirmation(language);

  useEffect(() => {
    if (!modelConfigurationRequest) return;
    setModelConfigTarget(modelConfigurationRequest);
    onModelConfigurationRequestHandled?.();
  }, [modelConfigurationRequest, onModelConfigurationRequestHandled]);

  useEffect(() => {
    setLocalServicePresent(!!localService);
    setLocalRunning(localService?.running ?? false);
    setLocalConnectionName(t.local);
    setLocalEndpoint(localService?.profile.endpoint ?? "http://127.0.0.1:8000");
    setHiddenLocalAgents([]);
  }, [
    localService?.profile.id,
    localService?.profile.endpoint,
    localService?.running,
    t.local,
  ]);

  const localProfile: ProfileView = {
    ...(localService?.profile ?? {
      id: "local-service",
      revision: 1,
      authentication: "unauthenticated_loopback" as const,
      caPem: null,
      compatibility: null,
      credentialState: "not_required" as const,
    }),
    name: localConnectionName,
    endpoint: localEndpoint,
  };
  const agentDialogProfiles = localServicePresent
    ? [localProfile, ...profiles]
    : profiles;
  const [hiddenRemoteServiceIds, setHiddenRemoteServiceIds] = useState<
    string[]
  >([]);
  const visibleRemoteServices = remoteServices.filter(
    (service) => !hiddenRemoteServiceIds.includes(service.profile.id),
  );
  const hasAnyService = localServicePresent || visibleRemoteServices.length > 0;

  async function startLocalService() {
    if (
      !(await confirmLocal({
        title: t.confirmStartTitle,
        message: t.confirmStart,
        confirmLabel: t.startService,
        primaryMessage: true,
      }))
    )
      return;
    setLocalRunning(true);
    notifySuccess("start");
  }

  async function stopLocalService() {
    if (
      !(await confirmLocal({
        title: t.confirmStopTitle,
        message: t.confirmStop,
        confirmLabel: t.stopService,
        primaryMessage: true,
      }))
    )
      return;
    setLocalRunning(false);
    notifySuccess("stop");
  }

  async function restartLocalService() {
    if (
      localRestarting ||
      !(await confirmLocal({
        title: t.confirmRestartTitle,
        message: t.confirmRestart,
        confirmLabel: t.restartService,
        primaryMessage: true,
      }))
    )
      return;
    setLocalRestarting(true);
    window.setTimeout(() => {
      setLocalRestarting(false);
      notifySuccess("restart");
    }, 1200);
  }

  async function checkLocalConnection() {
    if (localRestarting || localCheckResult === "checking") return;
    setLocalCheckResult("checking");
    await new Promise((resolve) => window.setTimeout(resolve, 450));
    setLocalCheckResult(localRunning ? "passed" : "failed");
  }

  async function deleteLocalConnection() {
    if (
      !(await confirmLocal({
        title: t.confirmDeleteLocalConnectionTitle,
        message: t.confirmDeleteLocalConnection,
        confirmLabel: t.deleteConnection,
        primaryMessage: true,
      }))
    )
      return;
    setLocalServicePresent(false);
    setLocalRunning(false);
    notifySuccess("deleteConnection");
  }

  async function deleteLocalService() {
    if (
      !(await confirmLocal({
        title: t.confirmDeleteLocalServiceTitle,
        message: t.confirmDeleteLocalService,
        confirmLabel: t.deleteLocalService,
        destructive: true,
        primaryMessage: true,
      }))
    )
      return;
    setLocalServicePresent(false);
    setLocalRunning(false);
    notifySuccess("uninstall");
  }

  return (
    <div className="service-agent-workspace">
      {state && !hasAnyService ? (
        <ConnectionRequiredGuide
          title={t.firstRunTitle}
          language={language}
          onDeploy={() => setDeployOpen(true)}
          onConnect={() => setConnectOpen(true)}
        />
      ) : (
        <>
          <header className="service-agent-heading">
            <div>
              <h1>{t.title}</h1>
            </div>
            <PageActionButton onClick={() => setConnectOpen(true)}>
              {t.connectExisting}
            </PageActionButton>
          </header>

          <div className="service-accordion">
            <section className="service-connection">
              <ServiceHeader
                name={localConnectionName}
                actions={
                  localServicePresent ? (
                    <div className="service-header-actions">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <span
                            title={
                              localRestarting
                                ? t.restartingUnavailable
                                : undefined
                            }
                          >
                            <Button
                              size="icon-sm"
                              variant="outline"
                              aria-label={t.moreActions}
                              disabled={localRestarting}
                            >
                              <Ellipsis aria-hidden="true" />
                            </Button>
                          </span>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          {localRunning ? (
                            <>
                              <DropdownMenuItem
                                onSelect={(event) => {
                                  if (
                                    event.currentTarget instanceof HTMLElement
                                  )
                                    onCreateSpace?.(event.currentTarget);
                                }}
                              >
                                {language === "zh"
                                  ? "创建记忆空间"
                                  : "Create memory space"}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator className="service-menu-separator" />
                              <DropdownMenuItem
                                onSelect={() => setLocalEditOpen(true)}
                              >
                                {t.editConnection}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => void checkLocalConnection()}
                              >
                                {t.checkConnection}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator className="service-menu-separator" />
                              <DropdownMenuItem
                                onSelect={() => void restartLocalService()}
                              >
                                {t.restartService}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => void stopLocalService()}
                              >
                                {t.stopService}
                              </DropdownMenuItem>
                            </>
                          ) : (
                            <>
                              <DropdownMenuItem
                                onSelect={() => setLocalEditOpen(true)}
                              >
                                {t.editConnection}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => void checkLocalConnection()}
                              >
                                {t.checkConnection}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator className="service-menu-separator" />
                              <DropdownMenuItem
                                onSelect={() => void startLocalService()}
                              >
                                {t.startService}
                              </DropdownMenuItem>
                            </>
                          )}
                          <DropdownMenuSeparator className="service-menu-separator" />
                          <DropdownMenuItem
                            destructive
                            onSelect={() => void deleteLocalConnection()}
                          >
                            {t.deleteConnection}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            destructive
                            onSelect={() => void deleteLocalService()}
                          >
                            {t.deleteLocalService}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  ) : undefined
                }
              />
              {localServicePresent ? (
                <div className="local-service-cards">
                  <ServiceMetadataCard
                    type={t.localType}
                    status={
                      localRestarting
                        ? t.restarting
                        : localRunning
                          ? t.running
                          : t.stopped
                    }
                    address={localProfile.endpoint}
                    statusTone={
                      localRestarting
                        ? "neutral"
                        : localRunning
                          ? "success"
                          : "neutral"
                    }
                    labels={{
                      type: t.serviceType,
                      status: t.serviceStatus,
                      address: t.serviceAddressLabel,
                    }}
                  />
                  <section className="service-accordion-panel local-agent-panel">
                    <header className="service-agent-list-heading">
                      <p className="service-agent-list-description">
                        {t.agentListDescription}
                      </p>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={localRestarting}
                        title={
                          localRestarting ? t.restartingUnavailable : undefined
                        }
                        onClick={() =>
                          setAgentDialog({
                            profileId: localProfile.id,
                            mode: "connect",
                          })
                        }
                      >
                        {t.connectLocalAgent}
                      </Button>
                    </header>
                    {localAgentConnected ? (
                      <ul className="connected-agent-list service-connected-agent-list">
                        {visibleLocalAgents.map((agent) => (
                          <li key={agent.name}>
                            <span className="connected-agent-identity">
                              <span className="connected-agent-name">
                                <span className="connected-agent-name-line">
                                  <strong>{agent.name}</strong>
                                  <span className="connected-agent-status connected-agent-detail">
                                    <span className="status-dot success" />
                                    {language === "zh" ? "已连接" : "Connected"}
                                    {localAgentsNotCapturing.includes(
                                      agent.name,
                                    ) &&
                                      (language === "zh"
                                        ? " | 未采集消息"
                                        : " | Messages not captured")}
                                  </span>
                                </span>
                                <span className="connected-agent-project connected-agent-detail">
                                  {language === "zh" ? "项目" : "Project"}：
                                  {agent.project}
                                </span>
                              </span>
                            </span>
                            <span className="connected-agent-basic-info">
                              <span className="connected-agent-space connected-agent-detail">
                                {agent.space}
                              </span>
                              <span className="connected-agent-time connected-agent-detail">
                                {agent.lastWrite}
                              </span>
                            </span>
                            <AgentConnectionMenu
                              agent={agent.name}
                              language={language}
                              disabled={localRestarting}
                              disabledReason={t.restartingUnavailable}
                              onChangeBinding={() =>
                                setAgentDialog({
                                  profileId: localProfile.id,
                                  mode: "change",
                                })
                              }
                              onDelete={() =>
                                setHiddenLocalAgents((current) => [
                                  ...current,
                                  agent.id,
                                ])
                              }
                              onDeleted={() =>
                                notifySuccess("agentConnectionDeleted")
                              }
                              onCaptureChange={(enabled) =>
                                setLocalAgentsNotCapturing((current) =>
                                  enabled
                                    ? current.filter(
                                        (name) => name !== agent.name,
                                      )
                                    : [...new Set([...current, agent.name])],
                                )
                              }
                              onCaptureSaved={(enabled) =>
                                notifySuccess(
                                  "captureSettingsSaved",
                                  localConnectionName,
                                  agent.name,
                                  enabled,
                                )
                              }
                            />
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="service-agent-list-empty">
                        {t.noLocalAgents}
                      </div>
                    )}
                  </section>
                  <section className="service-accordion-panel local-settings-panel">
                    <div className="local-setting-row">
                      <div>
                        <strong>{t.startupTitle}</strong>
                        <p>{t.startupDescription}</p>
                      </div>
                      <Switch
                        checked={startOnLogin}
                        disabled={localRestarting}
                        title={
                          localRestarting ? t.restartingUnavailable : undefined
                        }
                        onCheckedChange={setStartOnLogin}
                        aria-label={t.startupTitle}
                      />
                    </div>
                    <div
                      className="local-model-settings"
                      ref={modelListRef}
                      tabIndex={-1}
                    >
                      <header>
                        <div>
                          <strong>{t.modelConfiguration}</strong>
                          <p>{t.modelConfigurationDescription}</p>
                        </div>
                      </header>
                      <dl>
                        <div>
                          <dt>{t.generationModel}</dt>
                          <dd data-configured={configuredModels.generation}>
                            {configuredModels.generation && (
                              <span>{generationModelName}</span>
                            )}
                            <small
                              data-configured={configuredModels.generation}
                            >
                              {configuredModels.generation
                                ? t.configured
                                : t.notConfigured}
                            </small>
                            <Button
                              size="sm"
                              variant="outline"
                              aria-label={
                                configuredModels.generation
                                  ? language === "zh"
                                    ? "修改生成模型"
                                    : "Edit generation model"
                                  : language === "zh"
                                    ? "配置生成模型"
                                    : "Configure generation model"
                              }
                              disabled={localRestarting}
                              title={
                                localRestarting
                                  ? t.restartingUnavailable
                                  : undefined
                              }
                              onClick={() =>
                                configuredModels.generation
                                  ? setModelEditTarget("generation")
                                  : setModelConfigTarget("generation")
                              }
                            >
                              {configuredModels.generation
                                ? language === "zh"
                                  ? "修改"
                                  : "Edit"
                                : language === "zh"
                                  ? "配置"
                                  : "Configure"}
                            </Button>
                          </dd>
                        </div>
                        <div>
                          <dt>{t.embeddingModel}</dt>
                          <dd data-configured={configuredModels.embedding}>
                            {configuredModels.embedding && (
                              <span>text-embedding-v3</span>
                            )}
                            <small data-configured={configuredModels.embedding}>
                              {configuredModels.embedding
                                ? t.configured
                                : t.notConfigured}
                            </small>
                            <Button
                              size="sm"
                              variant="outline"
                              aria-label={
                                configuredModels.embedding
                                  ? language === "zh"
                                    ? "修改嵌入模型"
                                    : "Edit embedding model"
                                  : language === "zh"
                                    ? "配置嵌入模型"
                                    : "Configure embedding model"
                              }
                              disabled={localRestarting}
                              title={
                                localRestarting
                                  ? t.restartingUnavailable
                                  : undefined
                              }
                              onClick={() =>
                                configuredModels.embedding
                                  ? setModelEditTarget("embedding")
                                  : setModelConfigTarget("embedding")
                              }
                            >
                              {configuredModels.embedding
                                ? language === "zh"
                                  ? "修改"
                                  : "Edit"
                                : language === "zh"
                                  ? "配置"
                                  : "Configure"}
                            </Button>
                          </dd>
                        </div>
                      </dl>
                    </div>
                  </section>
                </div>
              ) : (
                <section className="service-accordion-panel local-empty-panel">
                  <div className="service-agent-empty">
                    <p>{t.localEmpty}</p>
                    <Button
                      size="sm"
                      onClick={() => {
                        if (model.simulated) {
                          setLocalServicePresent(true);
                          setLocalRunning(true);
                        }
                        onDeployLocal?.();
                      }}
                    >
                      {t.deployLocal}
                    </Button>
                  </div>
                </section>
              )}
            </section>

            {visibleRemoteServices.map((service) => (
              <RemoteService
                key={service.profile.id}
                profile={service.profile}
                state={state}
                language={language}
                agents={service.agents}
                simulated={service.simulated}
                onConnectAgent={() =>
                  setAgentDialog({
                    profileId: service.profile.id,
                    mode: "connect",
                  })
                }
                onState={onState}
                onDeleted={() => {
                  setHiddenRemoteServiceIds((current) => [
                    ...current,
                    service.profile.id,
                  ]);
                  notifySuccess("deleteConnection", service.profile.name);
                }}
                onAgentDeleted={() =>
                  notifySuccess("agentConnectionDeleted", service.profile.name)
                }
                onCaptureSaved={(agentName, enabled) =>
                  notifySuccess(
                    "captureSettingsSaved",
                    service.profile.name,
                    agentName,
                    enabled,
                  )
                }
                onCreateSpace={onCreateSpace}
              />
            ))}
          </div>
        </>
      )}
      {connectOpen && (
        <QuickConnectDialog
          state={state}
          language={language}
          onState={onState}
          onClose={() => setConnectOpen(false)}
          title={
            language === "zh"
              ? "连接已有 PowerContext 服务"
              : "Connect an existing PowerContext service"
          }
        />
      )}
      {deployOpen && (
        <LocalServiceDeploymentDialog
          language={language}
          onClose={() => setDeployOpen(false)}
          run={async (signal) => {
            if (signal.aborted) return;
            if (!model.simulated)
              throw new Error(
                language === "zh"
                  ? "当前构建尚未接入本机安装执行器。"
                  : "This build is not connected to the local installer yet.",
              );
            await new Promise<void>((resolve, reject) => {
              const timer = window.setTimeout(resolve, 650);
              signal.addEventListener(
                "abort",
                () => {
                  window.clearTimeout(timer);
                  reject(new DOMException("Cancelled", "AbortError"));
                },
                { once: true },
              );
            });
          }}
          onComplete={() => {
            setLocalServicePresent(true);
            setLocalRunning(true);
            onDeployLocal?.();
            setDeployOpen(false);
            onDeploymentComplete?.();
          }}
        />
      )}
      {agentDialog && (
        <ConnectAgentDialog
          state={state}
          language={language}
          onState={onState}
          initialServiceId={agentDialog.profileId}
          profilesOverride={agentDialogProfiles}
          mode={agentDialog.mode}
          onClose={() => setAgentDialog(null)}
          onConnected={() => {
            const notification =
              agentDialog.mode === "change"
                ? "agentBindingChanged"
                : "agentAuthorized";
            setAgentDialog(null);
            const serviceName = agentDialogProfiles.find(
              (profile) => profile.id === agentDialog.profileId,
            )?.name;
            notifySuccess(notification, serviceName ?? t.local);
          }}
        />
      )}
      {localConfirmation}
      {successNotification && (
        <ActionNotification
          icon={<CircleCheck />}
          title={
            successNotification.kind === "captureSettingsSaved"
              ? t.captureSettingsSaved(
                  successNotification.serviceName,
                  successNotification.agentName ?? "",
                  successNotification.captureEnabled ?? false,
                )
              : {
                  start: t.startSucceeded,
                  stop: t.stopSucceeded,
                  restart: t.restartSucceeded,
                  deleteConnection: t.connectionDeleted,
                  uninstall: t.serviceUninstalled,
                  agentAuthorized: t.agentAuthorized,
                  agentBindingChanged: t.agentBindingChanged,
                  agentConnectionDeleted: t.agentConnectionDeleted,
                  modelConfigurationSaved: t.modelConfigurationSaved,
                  modelConfigurationUpdated: t.modelConfigurationUpdated,
                }[successNotification.kind](successNotification.serviceName)
          }
          closeLabel={t.closeNotification}
          onClose={() => setSuccessNotification(null)}
          tone="success"
        />
      )}
      {localEditOpen && (
        <EditConnectionDialog
          profile={localProfile}
          language={language}
          simulated={localService?.simulated ?? model.simulated}
          lockIdentity
          onState={onState}
          onPreviewSave={({ name, endpoint }) => {
            setLocalConnectionName(name);
            setLocalEndpoint(endpoint);
          }}
          onClose={() => setLocalEditOpen(false)}
        />
      )}
      {localCheckResult !== "idle" && (
        <ConnectionCheckDialog
          result={localCheckResult}
          language={language}
          serviceType="local"
          onClose={() => setLocalCheckResult("idle")}
        />
      )}
      {modelConfigTarget && (
        <ModelConfigurationDialog
          language={language}
          target={modelConfigTarget}
          configured={configuredModels}
          connectedAgents={connectedLocalAgentNames}
          onClose={() => setModelConfigTarget(null)}
          onApply={(next) => {
            setConfiguredModels(next);
            setModelConfigTarget(null);
            notifySuccess("modelConfigurationSaved");
            globalThis.requestAnimationFrame(() => {
              modelListRef.current?.scrollIntoView?.({
                behavior: "smooth",
                block: "center",
              });
              modelListRef.current?.focus({ preventScroll: true });
            });
          }}
        />
      )}
      {modelEditTarget && (
        <ModelEditDialog
          language={language}
          target={modelEditTarget}
          generationModelName={generationModelName}
          onClose={() => setModelEditTarget(null)}
          onSave={(name) => {
            if (name) setGenerationModelName(name);
            setModelEditTarget(null);
            notifySuccess("modelConfigurationUpdated");
          }}
        />
      )}
    </div>
  );
}

function ServiceHeader({
  name,
  actions,
}: {
  name: string;
  actions?: ReactNode;
}) {
  return (
    <div className="service-accordion-header">
      <span className="service-header-copy">
        <span className="service-header-title-row">
          <strong>{name}</strong>
        </span>
      </span>
      {actions}
    </div>
  );
}

function ServiceMetadataCard({
  type,
  status,
  address,
  statusTone = "neutral",
  labels,
}: {
  type: string;
  status: string;
  address: string;
  statusTone?: "success" | "danger" | "neutral";
  labels: { type: string; status: string; address: string };
}) {
  return (
    <section className="service-metadata-card" aria-label={type}>
      <dl>
        <div>
          <dt>{labels.type}</dt>
          <dd>{type}</dd>
        </div>
        <div>
          <dt>{labels.status}</dt>
          <dd className="service-status-indicator">
            <span
              className={`service-status-dot service-status-dot-${statusTone}`}
              aria-hidden="true"
            />
            {status}
          </dd>
        </div>
        <div>
          <dt>{labels.address}</dt>
          <dd title={address}>{address}</dd>
        </div>
      </dl>
    </section>
  );
}

function RemoteService({
  profile,
  state,
  language,
  agents,
  simulated,
  onConnectAgent,
  onState,
  onDeleted,
  onAgentDeleted,
  onCaptureSaved,
  onCreateSpace,
}: {
  profile: ProfileView;
  state: DesktopState | null;
  language: Language;
  agents: ServiceAgent[];
  simulated: boolean;
  onConnectAgent: () => void;
  onState: (state: DesktopState) => boolean | void;
  onDeleted: () => void;
  onAgentDeleted: () => void;
  onCaptureSaved: (agentName: string, enabled: boolean) => void;
  onCreateSpace?: (trigger: HTMLElement) => void;
}) {
  const t = copy[language];
  const { confirm, confirmation } = useConfirmation(language);
  const [busy, setBusy] = useState<"check" | "delete" | null>(null);
  const [checkResult, setCheckResult] = useState<
    "idle" | "checking" | "passed" | "failed"
  >("idle");
  const [editOpen, setEditOpen] = useState(false);
  const [displayName, setDisplayName] = useState(profile.name);
  const [hiddenAgentIds, setHiddenAgentIds] = useState<string[]>([]);
  const [agentIdsNotCapturing, setAgentIdsNotCapturing] = useState<string[]>(
    [],
  );
  const report = state?.reports.find(
    (item) => item.connectionId === profile.id,
  );
  const hasError = !!report?.liveness.error || !!report?.readiness.error;
  const ready = report?.readiness.value?.status === "ready";
  const status = hasError ? t.attention : ready ? t.ready : t.unchecked;
  const statusTone = hasError ? "danger" : ready ? "success" : "neutral";
  useEffect(() => setDisplayName(profile.name), [profile.name]);

  async function checkConnection() {
    if (busy) return;
    setBusy("check");
    setCheckResult("checking");
    try {
      if (simulated) {
        await new Promise((resolve) => window.setTimeout(resolve, 450));
        setCheckResult("passed");
        return;
      }
      const next = await desktopApi.check(profile.id, false);
      onState(next);
      const nextReport = next.reports.find(
        (item) => item.connectionId === profile.id,
      );
      setCheckResult(
        !nextReport?.liveness.error &&
          !nextReport?.readiness.error &&
          nextReport?.readiness.value?.status === "ready"
          ? "passed"
          : "failed",
      );
    } catch {
      setCheckResult("failed");
    } finally {
      setBusy(null);
    }
  }

  async function deleteConnection() {
    if (
      busy ||
      !(await confirm({
        title: t.confirmDeleteConnection,
        message: t.deleteConfirmation,
        confirmLabel: t.confirmDelete,
        primaryMessage: true,
        destructive: true,
      }))
    )
      return;
    if (simulated) {
      onDeleted();
      return;
    }
    setBusy("delete");
    try {
      onState(await desktopApi.remove(profile.id, profile.revision));
      onDeleted();
    } catch {
      // Keep the connection visible when removal fails.
    } finally {
      setBusy(null);
    }
  }
  return (
    <>
      {confirmation}
      <section className="service-connection remote-service-item">
        <ServiceHeader
          name={displayName}
          actions={
            <div className="service-header-actions">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    size="icon-sm"
                    variant="outline"
                    aria-label={t.moreActions}
                  >
                    <Ellipsis aria-hidden="true" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onSelect={(event) => {
                      if (event.currentTarget instanceof HTMLElement)
                        onCreateSpace?.(event.currentTarget);
                    }}
                  >
                    {language === "zh" ? "创建记忆空间" : "Create memory space"}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator className="service-menu-separator" />
                  <DropdownMenuItem onSelect={() => setEditOpen(true)}>
                    {t.editConnection}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    disabled={!!busy}
                    onSelect={() => void checkConnection()}
                  >
                    {t.checkConnection}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator className="service-menu-separator" />
                  <DropdownMenuItem
                    destructive
                    disabled={!!busy}
                    onSelect={() => void deleteConnection()}
                  >
                    {t.deleteConnection}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          }
        />
        <ServiceMetadataCard
          type={t.remote}
          status={status}
          address={profile.endpoint}
          statusTone={statusTone}
          labels={{
            type: t.serviceType,
            status: t.serviceStatus,
            address: t.serviceAddressLabel,
          }}
        />
        <section className="service-accordion-panel remote">
          <header className="service-agent-list-heading">
            <p className="service-agent-list-description">
              {t.agentListDescription}
            </p>
            <Button size="sm" variant="outline" onClick={onConnectAgent}>
              {t.connectLocalAgent}
            </Button>
          </header>
          <section className="service-agent-list-section">
            {agents.some((agent) => !hiddenAgentIds.includes(agent.id)) ? (
              <ul className="connected-agent-list service-connected-agent-list">
                {agents
                  .filter((agent) => !hiddenAgentIds.includes(agent.id))
                  .map((agent) => (
                    <li key={agent.id}>
                      <span className="connected-agent-identity">
                        <span className="connected-agent-name">
                          <span className="connected-agent-name-line">
                            <strong>{agent.name}</strong>
                            <span className="connected-agent-status connected-agent-detail">
                              <span className="status-dot success" />
                              {language === "zh" ? "已连接" : "Connected"}
                              {agentIdsNotCapturing.includes(agent.id) &&
                                (language === "zh"
                                  ? " | 未采集消息"
                                  : " | Messages not captured")}
                            </span>
                          </span>
                          <span className="connected-agent-project connected-agent-detail">
                            {language === "zh" ? "项目" : "Project"}：
                            {agent.project}
                          </span>
                        </span>
                      </span>
                      <span className="connected-agent-basic-info">
                        <span className="connected-agent-space connected-agent-detail">
                          {agent.space}
                        </span>
                        <span className="connected-agent-time connected-agent-detail">
                          {agent.lastWrite}
                        </span>
                      </span>
                      <AgentConnectionMenu
                        agent={agent.name}
                        language={language}
                        onChangeBinding={onConnectAgent}
                        onDelete={() =>
                          setHiddenAgentIds((current) => [...current, agent.id])
                        }
                        onDeleted={onAgentDeleted}
                        onCaptureChange={(enabled) =>
                          setAgentIdsNotCapturing((current) =>
                            enabled
                              ? current.filter((id) => id !== agent.id)
                              : [...new Set([...current, agent.id])],
                          )
                        }
                        onCaptureSaved={(enabled) =>
                          onCaptureSaved(agent.name, enabled)
                        }
                      />
                    </li>
                  ))}
              </ul>
            ) : (
              <div className="service-agent-list-empty">{t.noLocalAgents}</div>
            )}
          </section>
        </section>
      </section>
      {editOpen && (
        <EditConnectionDialog
          profile={{ ...profile, name: displayName }}
          language={language}
          simulated={simulated}
          onState={onState}
          onPreviewSave={({ name }) => setDisplayName(name)}
          onClose={() => setEditOpen(false)}
        />
      )}
      {checkResult !== "idle" && (
        <ConnectionCheckDialog
          result={checkResult}
          language={language}
          onClose={() => setCheckResult("idle")}
        />
      )}
    </>
  );
}

function ModelEditDialog({
  language,
  target,
  generationModelName,
  onSave,
  onClose,
}: {
  language: Language;
  target: "generation" | "embedding";
  generationModelName: string;
  onSave: (generationModelName?: string) => void;
  onClose: () => void;
}) {
  const zh = language === "zh";
  const generation = target === "generation";
  const [modelName, setModelName] = useState(generationModelName);
  const [endpoint, setEndpoint] = useState("https://api.example.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [saveAttempted, setSaveAttempted] = useState(false);
  const [checking, setChecking] = useState(false);
  const [connected, setConnected] = useState(false);
  const [connectivityError, setConnectivityError] = useState<string | null>(
    null,
  );
  const [modelValidationError, setModelValidationError] = useState(false);
  const [connectivityAttempts, setConnectivityAttempts] = useState(0);
  const fieldErrors = {
    modelName: submitted && generation && connected && !modelName.trim(),
    endpoint: submitted && !endpoint.trim(),
    apiKey: submitted && !apiKey.trim(),
  };
  const connectivityRequired =
    generation &&
    saveAttempted &&
    Boolean(endpoint.trim()) &&
    Boolean(apiKey.trim()) &&
    !connected;
  const title = generation
    ? zh
      ? "修改生成模型"
      : "Edit generation model"
    : zh
      ? "修改嵌入模型"
      : "Edit embedding model";

  async function save() {
    if (checking) return;
    setSubmitted(true);
    setSaveAttempted(true);
    setConnectivityError(null);
    setModelValidationError(false);
    if (!endpoint.trim() || !apiKey.trim()) return;
    if (generation && !connected) return;
    if (generation && !modelName.trim()) return;

    setChecking(true);
    if (generation && !(await validateModelName(modelName.trim()))) {
      setModelValidationError(true);
      setChecking(false);
      return;
    }
    await new Promise((resolve) => globalThis.setTimeout(resolve, 420));
    if (!generation && connectivityAttempts === 0) {
      setConnectivityAttempts(1);
      setConnectivityError(
        zh
          ? "无法连接到模型服务，请检查服务地址、网络或 API Key 后重试。"
          : "Could not connect to the model service. Check the endpoint, network, or API key and try again.",
      );
      setChecking(false);
      return;
    }
    onSave(generation ? modelName.trim() : undefined);
  }

  async function checkGenerationConnectivity() {
    if (checking) return;
    setSubmitted(true);
    setSaveAttempted(false);
    setConnectivityError(null);
    setConnected(false);
    if (!endpoint.trim() || !apiKey.trim()) return;

    let modelEndpoint: URL;
    try {
      modelEndpoint = new URL(endpoint.trim());
      if (!["http:", "https:"].includes(modelEndpoint.protocol)) throw null;
    } catch {
      setConnectivityError(
        zh
          ? "无法连接到模型服务，请检查服务地址后重试。"
          : "Could not connect to the model service. Check the endpoint and try again.",
      );
      return;
    }

    setChecking(true);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 420));
    if (/^(unreachable|invalid)(\.|$)/i.test(modelEndpoint.hostname)) {
      setConnectivityError(
        zh
          ? "无法连接到模型服务，请检查服务地址、网络或 API Key 后重试。"
          : "Could not connect to the model service. Check the endpoint, network, or API key and try again.",
      );
      setChecking(false);
      return;
    }
    setModelName((current) =>
      generationModelOptions.includes(current) ? current : "",
    );
    setConnected(true);
    setSubmitted(false);
    setChecking(false);
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form
          className="model-edit-dialog contents"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogCloseButton label={zh ? "关闭弹窗" : "Close dialog"} />
          </DialogHeader>
          <DialogBody>
            {connectivityError && (
              <Alert variant="destructive">
                <CircleX
                  aria-hidden="true"
                  color="var(--destructive)"
                  size={16}
                  strokeWidth={1.75}
                />
                <AlertTitle>
                  {zh ? "连通性校验失败" : "Connectivity check failed"}
                </AlertTitle>
                <AlertDescription>{connectivityError}</AlertDescription>
              </Alert>
            )}
            <fieldset
              disabled={checking}
              className="manual-model-configuration"
            >
              <div className="form-field">
                <label htmlFor={`edit-${target}-endpoint`}>
                  {zh ? "服务地址" : "Endpoint"}
                </label>
                <Input
                  id={`edit-${target}-endpoint`}
                  value={endpoint}
                  aria-invalid={fieldErrors.endpoint || undefined}
                  aria-describedby={
                    fieldErrors.endpoint
                      ? `edit-${target}-endpoint-error`
                      : undefined
                  }
                  onChange={(event) => {
                    setEndpoint(event.target.value);
                    setConnected(false);
                    setSaveAttempted(false);
                    setConnectivityError(null);
                    setModelValidationError(false);
                  }}
                />
                {fieldErrors.endpoint && (
                  <FieldError id={`edit-${target}-endpoint-error`}>
                    {zh ? "请输入服务地址" : "Enter an endpoint"}
                  </FieldError>
                )}
              </div>
              <div className="form-field">
                {generation ? (
                  <div className="model-api-key-heading">
                    <label htmlFor={`edit-${target}-api-key`}>
                      {zh
                        ? "API Key（仅保存在 Desktop）"
                        : "API key (stored only in Desktop)"}
                    </label>
                    <Button
                      size="sm"
                      variant="outline"
                      type="button"
                      disabled={checking}
                      onClick={() => void checkGenerationConnectivity()}
                    >
                      {checking
                        ? zh
                          ? "正在校验…"
                          : "Checking…"
                        : connected
                          ? zh
                            ? "已通过连通性校验"
                            : "Connection verified"
                          : zh
                            ? "校验连通性"
                            : "Check connection"}
                    </Button>
                  </div>
                ) : (
                  <label htmlFor={`edit-${target}-api-key`}>
                    {zh
                      ? "API Key（仅保存在 Desktop）"
                      : "API key (stored only in Desktop)"}
                  </label>
                )}
                <Input
                  id={`edit-${target}-api-key`}
                  type="password"
                  value={apiKey}
                  autoComplete="new-password"
                  aria-invalid={fieldErrors.apiKey || undefined}
                  aria-describedby={
                    fieldErrors.apiKey || connectivityRequired
                      ? `edit-${target}-api-key-error`
                      : undefined
                  }
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    if (generation) setConnected(false);
                    setSaveAttempted(false);
                    setConnectivityError(null);
                    setModelValidationError(false);
                  }}
                />
                {fieldErrors.apiKey && (
                  <FieldError id={`edit-${target}-api-key-error`}>
                    {zh ? "请输入 API Key" : "Enter an API key"}
                  </FieldError>
                )}
                {connectivityRequired && (
                  <FieldError id={`edit-${target}-api-key-error`}>
                    {zh
                      ? "请先校验 API Key 的连通性"
                      : "Check the API key connection first"}
                  </FieldError>
                )}
                {generation && connected && (
                  <span className="model-connectivity-success" role="status">
                    <CircleCheck aria-hidden="true" />
                    {zh ? "已获取可用模型" : "Available models loaded"}
                  </span>
                )}
              </div>
              {generation && (
                <div className="form-field">
                  <label htmlFor="edit-generation-model">
                    {zh ? "生成模型" : "Generation model"}
                  </label>
                  <ModelNameCombobox
                    id="edit-generation-model"
                    value={modelName}
                    disabled={!connected}
                    options={generationModelOptions}
                    invalid={fieldErrors.modelName || modelValidationError}
                    describedBy="edit-generation-model-error"
                    menuLabel={zh ? "显示可用模型" : "Show available models"}
                    placeholder={
                      connected
                        ? zh
                          ? "选择或输入生成模型"
                          : "Select or enter a model"
                        : zh
                          ? "请先校验 API Key 的连通性"
                          : "Check the API key connection first"
                    }
                    onChange={(value) => {
                      setModelName(value);
                      setModelValidationError(false);
                    }}
                  />
                  {fieldErrors.modelName && (
                    <FieldError id="edit-generation-model-error">
                      {zh ? "请选择生成模型" : "Select a generation model"}
                    </FieldError>
                  )}
                  {modelValidationError && (
                    <FieldError id="edit-generation-model-error">
                      {zh
                        ? "该模型当前不可用，请检查名称或选择其他模型"
                        : "This model is unavailable. Check the name or choose another model."}
                    </FieldError>
                  )}
                </div>
              )}
              {!generation && (
                <div className="form-field">
                  <label htmlFor="embedding-model-type">
                    {zh ? "模型类型（不可修改）" : "Model type (locked)"}
                  </label>
                  <Input
                    id="embedding-model-type"
                    value="text-embedding-v3"
                    readOnly
                    disabled
                  />
                </div>
              )}
              <p className="model-apply-note">
                {zh
                  ? "保存后自动应用，并在需要时重启本机服务。"
                  : "Desktop applies the change and restarts the local service when needed."}
              </p>
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <Button size="sm" variant="outline" type="button" onClick={onClose}>
              {zh ? "取消" : "Cancel"}
            </Button>
            <Button type="submit" disabled={checking}>
              {checking
                ? zh
                  ? "正在校验…"
                  : "Checking…"
                : zh
                  ? "保存并应用"
                  : "Save and apply"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ModelConfigurationDialog({
  language,
  target,
  configured,
  connectedAgents,
  onApply,
  onClose,
}: {
  language: Language;
  target: "generation" | "embedding";
  configured: { generation: boolean; embedding: boolean };
  connectedAgents: string[];
  onApply: (next: { generation: boolean; embedding: boolean }) => void;
  onClose: () => void;
}) {
  const zh = language === "zh";
  const [method, setMethod] = useState<"agent" | "manual">("agent");
  const generation = target === "generation";
  const embedding = target === "embedding";
  const [generationModel, setGenerationModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [targetAgent, setTargetAgent] = useState(connectedAgents[0] ?? "");
  const [submitted, setSubmitted] = useState(false);
  const [manualSaveAttempted, setManualSaveAttempted] = useState(false);
  const [checking, setChecking] = useState(false);
  const [manualConnected, setManualConnected] = useState(false);
  const [copied, setCopied] = useState(false);
  const [connectivityError, setConnectivityError] = useState<string | null>(
    null,
  );
  const [modelValidationError, setModelValidationError] = useState(false);
  const fieldErrors = {
    targetAgent: submitted && method === "agent" && !targetAgent,
    generationModel:
      submitted &&
      method === "manual" &&
      manualConnected &&
      generation &&
      !generationModel.trim(),
    embeddingModel:
      submitted &&
      method === "manual" &&
      manualConnected &&
      embedding &&
      !embeddingModel.trim(),
    endpoint: submitted && method === "manual" && !endpoint.trim(),
    apiKey: submitted && method === "manual" && !apiKey.trim(),
  };
  const manualConnectivityRequired =
    method === "manual" &&
    manualSaveAttempted &&
    Boolean(endpoint.trim()) &&
    Boolean(apiKey.trim()) &&
    !manualConnected;
  const capability = generation
    ? zh
      ? "生成模型"
      : "generation model"
    : zh
      ? "嵌入模型"
      : "embedding model";
  const prompt = zh
    ? `请检查当前 PowerContext 模型设置，并为${targetAgent || "所选 Agent"}配置${capability}。请只将非敏感配置写入 Desktop 可读取的本机暂存区，不要在对话、命令或回执中包含 API Key、Token 或其他凭据。完成后告诉我可以回到 Desktop 检查配置。`
    : `Review the current PowerContext model settings and configure the ${capability} for ${targetAgent || "the selected Agent"}. Write only non-sensitive settings to the local staging area read by Desktop. Do not include API keys, tokens, or other credentials in chat, commands, or receipts. Tell me when Desktop can check the configuration.`;

  async function copyPrompt() {
    await navigator.clipboard?.writeText(prompt);
    setCopied(true);
    globalThis.setTimeout(() => setCopied(false), 1600);
  }

  async function checkAgentConfiguration() {
    if (checking) return;
    setSubmitted(true);
    setConnectivityError(null);
    if (!targetAgent) return;
    setChecking(true);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 420));
    onApply({
      generation: configured.generation || generation,
      embedding: configured.embedding || embedding,
    });
  }

  async function saveManualConfiguration() {
    if (checking) return;
    setSubmitted(true);
    setManualSaveAttempted(true);
    setConnectivityError(null);
    setModelValidationError(false);
    if (!endpoint.trim() || !apiKey.trim()) return;
    if (!manualConnected) return;
    if (
      (generation && !generationModel.trim()) ||
      (embedding && !embeddingModel.trim())
    )
      return;

    const modelName = generation
      ? generationModel.trim()
      : embeddingModel.trim();
    setChecking(true);
    if (!(await validateModelName(modelName))) {
      setModelValidationError(true);
      setChecking(false);
      return;
    }
    onApply({
      generation: configured.generation || generation,
      embedding: configured.embedding || embedding,
    });
  }

  async function checkManualConnectivity() {
    if (checking) return;
    setSubmitted(true);
    setManualSaveAttempted(false);
    setConnectivityError(null);
    setManualConnected(false);
    if (!endpoint.trim() || !apiKey.trim()) return;

    let modelEndpoint: URL;
    try {
      modelEndpoint = new URL(endpoint.trim());
      if (!["http:", "https:"].includes(modelEndpoint.protocol)) throw null;
    } catch {
      setConnectivityError(
        zh
          ? "无法连接到模型服务，请检查服务地址后重试。"
          : "Could not connect to the model service. Check the endpoint and try again.",
      );
      return;
    }

    setChecking(true);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 420));
    if (/^(unreachable|invalid)(\.|$)/i.test(modelEndpoint.hostname)) {
      setChecking(false);
      setConnectivityError(
        zh
          ? "模型服务连通性校验失败，请检查服务地址、网络或 API Key。"
          : "Model connectivity check failed. Check the endpoint, network, or API key.",
      );
      return;
    }
    setGenerationModel((current) =>
      generationModelOptions.includes(current)
        ? current
        : generationModelOptions[0],
    );
    setEmbeddingModel((current) =>
      embeddingModelOptions.includes(current)
        ? current
        : embeddingModelOptions[0],
    );
    setManualConnected(true);
    setSubmitted(false);
    setChecking(false);
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form
          className="model-config-dialog contents"
          onSubmit={(event) => {
            event.preventDefault();
            if (method === "manual") void saveManualConfiguration();
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {zh ? `配置${capability}` : `Configure ${capability}`}
            </DialogTitle>
            <DialogCloseButton label={zh ? "关闭弹窗" : "Close dialog"} />
          </DialogHeader>

          <DialogBody>
            <ButtonGroup role="tablist">
              <Button
                className="model-config-method"
                size="sm"
                type="button"
                variant="segmented"
                role="tab"
                aria-selected={method === "agent"}
                onClick={() => {
                  setMethod("agent");
                  setSubmitted(false);
                  setConnectivityError(null);
                }}
              >
                {zh ? "Agent 协助" : "Agent-assisted"}
              </Button>
              <Button
                className="model-config-method"
                size="sm"
                type="button"
                variant="segmented"
                role="tab"
                aria-selected={method === "manual"}
                onClick={() => {
                  setMethod("manual");
                  setSubmitted(false);
                  setConnectivityError(null);
                }}
              >
                {zh ? "手动配置" : "Manual"}
              </Button>
            </ButtonGroup>

            {connectivityError && (
              <Alert variant="destructive">
                <CircleX
                  aria-hidden="true"
                  color="var(--destructive)"
                  size={16}
                  strokeWidth={1.75}
                />
                <AlertTitle>
                  {zh ? "配置校验失败" : "Configuration validation failed"}
                </AlertTitle>
                <AlertDescription>{connectivityError}</AlertDescription>
              </Alert>
            )}

            {method === "agent" ? (
              <div className="agent-model-configuration">
                <div className="form-field">
                  <label htmlFor="model-target-agent">
                    {zh ? "目标 Agent" : "Target Agent"}
                  </label>
                  <Select
                    value={targetAgent}
                    onValueChange={(value) => {
                      setTargetAgent(value);
                      setConnectivityError(null);
                    }}
                  >
                    <SelectTrigger
                      id="model-target-agent"
                      aria-invalid={fieldErrors.targetAgent || undefined}
                      aria-describedby={
                        fieldErrors.targetAgent
                          ? "model-target-agent-error"
                          : undefined
                      }
                    >
                      <SelectValue
                        placeholder={
                          zh
                            ? "请选择已连接的 Agent"
                            : "Select a connected Agent"
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {connectedAgents.map((agent) => (
                        <SelectItem key={agent} value={agent}>
                          {agent}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {fieldErrors.targetAgent && (
                    <FieldError id="model-target-agent-error">
                      {zh
                        ? "请选择当前服务下已连接的本地 Agent"
                        : "Select a local Agent connected to this service"}
                    </FieldError>
                  )}
                </div>
                <div className="model-prompt-block">
                  <strong>{zh ? "复制提示词" : "Copy prompt"}</strong>
                  <p>
                    {zh
                      ? "提示词不包含 API Key 或 Token。Agent 完成后，Desktop 会自动读取、保存、应用并按需重启服务。"
                      : "The prompt contains no API key or token. Desktop reads, saves, applies, and restarts as needed after the Agent finishes."}
                  </p>
                  <pre>{prompt}</pre>
                  <Button
                    size="sm"
                    type="button"
                    variant="outline"
                    disabled={!targetAgent}
                    onClick={() => void copyPrompt()}
                  >
                    <Copy aria-hidden="true" />
                    {copied
                      ? zh
                        ? "已复制"
                        : "Copied"
                      : zh
                        ? "复制提示词"
                        : "Copy prompt"}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="manual-model-configuration">
                <div className="form-field">
                  <label htmlFor="model-endpoint">
                    {zh ? "服务地址" : "Endpoint"}
                  </label>
                  <Input
                    id="model-endpoint"
                    value={endpoint}
                    placeholder="https://api.example.com/v1"
                    aria-invalid={fieldErrors.endpoint || undefined}
                    aria-describedby={
                      fieldErrors.endpoint ? "model-endpoint-error" : undefined
                    }
                    onChange={(event) => {
                      setEndpoint(event.target.value);
                      setManualConnected(false);
                      setManualSaveAttempted(false);
                      setConnectivityError(null);
                    }}
                  />
                  {fieldErrors.endpoint && (
                    <FieldError id="model-endpoint-error">
                      {zh
                        ? "请输入模型服务地址"
                        : "Enter the model service endpoint"}
                    </FieldError>
                  )}
                </div>
                <div className="form-field">
                  <div className="model-api-key-heading">
                    <label htmlFor="model-api-key">
                      {zh
                        ? "API Key（仅保存在 Desktop）"
                        : "API key (stored only in Desktop)"}
                    </label>
                    <Button
                      size="sm"
                      variant="outline"
                      type="button"
                      disabled={checking}
                      onClick={() => void checkManualConnectivity()}
                    >
                      {checking
                        ? zh
                          ? "正在校验…"
                          : "Checking…"
                        : manualConnected
                          ? zh
                            ? "已通过连通性校验"
                            : "Connection verified"
                          : zh
                            ? "校验连通性"
                            : "Check connection"}
                    </Button>
                  </div>
                  <Input
                    id="model-api-key"
                    type="password"
                    value={apiKey}
                    autoComplete="new-password"
                    aria-invalid={fieldErrors.apiKey || undefined}
                    aria-describedby={
                      fieldErrors.apiKey || manualConnectivityRequired
                        ? "model-api-key-error"
                        : undefined
                    }
                    onChange={(event) => {
                      setApiKey(event.target.value);
                      setManualConnected(false);
                      setManualSaveAttempted(false);
                      setConnectivityError(null);
                    }}
                  />
                  {fieldErrors.apiKey && (
                    <FieldError id="model-api-key-error">
                      {zh ? "请输入 API Key" : "Enter an API key"}
                    </FieldError>
                  )}
                  {manualConnectivityRequired && (
                    <FieldError id="model-api-key-error">
                      {zh
                        ? "请先校验 API Key 的连通性"
                        : "Check the API key connection first"}
                    </FieldError>
                  )}
                  {manualConnected && (
                    <span className="model-connectivity-success" role="status">
                      <CircleCheck aria-hidden="true" />
                      {zh ? "已获取可用模型" : "Available models loaded"}
                    </span>
                  )}
                </div>
                {generation && (
                  <div className="form-field">
                    <label htmlFor="generation-model-name">
                      {zh ? "生成模型" : "Generation model"}
                    </label>
                    <ModelNameCombobox
                      id="generation-model-name"
                      value={generationModel}
                      disabled={!manualConnected}
                      options={generationModelOptions}
                      invalid={
                        fieldErrors.generationModel || modelValidationError
                      }
                      describedBy="generation-model-name-error"
                      menuLabel={zh ? "显示可用模型" : "Show available models"}
                      placeholder={
                        manualConnected
                          ? zh
                            ? "选择或输入生成模型"
                            : "Select or enter a model"
                          : zh
                            ? "请先校验 API Key 的连通性"
                            : "Check the API key connection first"
                      }
                      onChange={(value) => {
                        setGenerationModel(value);
                        setModelValidationError(false);
                      }}
                    />
                    {fieldErrors.generationModel && (
                      <FieldError id="generation-model-name-error">
                        {zh ? "请选择生成模型" : "Select a generation model"}
                      </FieldError>
                    )}
                    {modelValidationError && (
                      <FieldError id="generation-model-name-error">
                        {zh
                          ? "该模型当前不可用，请检查名称或选择其他模型"
                          : "This model is unavailable. Check the name or choose another model."}
                      </FieldError>
                    )}
                  </div>
                )}
                {embedding && (
                  <div className="form-field">
                    <label htmlFor="embedding-model-name">
                      {zh ? "嵌入模型" : "Embedding model"}
                    </label>
                    <ModelNameCombobox
                      id="embedding-model-name"
                      value={embeddingModel}
                      disabled={!manualConnected}
                      options={embeddingModelOptions}
                      invalid={
                        fieldErrors.embeddingModel || modelValidationError
                      }
                      describedBy="embedding-model-name-error"
                      menuLabel={zh ? "显示可用模型" : "Show available models"}
                      placeholder={
                        manualConnected
                          ? zh
                            ? "选择或输入嵌入模型"
                            : "Select or enter a model"
                          : zh
                            ? "请先校验 API Key 的连通性"
                            : "Check the API key connection first"
                      }
                      onChange={(value) => {
                        setEmbeddingModel(value);
                        setModelValidationError(false);
                      }}
                    />
                    {fieldErrors.embeddingModel && (
                      <FieldError id="embedding-model-name-error">
                        {zh ? "请选择嵌入模型" : "Select an embedding model"}
                      </FieldError>
                    )}
                    {modelValidationError && (
                      <FieldError id="embedding-model-name-error">
                        {zh
                          ? "该模型当前不可用，请检查名称或选择其他模型"
                          : "This model is unavailable. Check the name or choose another model."}
                      </FieldError>
                    )}
                  </div>
                )}
                <p className="model-apply-note">
                  {zh
                    ? "保存后自动应用配置，并在需要时重启本机服务。"
                    : "Desktop applies the settings and restarts the local service when needed."}
                </p>
              </div>
            )}
          </DialogBody>

          <DialogFooter>
            <Button variant="outline" type="button" onClick={onClose}>
              {zh ? "取消" : "Cancel"}
            </Button>
            {method === "agent" ? (
              <Button
                type="button"
                disabled={checking}
                onClick={() => void checkAgentConfiguration()}
              >
                {checking
                  ? zh
                    ? "正在检查…"
                    : "Checking…"
                  : zh
                    ? "Agent 已完成，检查配置"
                    : "Agent finished, check configuration"}
              </Button>
            ) : (
              <Button type="submit" disabled={checking}>
                {checking
                  ? zh
                    ? "正在校验…"
                    : "Checking…"
                  : zh
                    ? "保存并应用"
                    : "Save and apply"}
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ConnectionCheckDialog({
  result,
  language,
  serviceType = "remote",
  onClose,
}: {
  result: "checking" | "passed" | "failed";
  language: Language;
  serviceType?: "local" | "remote";
  onClose: () => void;
}) {
  const t = copy[language];
  const isLocal = serviceType === "local";
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.checkConnection}</DialogTitle>
        </DialogHeader>
        <DialogBody className="agent-verification-result" aria-live="polite">
          {result === "checking" ? (
            <p>{isLocal ? t.checkingLocalConnection : t.checkingConnection}</p>
          ) : result === "failed" ? (
            <>
              <div className="agent-verification-block">
                <strong className="agent-verification-error">
                  {t.connectionIssueFound}
                </strong>
                <p>{isLocal ? t.localConnectionIssue : t.connectionIssue}</p>
              </div>
              <div className="agent-verification-block">
                <strong className="agent-verification-suggestion">
                  {t.suggestion}
                </strong>
                <p>{t.connectionSuggestion}</p>
              </div>
            </>
          ) : (
            <div className="agent-verification-block">
              <strong className="agent-verification-success">
                <CircleCheck aria-hidden="true" />
                {t.connectionChecked}
              </strong>
              <p>
                {isLocal
                  ? t.localConnectionCheckedHint
                  : t.connectionCheckedHint}
              </p>
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            disabled={result === "checking"}
            onClick={onClose}
          >
            {t.done}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditConnectionDialog({
  profile,
  language,
  simulated,
  lockIdentity = false,
  onState,
  onPreviewSave,
  onClose,
}: {
  profile: ProfileView;
  language: Language;
  simulated: boolean;
  lockIdentity?: boolean;
  onState: (state: DesktopState) => boolean | void;
  onPreviewSave?: (value: {
    name: string;
    endpoint: string;
    credential: string;
  }) => void;
  onClose: () => void;
}) {
  const t = copy[language];
  const [name, setName] = useState(profile.name);
  const [endpoint, setEndpoint] = useState(profile.endpoint);
  const [credential, setCredential] = useState("");
  const [credentialVisible, setCredentialVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function save() {
    const savedName = lockIdentity ? profile.name : name.trim();
    const savedEndpoint = lockIdentity ? endpoint.trim() : profile.endpoint;
    if (busy || !savedName || !savedEndpoint) return;
    setBusy(true);
    setFailed(false);
    try {
      if (!simulated) {
        const next = await desktopApi.save({
          id: profile.id,
          revision: profile.revision,
          name: savedName,
          endpoint: savedEndpoint,
          authentication: profile.authentication,
          caPem: profile.caPem,
          compatibility: profile.compatibility,
          keepCredential:
            !credential &&
            (profile.credentialState === "stored" ||
              profile.credentialState === "session_only"),
          credential: credential
            ? { secret: credential, storage: "persistent" }
            : null,
        });
        onState(next);
      } else {
        onPreviewSave?.({
          name: savedName,
          endpoint: savedEndpoint,
          credential,
        });
      }
      onClose();
    } catch {
      setFailed(true);
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
            void save();
          }}
        >
          <DialogHeader>
            <DialogTitle>{t.editTitle}</DialogTitle>
            <DialogCloseButton label={t.closeDialog} />
          </DialogHeader>
          <DialogBody>
            {failed && (
              <PageFeedback compact kind="error" title={t.saveFailed} />
            )}
            <fieldset disabled={busy} className="form-fields">
              <div className="form-field">
                <label>
                  {t.connectionName}
                  <Input
                    autoFocus={!lockIdentity}
                    value={name}
                    readOnly={lockIdentity}
                    onChange={
                      lockIdentity
                        ? undefined
                        : (event) => setName(event.target.value)
                    }
                  />
                </label>
              </div>
              <div className="form-field">
                <label>
                  {t.serviceAddress}
                  <Input
                    autoFocus={lockIdentity}
                    value={endpoint}
                    readOnly={!lockIdentity}
                    onChange={
                      lockIdentity
                        ? (event) => setEndpoint(event.target.value)
                        : undefined
                    }
                  />
                </label>
              </div>
              {(lockIdentity ||
                profile.authentication !== "unauthenticated_loopback") && (
                <div className="form-field">
                  <label>
                    {t.credential}
                    <span className="password-field">
                      <Input
                        adornment="trailing"
                        type={credentialVisible ? "text" : "password"}
                        value={credential}
                        maxLength={2048}
                        autoComplete="new-password"
                        onChange={(event) => setCredential(event.target.value)}
                        aria-describedby="edit-connection-credential-hint"
                      />
                      <Button
                        variant="field-icon"
                        size="icon-sm"
                        type="button"
                        className="password-toggle"
                        aria-label={
                          credentialVisible
                            ? t.hideCredential
                            : t.showCredential
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
                  </label>
                  <span
                    className="form-hint"
                    id="edit-connection-credential-hint"
                  >
                    {t.credentialHint}
                  </span>
                </div>
              )}
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              {t.cancel}
            </Button>
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? t.saving : t.save}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
