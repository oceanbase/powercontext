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

import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { resolvePreviewMode } from "./preview-mode";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Brain, Home, Server, Settings } from "lucide-react";
import { messages, type Language } from "./messages";
import { desktopApi } from "../shared/ipc";
import type { DesktopState } from "../generated/ipc";
import logo from "../../../src-tauri/icons/brand.png";

import { TopBar } from "./TopBar";
import { Overview, QuickConnectDialog } from "./Overview";
import { MemoryWorkspace } from "./MemoryWorkspace";
import { SettingsPage } from "./SettingsPage";
import {
  loadDesktopPreferences,
  saveDesktopPreferences,
  type DesktopPreferences,
} from "./desktop-preferences";
import { ServiceAgentWorkspace } from "./ServiceAgentWorkspace";
import { LocalServiceDeploymentDialog } from "./LocalServiceDeploymentDialog";
import { connectionMessages } from "./connection-messages";
import { ErrorFeedback } from "./ErrorFeedback";
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
import { activeCheckReport } from "./context";
import { useConfirmation } from "./useConfirmation";
import {
  previewDesktopState,
  previewDataScopeId,
  previewDisconnectedState,
  previewEmptyScopeId,
  previewLocalConnectionId,
  previewRemoteConnectionId,
  previewScopes,
  previewServiceAgents,
} from "./preview-data";
import { buildServiceAgentWorkspaceModel } from "./service-agent-model";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  canShowStartupGuide,
  getProgressiveGuideStep,
  hasAnyConfiguredModel,
  ProgressiveSetupGuide,
  StartupSuccessNotification,
  type ProgressiveGuideStep,
} from "./ProgressiveSetupGuide";

type Page = "home" | "connections" | "memories" | "observation" | "settings";
export function App() {
  const usesNativeWindowControls =
    document.documentElement.dataset.platform === "macos";
  const [preferences, setPreferences] = useState(loadDesktopPreferences);
  const { language, theme } = preferences;
  function updatePreference<Key extends keyof DesktopPreferences>(
    key: Key,
    value: DesktopPreferences[Key],
  ) {
    setPreferences((current) => {
      const next = { ...current, [key]: value };
      saveDesktopPreferences(next);
      return next;
    });
  }
  const [page, setPage] = useState<Page>("home");
  const [, setServiceSetupMode] = useState<"local" | "existing" | null>(null);
  const [menu, setMenu] = useState(false);
  const [startOnLogin, setStartOnLogin] = useState(
    () => globalThis.localStorage?.getItem("start-on-login") === "true",
  );
  const [startupDisabledByUser, setStartupDisabledByUser] = useState(
    () =>
      globalThis.localStorage?.getItem("startup-disabled-by-user") === "true",
  );
  const [configuredModels, setConfiguredModels] = useState({
    generation: false,
    embedding: false,
  });
  const [previousGuideEndedAt, setPreviousGuideEndedAt] = useState<
    number | null
  >(() => {
    const stored = globalThis.localStorage?.getItem("guide-last-ended-at");
    return stored ? Number(stored) : null;
  });
  const [guideClock, setGuideClock] = useState(() => Date.now());
  const [dismissedGuideStep, setDismissedGuideStep] =
    useState<ProgressiveGuideStep>(null);
  const [startupGuideDismissCount, setStartupGuideDismissCount] = useState(() =>
    Math.min(
      2,
      Math.max(
        0,
        Number(
          globalThis.localStorage?.getItem("startup-guide-dismiss-count") ?? 0,
        ) || 0,
      ),
    ),
  );
  const [startupGuideDismissedAt, setStartupGuideDismissedAt] = useState<
    number | null
  >(() => {
    const stored = globalThis.localStorage?.getItem(
      "startup-guide-dismissed-at",
    );
    return stored ? Number(stored) : null;
  });
  const [startupSuccessVisible, setStartupSuccessVisible] = useState(false);
  const [modelConfigurationRequest, setModelConfigurationRequest] = useState<
    "generation" | "embedding" | null
  >(null);
  const [desktop, setDesktop] = useState<DesktopState | null>(null);
  const browserPreviewState = resolvePreviewMode(
    import.meta.env.DEV,
    import.meta.env.MODE,
    globalThis.location?.search ?? "",
  );
  const browserPreviewMode = browserPreviewState !== null;
  const browserPreviewConnected =
    browserPreviewState === "connected" ||
    browserPreviewState === "remote-only" ||
    browserPreviewState === "local-only";
  const previewMode = browserPreviewMode;
  const [previewConnected, setPreviewConnected] = useState(
    browserPreviewConnected,
  );
  const [previewConnectionId, setPreviewConnectionId] = useState(
    browserPreviewState === "remote-only"
      ? previewRemoteConnectionId
      : previewLocalConnectionId,
  );
  const [previewScopeId, setPreviewScopeId] = useState(
    browserPreviewConnected ? previewDataScopeId : previewEmptyScopeId,
  );
  const displayedDesktop = useMemo(
    () =>
      previewMode
        ? previewConnected
          ? previewDesktopState(
              desktop,
              language,
              previewConnectionId,
              previewScopeId,
              browserPreviewState === "remote-only"
                ? [previewRemoteConnectionId]
                : browserPreviewState === "local-only"
                  ? [previewLocalConnectionId]
                  : undefined,
            )
          : previewDisconnectedState(desktop)
        : desktop,
    [
      desktop,
      language,
      previewConnected,
      previewConnectionId,
      previewMode,
      previewScopeId,
      browserPreviewState,
    ],
  );
  const currentState = useRef<DesktopState | null>(null);
  const [nativeError, setNativeError] = useState<unknown>(null);
  const [stateError, setStateError] = useState<unknown>(null);
  const [stateLoading, setStateLoading] = useState(true);
  const stateRequest = useRef(0);
  const [dirty, setDirty] = useState(false);
  const [createScopeOpen, setCreateScopeOpen] = useState(false);
  const [deploymentOpen, setDeploymentOpen] = useState(false);
  const [quickConnectOpen, setQuickConnectOpen] = useState(false);
  const [connectionSelection, setConnectionSelection] = useState<{
    id: string;
    request: number;
  } | null>(null);
  const [pendingConnections, setPendingConnections] = useState(0);
  const contextPending = pendingConnections > 0;
  function connectionPending(pending: boolean) {
    // Pending operations may finish after their editor has unmounted.
    setPendingConnections((current) => current + (pending ? 1 : -1));
  }
  function receiveState(next: DesktopState) {
    if (
      currentState.current &&
      next.generation < currentState.current.generation
    )
      return false;
    currentState.current = next;
    setDesktop(next);
    if (next.active) {
      globalThis.localStorage?.setItem(
        "active-connection-id",
        next.active.connectionId,
      );
      if (next.active.scope)
        globalThis.localStorage?.setItem(
          "active-scope-id",
          next.active.scope.scope_id,
        );
    }
    if (previewMode && next.active?.connectionId.startsWith("preview-")) {
      setPreviewConnected(true);
      setPreviewConnectionId(next.active.connectionId);
      setPreviewScopeId(next.active.scope?.scope_id ?? previewEmptyScopeId);
    }
    return true;
  }
  const { confirm, confirmation } = useConfirmation(language);
  async function confirmSwitch() {
    if (dirty && !(await confirm(connectionMessages[language].discard)))
      return false;
    return true;
  }
  const activeProfile = displayedDesktop?.profiles.find(
    (p) => p.id === displayedDesktop.active?.connectionId,
  );
  const activeReport = activeCheckReport(displayedDesktop);
  const heading = useRef<HTMLHeadingElement>(null);
  const localServerHeading = useRef<HTMLHeadingElement>(null);
  const settingsSection = useRef<"local-server" | undefined>(undefined);
  const scopeReturnFocus = useRef<HTMLElement | null>(null);
  const t = messages[language];
  async function restoreSavedContext(next: DesktopState) {
    if (next.active?.scope || next.profiles.length === 0) return next;
    const storedConnectionId = globalThis.localStorage?.getItem(
      "active-connection-id",
    );
    const profile =
      next.profiles.find((candidate) => candidate.id === storedConnectionId) ??
      next.profiles[0];
    try {
      const checked = next.active
        ? next
        : await desktopApi.check(profile.id, true);
      if (!checked.active || !checked.active.report.compatibilityVerified)
        return checked;
      const storedScopeId = globalThis.localStorage?.getItem("active-scope-id");
      if (storedScopeId) {
        try {
          return await desktopApi.selectScope(
            checked.active.generation,
            storedScopeId,
          );
        } catch {
          // A removed or inaccessible previous Space falls back to the service default.
        }
      }
      const defaultScope = await desktopApi.defaultScope(
        checked.active.generation,
      );
      return await desktopApi.selectScope(
        checked.active.generation,
        defaultScope.scope_id,
      );
    } catch {
      return next;
    }
  }
  async function loadState() {
    const request = ++stateRequest.current;
    setStateLoading(true);
    setStateError(null);
    try {
      receiveState(await restoreSavedContext(await desktopApi.state()));
    } catch (error) {
      if (request === stateRequest.current) setStateError(error);
    } finally {
      if (request === stateRequest.current) setStateLoading(false);
    }
  }
  useEffect(() => {
    if (browserPreviewMode) {
      setStateLoading(false);
      setStateError(null);
      return;
    }
    void loadState();
  }, []);
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    if (isTauri()) void desktopApi.setMenuLanguage?.(language);
  }, [language]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    const timer = globalThis.setInterval(
      () => setGuideClock(Date.now()),
      30_000,
    );
    return () => globalThis.clearInterval(timer);
  }, []);
  useLayoutEffect(() => {
    const localServer =
      page === "connections" && settingsSection.current === "local-server";
    const target = localServer ? localServerHeading.current : heading.current;
    target?.focus();
    if (localServer) target?.scrollIntoView?.({ block: "start" });
  }, [page, connectionSelection]);
  async function navigate(
    next: Page,
    target?: { section?: "local-server"; profileId?: string },
  ) {
    if (
      (next === page && target?.profileId === undefined) ||
      !(await confirmSwitch())
    )
      return;
    settingsSection.current = target?.section;
    setConnectionSelection((current) =>
      target?.profileId === undefined
        ? null
        : { id: target.profileId, request: (current?.request ?? 0) + 1 },
    );
    setDirty(false);
    setPage(next);
    setMenu(false);
  }
  const ready = activeReport?.readiness.value?.status === "ready";
  const localServiceReady =
    ready &&
    !!activeProfile &&
    (activeProfile.endpoint.includes("127.0.0.1") ||
      activeProfile.endpoint.includes("localhost"));
  const serviceAgentModel = useMemo(
    () =>
      buildServiceAgentWorkspaceModel(displayedDesktop, {
        simulated: previewMode,
        agentsByServiceId: previewMode
          ? previewServiceAgents(language)
          : undefined,
      }),
    [displayedDesktop, language, previewMode],
  );
  const localAgentConfigured = serviceAgentModel.services.some(
    (service) => service.kind === "local" && service.agents.length > 0,
  );
  const startupPrerequisitesMet = localServiceReady && localAgentConfigured;
  const startupGuideAllowed = canShowStartupGuide({
    prerequisitesMet: startupPrerequisitesMet,
    startOnLogin,
    disabledByUser: startupDisabledByUser,
    dismissCount: startupGuideDismissCount,
    dismissedAt: startupGuideDismissedAt,
    now: guideClock,
  });
  const progressiveGuideStep = getProgressiveGuideStep({
    prerequisitesMet: startupPrerequisitesMet,
    startupGuideAllowed,
    anyModelConfigured: hasAnyConfiguredModel(configuredModels),
    previousGuideEndedAt,
    now: guideClock,
  });
  const visibleGuideStep =
    progressiveGuideStep === dismissedGuideStep ? null : progressiveGuideStep;
  return (
    <div className="app">
      <a className="skip" href="#main">
        {t.skip}
      </a>
      <header className="mobile-bar">
        <span>PowerContext</span>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={menu}
          aria-controls="navigation"
          onClick={() => setMenu(!menu)}
        >
          {t.menu}
        </Button>
      </header>
      <aside id="navigation" className={menu ? "sidebar open" : "sidebar"}>
        <div className="window-chrome">
          {!usesNativeWindowControls && (
            <div className="window-controls">
              <button
                aria-label={language === "zh" ? "关闭窗口" : "Close window"}
                onClick={() => void getCurrentWindow().close()}
              >
                <svg
                  className="window-control-icon"
                  viewBox="0 0 12 12"
                  aria-hidden="true"
                >
                  <path d="M3.25 3.25 8.75 8.75M8.75 3.25 3.25 8.75" />
                </svg>
              </button>
              <button
                aria-label={
                  language === "zh" ? "最小化窗口" : "Minimize window"
                }
                onClick={() => void getCurrentWindow().minimize()}
              >
                <svg
                  className="window-control-icon"
                  viewBox="0 0 12 12"
                  aria-hidden="true"
                >
                  <path d="M3 6h6" />
                </svg>
              </button>
              <button
                aria-label={
                  language === "zh" ? "最大化窗口" : "Maximize window"
                }
                onClick={() => void getCurrentWindow().toggleMaximize()}
              >
                <svg
                  className="window-control-icon window-control-icon-maximize"
                  viewBox="0 0 12 12"
                  aria-hidden="true"
                >
                  <path d="M2.75 2.75H7.7L2.75 7.7ZM9.25 9.25H4.3l4.95-4.95Z" />
                </svg>
              </button>
            </div>
          )}
          <div
            className="window-drag-handle"
            data-tauri-drag-region
            aria-hidden="true"
          />
        </div>
        <div className="brand" data-tauri-drag-region="deep">
          <img src={logo} alt="" />
          <strong>PowerContext</strong>
          <span className="brand-sub">{t.brandSub}</span>
        </div>
        <nav aria-label={t.menu}>
          {(
            [
              ["home", Home],
              ["memories", Brain],
              ["connections", Server],
            ] as const
          ).map(([item, Icon]) => (
            <Button
              variant="navigation"
              size="sm"
              key={item}
              aria-current={page === item ? "page" : undefined}
              onClick={() => navigate(item)}
            >
              <Icon className="nav-symbol" aria-hidden="true" />
              {t[item]}
            </Button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <Button
            variant="navigation"
            size="sm"
            aria-current={page === "settings" ? "page" : undefined}
            onClick={() => navigate("settings")}
          >
            <Settings className="nav-symbol" aria-hidden="true" />
            {t.settings}
          </Button>
        </div>
      </aside>
      <div className="content">
        <TopBar
          state={displayedDesktop}
          language={language}
          loading={stateLoading}
          contextPending={contextPending}
          hideContent={page === "connections" || page === "settings"}
          onManageConnections={(profileId) =>
            navigate("connections", { profileId })
          }
          onSelectConnection={(profileId) => {
            if (profileId === displayedDesktop?.active?.connectionId) return;
            if (previewMode && profileId.startsWith("preview-")) {
              setPreviewConnectionId(profileId);
              setPreviewScopeId(previewEmptyScopeId);
              return;
            }
            connectionPending(true);
            void (async () => {
              try {
                const checked = await desktopApi.check(profileId, true);
                if (
                  !checked.active ||
                  checked.active.connectionId !== profileId
                )
                  throw { code: "compatibility_unverified" };
                const defaultScope = await desktopApi.defaultScope(
                  checked.active.generation,
                );
                receiveState(
                  await desktopApi.selectScope(
                    checked.active.generation,
                    defaultScope.scope_id,
                  ),
                );
              } catch (error) {
                setNativeError(error);
              } finally {
                connectionPending(false);
              }
            })();
          }}
          onSelectScope={(scope) => {
            if (previewMode && scope.scope_id.startsWith("preview-")) {
              setPreviewScopeId(scope.scope_id);
              return;
            }
            if (
              !desktop?.active ||
              scope.scope_id === desktop.active.scope?.scope_id
            )
              return;
            const generation = desktop.generation + 1;
            receiveState({
              ...desktop,
              generation,
              active: { ...desktop.active, generation, scope },
            });
          }}
          onCreateScope={(trigger) => {
            scopeReturnFocus.current = trigger;
            setCreateScopeOpen(true);
          }}
        />
        <main id="main">
          <h1 className="sr-only" tabIndex={-1} ref={heading}>
            {t[page]}
          </h1>
          {contextPending && (
            <PageFeedback kind="loading" title={t.connectionUpdating}>
              <p>{t.connectionUpdatingHint}</p>
            </PageFeedback>
          )}
          {stateLoading && (
            <PageFeedback kind="loading" title={t.loadingState} />
          )}
          {!stateLoading && stateError != null && (
            <ErrorFeedback
              error={stateError}
              language={language}
              title={t.stateFailed}
              action={
                <Button variant="outline" onClick={() => void loadState()}>
                  {t.retryState}
                </Button>
              }
            />
          )}
          {nativeError != null && (
            <ErrorFeedback
              error={nativeError}
              language={language}
              action={
                <Button
                  variant="outline"
                  onClick={() => navigate("connections")}
                >
                  {t.manageConnections}
                </Button>
              }
            />
          )}
          {page === "home" && (
            <Overview
              state={displayedDesktop}
              language={language}
              onState={receiveState}
              onOpenLocalServer={() => setDeploymentOpen(true)}
              contextPending={contextPending}
            />
          )}
          {page === "memories" && (
            <MemoryWorkspace
              key={`memory-${displayedDesktop?.active?.connectionId ?? "none"}`}
              state={displayedDesktop}
              language={language}
              onManageConnections={() => navigate("connections")}
              onOpenLocalServer={() => setDeploymentOpen(true)}
              onOpenExistingServer={() => setQuickConnectOpen(true)}
              contextPending={contextPending}
            />
          )}
          {page === "connections" && (
            <ServiceAgentWorkspace
              state={displayedDesktop}
              language={language}
              onState={receiveState}
              model={serviceAgentModel}
              startOnLogin={startOnLogin}
              onStartOnLoginChange={(enabled) => {
                setStartOnLogin(enabled);
                globalThis.localStorage?.setItem(
                  "start-on-login",
                  String(enabled),
                );
                if (enabled) {
                  const endedAt = Date.now();
                  setPreviousGuideEndedAt(endedAt);
                  setGuideClock(endedAt);
                  globalThis.localStorage?.setItem(
                    "guide-last-ended-at",
                    String(endedAt),
                  );
                } else {
                  setStartupDisabledByUser(true);
                  globalThis.localStorage?.setItem(
                    "startup-disabled-by-user",
                    "true",
                  );
                  setPreviousGuideEndedAt(null);
                  globalThis.localStorage?.removeItem("guide-last-ended-at");
                }
              }}
              configuredModels={configuredModels}
              onConfiguredModelsChange={setConfiguredModels}
              modelConfigurationRequest={modelConfigurationRequest}
              onModelConfigurationRequestHandled={() =>
                setModelConfigurationRequest(null)
              }
              onDeployLocal={() => {
                setServiceSetupMode("local");
                if (previewMode)
                  receiveState(
                    previewDesktopState(
                      desktop,
                      language,
                      previewLocalConnectionId,
                      previewEmptyScopeId,
                    ),
                  );
              }}
              onDeploymentComplete={() => {
                setPage("home");
                setMenu(false);
              }}
              onCreateSpace={(trigger) => {
                scopeReturnFocus.current = trigger;
                setCreateScopeOpen(true);
              }}
            />
          )}
          {page === "settings" && (
            <SettingsPage
              preferences={preferences}
              onPreferenceChange={updatePreference}
            />
          )}
        </main>
      </div>
      {createScopeOpen && (
        <CreateSpaceDialog
          language={language}
          state={desktop}
          onClose={() => setCreateScopeOpen(false)}
          onCreated={(scope) => {
            if (!desktop?.active) return;
            const generation = desktop.generation + 1;
            receiveState({
              ...desktop,
              generation,
              active: { ...desktop.active, generation, scope },
            });
            setCreateScopeOpen(false);
          }}
          returnFocus={() => scopeReturnFocus.current}
        />
      )}
      {deploymentOpen && (
        <LocalServiceDeploymentDialog
          language={language}
          onClose={() => setDeploymentOpen(false)}
          run={async (signal) => {
            if (signal.aborted) return;
            if (!previewMode)
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
            receiveState(
              previewDesktopState(
                desktop,
                language,
                previewLocalConnectionId,
                previewEmptyScopeId,
              ),
            );
            setDeploymentOpen(false);
            setMenu(false);
          }}
        />
      )}
      {quickConnectOpen && (
        <QuickConnectDialog
          state={displayedDesktop}
          language={language}
          onState={receiveState}
          onClose={() => setQuickConnectOpen(false)}
        />
      )}
      {startupSuccessVisible ? (
        <StartupSuccessNotification
          language={language}
          onClose={() => setStartupSuccessVisible(false)}
        />
      ) : visibleGuideStep ? (
        <ProgressiveSetupGuide
          step={visibleGuideStep}
          language={language}
          onClose={() => {
            if (visibleGuideStep !== "startup") {
              setDismissedGuideStep(visibleGuideStep);
              return;
            }
            const nextCount = Math.min(2, startupGuideDismissCount + 1);
            const dismissedAt = Date.now();
            setStartupGuideDismissCount(nextCount);
            setStartupGuideDismissedAt(dismissedAt);
            setPreviousGuideEndedAt(dismissedAt);
            setGuideClock(dismissedAt);
            globalThis.localStorage?.setItem(
              "startup-guide-dismiss-count",
              String(nextCount),
            );
            globalThis.localStorage?.setItem(
              "startup-guide-dismissed-at",
              String(dismissedAt),
            );
            globalThis.localStorage?.setItem(
              "guide-last-ended-at",
              String(dismissedAt),
            );
          }}
          onAction={() => {
            setDismissedGuideStep(null);
            if (visibleGuideStep === "startup") {
              const completedAt = Date.now();
              setStartOnLogin(true);
              setPreviousGuideEndedAt(completedAt);
              setGuideClock(completedAt);
              globalThis.localStorage?.setItem("start-on-login", "true");
              globalThis.localStorage?.setItem(
                "guide-last-ended-at",
                String(completedAt),
              );
              globalThis.localStorage?.removeItem(
                "startup-guide-dismiss-count",
              );
              globalThis.localStorage?.removeItem("startup-guide-dismissed-at");
              setStartupSuccessVisible(true);
              return;
            }
            setModelConfigurationRequest("generation");
            void navigate("connections");
          }}
          onSecondaryAction={() => {
            setDismissedGuideStep(null);
            setModelConfigurationRequest("embedding");
            void navigate("connections");
          }}
        />
      ) : null}
      {confirmation}
    </div>
  );
}

export function CreateSpaceDialog({
  language,
  state,
  onClose,
  onCreated,
  returnFocus,
}: {
  language: Language;
  state: DesktopState | null;
  onClose: () => void;
  onCreated: (scope: import("../generated/ipc").ScopeDescriptor) => void;
  returnFocus: () => HTMLElement | null;
}) {
  const zh = language === "zh";
  const [title, setTitle] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const previewScope = state?.active?.scope?.scope_id.startsWith("preview-");
  const normalizedTitle = title.trim();
  const existingScopes = previewScope
    ? [
        ...previewScopes(language),
        ...(state?.active?.scope ? [state.active.scope] : []),
      ]
    : state?.active?.scope
      ? [state.active.scope]
      : [];
  const duplicateTitle = existingScopes.some(
    (scope) => scope.title.trim() === normalizedTitle,
  );
  const titleError = submitted
    ? !normalizedTitle
      ? zh
        ? "请输入空间名称"
        : "Enter a space name"
      : duplicateTitle
        ? zh
          ? "当前连接下已存在同名空间"
          : "A space with this name already exists in this connection"
        : null
    : null;

  async function create() {
    if (busy) return;
    if (!normalizedTitle || duplicateTitle) {
      setSubmitted(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (!previewScope) {
        setError(
          zh
            ? "当前服务尚未提供创建记忆空间的能力"
            : "This service does not provide memory-space creation yet",
        );
        return;
      }
      await new Promise((resolve) => globalThis.setTimeout(resolve, 180));
      const id = `preview-created-${Date.now()}`;
      onCreated({
        scope_id: id,
        title: normalizedTitle,
        summary: "",
        parent_scope_id: null,
        context_references: [],
        external_references: [],
        version: 1,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent returnFocus={returnFocus}>
        <form
          className="quick-connect-form create-space-form contents"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {zh ? "创建记忆空间" : "Create memory space"}
            </DialogTitle>
            <DialogCloseButton label={zh ? "关闭弹窗" : "Close dialog"} />
          </DialogHeader>
          <DialogBody>
            {error && <PageFeedback compact kind="error" title={error} />}
            <fieldset disabled={busy} className="form-fields">
              <Field>
                <FieldLabel>
                  {zh ? "空间名称" : "Space name"}
                  <Input
                    autoFocus
                    value={title}
                    maxLength={128}
                    aria-invalid={titleError ? true : undefined}
                    aria-describedby={
                      titleError ? "space-title-error" : undefined
                    }
                    onChange={(event) => {
                      setTitle(event.target.value);
                      setError(null);
                    }}
                  />
                </FieldLabel>
                {titleError && (
                  <FieldError id="space-title-error">{titleError}</FieldError>
                )}
              </Field>
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              {zh ? "取消" : "Cancel"}
            </Button>
            <Button type="submit" size="sm" disabled={busy}>
              {busy
                ? zh
                  ? "正在创建…"
                  : "Creating…"
                : zh
                  ? "创建空间"
                  : "Create space"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
