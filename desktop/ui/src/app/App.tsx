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

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { messages, type Language } from "./messages";
import { getFoundationInfo, desktopApi } from "../shared/ipc";
import type {
  FoundationInfo,
  DesktopState,
  WriteRecord,
} from "../generated/ipc";
import logo from "../../../src-tauri/icons/brand.png";
import homeIcon from "../assets/overview.svg";
import connectionsIcon from "../assets/connections.svg";
import memoryIcon from "../assets/memory.svg";
import settingsIcon from "../assets/settings.svg";
import observationIcon from "../assets/retrieval.svg";

import { useLocalServer } from "./useLocalServer";
import { opsApi } from "../shared/ops";
import { TopBar } from "./TopBar";
import { Overview } from "./Overview";
import {
  MemoryWorkspace,
  SubmittedDraftRecovery,
  type SubmittedDraft,
} from "./MemoryWorkspace";
import { LocalServerSettings } from "./LocalServerSettings";
import { Help } from "./Help";
import { Diagnostics } from "./Diagnostics";
import { Connections } from "./Connections";
import { Scopes } from "./Scopes";
import { Observation, type ObservationTab } from "./Observation";
import { connectionMessages } from "./connection-messages";
import { PageFeedback, ErrorFeedback } from "./PageFeedback";
import { activeCheckReport } from "./context";
import { Modal } from "./Modal";
import { useConfirmation } from "./useConfirmation";

type Page = "home" | "connections" | "memories" | "observation" | "settings";
type Theme = "light" | "dark" | "system";
export function App() {
  const localServer = useLocalServer();
  const [connectingLocal, setConnectingLocal] = useState(false);
  const [language, setLanguage] = useState<Language>("zh");
  const [theme, setTheme] = useState<Theme>("system");
  const [page, setPage] = useState<Page>("home");
  const [observationTab, setObservationTab] =
    useState<ObservationTab>("runtime");
  const [menu, setMenu] = useState(false);
  const [host, setHost] = useState<FoundationInfo | null>(null);
  const [desktop, setDesktop] = useState<DesktopState | null>(null);
  const [nativeError, setNativeError] = useState<unknown>(null);
  const [stateError, setStateError] = useState<unknown>(null);
  const [stateLoading, setStateLoading] = useState(true);
  const stateRequest = useRef(0);
  const [dirty, setDirty] = useState(false);
  const uiIntent = useRef(0);
  function editorDirty(next: boolean) {
    uiIntent.current++;
    setDirty(next);
  }
  const [submittedDrafts, setSubmittedDrafts] = useState<SubmittedDraft[]>([]);
  const submissionSequence = useRef(0);
  function beginSubmission(text: string, target: SubmittedDraft["target"]) {
    const id = ++submissionSequence.current;
    setSubmittedDrafts((current) => [
      ...current,
      { id, text, target, phase: "pending" },
    ]);
    return (record: WriteRecord | null) => {
      // Update only a retained intent. Explicit discard removes its identity,
      // so late receipts cannot revive it or clear a different submission.
      setSubmittedDrafts((current) =>
        current.flatMap((draft) => {
          if (draft.id !== id) return [draft];
          if (record?.status === "succeeded") return [];
          return [
            {
              ...draft,
              target: record?.context ?? draft.target,
              phase: record?.status ?? "unconfirmed",
            },
          ];
        }),
      );
    };
  }
  const [scopeOpen, setScopeOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [connectionSelection, setConnectionSelection] = useState<{
    id: string;
    request: number;
  } | null>(null);
  const [pendingConnections, setPendingConnections] = useState(0);
  const [pendingScopes, setPendingScopes] = useState(0);
  const contextPending = pendingConnections > 0 || pendingScopes > 0;
  function scopePending(pending: boolean) {
    if (pending) setNativeError(null);
    setPendingScopes((current) => current + (pending ? 1 : -1));
  }
  function connectionPending(pending: boolean) {
    // Pending operations may finish after their editor has unmounted.
    setPendingConnections((current) => current + (pending ? 1 : -1));
  }
  const acceptedGeneration = useRef(-1);
  function receiveState(next: DesktopState) {
    if (next.generation < acceptedGeneration.current) return false;
    acceptedGeneration.current = next.generation;
    setDesktop(next);
    return true;
  }
  const { confirm, confirmation } = useConfirmation(language);
  async function confirmSwitch() {
    if (
      (dirty || submittedDrafts.length > 0) &&
      !(await confirm(connectionMessages[language].discard))
    )
      return false;
    setSubmittedDrafts([]);
    return true;
  }
  const activeProfile = desktop?.profiles.find(
    (p) => p.id === desktop.active?.connectionId,
  );
  const activeReport = activeCheckReport(desktop);
  const heading = useRef<HTMLHeadingElement>(null);
  const localServerHeading = useRef<HTMLHeadingElement>(null);
  const settingsSection = useRef<"local-server" | undefined>(undefined);
  const scopeReturnFocus = useRef<HTMLElement | null>(null);
  const t = messages[language];
  const ct = connectionMessages[language];
  async function loadState() {
    const request = ++stateRequest.current;
    setStateLoading(true);
    setStateError(null);
    try {
      receiveState(await desktopApi.state());
    } catch (error) {
      if (request === stateRequest.current) setStateError(error);
    } finally {
      if (request === stateRequest.current) setStateLoading(false);
    }
  }
  useEffect(() => {
    void loadState();
    getFoundationInfo()
      .then(setHost)
      .catch(() => setHost(null));
  }, []);
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useLayoutEffect(() => {
    const localServer =
      page === "settings" && settingsSection.current === "local-server";
    const target = localServer ? localServerHeading.current : heading.current;
    target?.focus();
    if (localServer) target?.scrollIntoView?.({ block: "start" });
  }, [page, connectionSelection]);
  function openScopes(trigger: HTMLElement) {
    uiIntent.current++;
    scopeReturnFocus.current = trigger;
    setScopeOpen(true);
  }
  async function navigate(
    next: Page,
    target?: { section?: "local-server"; profileId?: string },
  ) {
    if (
      (next === page && target?.profileId === undefined) ||
      !(await confirmSwitch())
    )
      return;
    uiIntent.current++;
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
  async function disconnect() {
    if (!(await confirmSwitch())) return;
    connectionPending(true);
    void desktopApi
      .disconnect()
      .then(receiveState)
      .catch(setNativeError)
      .finally(() => connectionPending(false));
  }
  async function refresh() {
    setRefreshing(true);
    setNativeError(null);
    connectionPending(true);
    try {
      receiveState(
        desktop?.active
          ? await desktopApi.check(desktop.active.connectionId, false)
          : await desktopApi.state(),
      );
    } catch (e) {
      setNativeError(e);
      try {
        receiveState(await desktopApi.state());
      } catch {
        /* Preserve the original check failure. */
      }
    } finally {
      connectionPending(false);
      setRefreshing(false);
    }
  }
  const ready = activeReport?.readiness.value?.status === "ready";
  return (
    <div className="app">
      <a className="skip" href="#main">
        {t.skip}
      </a>
      <header className="mobile-bar">
        <span>PowerContext</span>
        <button
          aria-expanded={menu}
          aria-controls="navigation"
          onClick={() => setMenu(!menu)}
        >
          {t.menu}
        </button>
      </header>
      <aside id="navigation" className={menu ? "sidebar open" : "sidebar"}>
        <div className="brand">
          <img src={logo} alt="" />
          <strong>PowerContext</strong>
          <span className="brand-sub">{t.brandSub}</span>
        </div>
        <span className="brand-badge">{t.foundation}</span>
        <nav aria-label={t.menu}>
          {(["home", "connections", "memories", "observation"] as const).map(
            (item, index) => (
              <button
                key={item}
                aria-current={page === item ? "page" : undefined}
                onClick={() => navigate(item)}
              >
                <img
                  className="nav-symbol"
                  alt=""
                  src={
                    [homeIcon, connectionsIcon, memoryIcon, observationIcon][
                      index
                    ]
                  }
                />
                {t[item]}
              </button>
            ),
          )}
        </nav>
        <div className="sidebar-bottom">
          <button
            aria-current={page === "settings" ? "page" : undefined}
            onClick={() => navigate("settings")}
          >
            <img className="nav-symbol" alt="" src={settingsIcon} />
            {t.settings}
          </button>
          <div className="sidebar-status">
            <p className="status">
              <span aria-hidden="true" className="dot" />
              {activeProfile?.name ??
                (stateLoading
                  ? t.loadingState
                  : desktop
                    ? t.disconnected
                    : t.stateUnavailable)}
            </p>
            <p className="caption">{t.sidebarRunning}</p>
          </div>
        </div>
      </aside>
      <div className="content">
        <TopBar
          state={desktop}
          language={language}
          loading={stateLoading}
          contextPending={contextPending}
          onManageConnections={(profileId) =>
            navigate("connections", { profileId })
          }
          onOpenScopes={openScopes}
          onDisconnect={disconnect}
        />
        <main id="main">
          <h1 className="sr-only" tabIndex={-1} ref={heading}>
            {t[page]}
          </h1>
          {contextPending && (
            <PageFeedback
              kind="loading"
              title={
                pendingScopes > 0 ? t.selectingScope : t.connectionUpdating
              }
            >
              <p>
                {pendingScopes > 0
                  ? t.scopeUpdatingHint
                  : t.connectionUpdatingHint}
              </p>
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
                <button onClick={() => void loadState()}>{t.retryState}</button>
              }
            />
          )}
          {nativeError != null && (
            <ErrorFeedback
              error={nativeError}
              language={language}
              action={
                <button onClick={() => navigate("connections")}>
                  {t.manageConnections}
                </button>
              }
            />
          )}
          {page === "memories" && desktop?.active && (
            <div className="context-line">
              <span>{activeProfile?.name}</span>
              <span aria-hidden="true">·</span>
              <span>{activeProfile?.endpoint}</span>
              <span aria-hidden="true">·</span>
              <span>{desktop.active.scope?.title ?? t.noScopeSelected}</span>
              {desktop.active.scope && (
                <code>{desktop.active.scope.scope_id}</code>
              )}
              <span className={ready ? "badge success" : "badge subtle"}>
                {ready ? t.serviceReady : t.unverified}
              </span>
            </div>
          )}
          {page === "home" && !desktop?.active && (
            <div className="actions">
              <button
                onClick={() =>
                  navigate("settings", { section: "local-server" })
                }
              >
                {t.setUpLocally}
              </button>
            </div>
          )}
          {page === "home" && (
            <Overview
              state={desktop}
              language={language}
              onNavigate={navigate}
              refreshing={refreshing}
              onRefresh={() => void refresh()}
            />
          )}
          {page === "memories" && (
            <>
              {submittedDrafts
                .filter(
                  (draft) =>
                    draft.target.generation !== desktop?.active?.generation,
                )
                .map((draft) => (
                  <SubmittedDraftRecovery
                    key={draft.id}
                    draft={draft}
                    language={language}
                    onDiscard={() =>
                      setSubmittedDrafts((current) =>
                        current.filter((item) => item.id !== draft.id),
                      )
                    }
                  />
                ))}
              <MemoryWorkspace
                key={`memory-${desktop?.active?.generation ?? "none"}`}
                state={desktop}
                language={language}
                onState={receiveState}
                onDirty={editorDirty}
                onSubmission={beginSubmission}
                onManageConnections={() => navigate("connections")}
                onOpenScopes={openScopes}
                onOpenLocalServer={() =>
                  navigate("settings", { section: "local-server" })
                }
                contextPending={contextPending}
              />
            </>
          )}
          {page === "connections" && (
            <Connections
              key={connectionSelection?.request ?? 0}
              initialProfileId={connectionSelection?.id}
              state={desktop}
              language={language}
              onState={receiveState}
              onDirty={editorDirty}
              onPendingChange={connectionPending}
            />
          )}
          {page === "observation" && (
            <Observation
              language={language}
              tab={observationTab}
              onTabChange={setObservationTab}
            />
          )}
          {page === "settings" && (
            <div className="stack settings">
              <section className="card">
                <div className="setting-row">
                  <label htmlFor="language">{t.language}</label>
                  <select
                    id="language"
                    value={language}
                    onChange={(e) => setLanguage(e.target.value as Language)}
                  >
                    <option value="zh">简体中文</option>
                    <option value="en">English</option>
                  </select>
                </div>
                <div className="setting-row">
                  <label htmlFor="theme">{t.theme}</label>
                  <select
                    id="theme"
                    value={theme}
                    onChange={(e) => setTheme(e.target.value as Theme)}
                  >
                    <option value="system">{t.system}</option>
                    <option value="light">{t.light}</option>
                    <option value="dark">{t.dark}</option>
                  </select>
                </div>
              </section>
              <LocalServerSettings
                controller={localServer}
                connecting={connectingLocal}
                connectionAvailable={
                  !!desktop && !stateLoading && !contextPending
                }
                onBeforeApply={async (plan) => {
                  if (
                    activeProfile &&
                    plan.target.endpoint &&
                    new URL(activeProfile.endpoint).origin ===
                      new URL(plan.target.endpoint).origin
                  ) {
                    if (!(await confirmSwitch())) return false;
                    try {
                      receiveState(
                        await desktopApi.invalidate(activeProfile.id),
                      );
                      setDirty(false);
                    } catch (error) {
                      setNativeError(error);
                      return false;
                    }
                  }
                  return true;
                }}
                onConnect={async () => {
                  const intent = uiIntent.current;
                  if (!desktop || connectingLocal || !(await confirmSwitch()))
                    return;
                  if (intent !== uiIntent.current) return;
                  setConnectingLocal(true);
                  connectionPending(true);
                  setNativeError(null);
                  try {
                    const next = await opsApi.connect(desktop.generation);
                    const accepted = receiveState(next);
                    if (
                      accepted &&
                      next.active?.scope &&
                      intent === uiIntent.current
                    ) {
                      setDirty(false);
                      setPage("memories");
                    }
                  } catch (error) {
                    setNativeError(error);
                    try {
                      receiveState(await desktopApi.state());
                    } catch {
                      /* Keep the connection error visible. */
                    }
                  } finally {
                    setConnectingLocal(false);
                    connectionPending(false);
                  }
                }}
                language={language}
                headingRef={localServerHeading}
                onManageConnections={() => navigate("connections")}
              />
              <Help language={language} />
              <Diagnostics language={language} />
              <section className="card">
                <h2>{t.version}</h2>
                <dl>
                  <div>
                    <dt>{t.version}</dt>
                    <dd>{host?.version ?? t.unknown}</dd>
                  </div>
                  <div>
                    <dt>{t.native}</dt>
                    <dd role="status">
                      {host ? t.nativeReady : t.unavailable}
                    </dd>
                  </div>
                </dl>
                <p>{t.boundary}</p>
              </section>
            </div>
          )}
        </main>
      </div>
      {scopeOpen && (
        <Modal
          label={ct.scope}
          onClose={() => setScopeOpen(false)}
          returnFocus={() => scopeReturnFocus.current}
        >
          <div className="modal-heading">
            <h2>{ct.scope}</h2>
            <button onClick={() => setScopeOpen(false)}>{t.closeDialog}</button>
          </div>
          <Scopes
            key={`scopes-${desktop?.active?.generation ?? "none"}`}
            state={desktop}
            language={language}
            onState={receiveState}
            confirmSwitch={confirmSwitch}
            onPendingChange={scopePending}
            onSelectionError={setNativeError}
            onSelected={() => {
              setDirty(false);
              setScopeOpen(false);
            }}
            hideHeading
            onManageConnections={() => {
              scopeReturnFocus.current = heading.current;
              setScopeOpen(false);
              navigate("connections");
            }}
          />
        </Modal>
      )}
      {confirmation}
    </div>
  );
}
