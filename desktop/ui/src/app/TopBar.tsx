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

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { DesktopState } from "../generated/ipc";
import { messages, type Language } from "./messages";
import { connectionError, connectionMessages } from "./connection-messages";
import { activeCheckReport } from "./context";

type Props = {
  state: DesktopState | null;
  language: Language;
  loading?: boolean;
  contextPending?: boolean;
  onManageConnections: (profileId?: string) => void;
  onOpenScopes: (trigger: HTMLElement) => void;
  onDisconnect: () => void;
};
export function TopBar({
  state,
  language,
  loading = false,
  contextPending = false,
  onManageConnections,
  onOpenScopes,
  onDisconnect,
}: Props) {
  const t = messages[language];
  const ct = connectionMessages[language];
  const [open, setOpen] = useState<"connection" | "identity" | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const connectionTrigger = useRef<HTMLButtonElement>(null);
  const identityTrigger = useRef<HTMLButtonElement>(null);
  const connectionMenu = useRef<HTMLDivElement>(null);
  const menuStart = useRef(0);
  const connectionMenuId = useId();
  const identityId = useId();
  function closePopup() {
    (open === "connection"
      ? connectionTrigger
      : identityTrigger
    ).current?.focus();
    setOpen(null);
  }
  useLayoutEffect(() => {
    if (open !== "connection") return;
    const items = connectionMenu.current?.querySelectorAll<HTMLButtonElement>(
      '[role="menuitem"]:not(:disabled)',
    );
    items?.[menuStart.current === -1 ? items.length - 1 : 0]?.focus();
  }, [open]);
  useEffect(() => {
    const bar = wrap.current;
    if (!bar) return;
    const update = () => {
      document.documentElement.style.setProperty(
        "--topbar-height",
        `${bar.getBoundingClientRect().height}px`,
      );
    };
    update();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(bar);
    return () => {
      observer?.disconnect();
      document.documentElement.style.removeProperty("--topbar-height");
    };
  }, []);
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (event.target instanceof Node && !wrap.current?.contains(event.target))
        setOpen(null);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closePopup();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);
  const active = state?.active;
  const report = activeCheckReport(state);
  const activeProfile = state?.profiles.find(
    (p) => p.id === active?.connectionId,
  );
  const principal = report?.anonymousAccess
    ? null
    : report?.identity.value?.principal;
  const identityText = !active
    ? t.notConnected
    : report?.anonymousAccess
      ? t.anonymousIdentity
      : principal
        ? (principal.description ?? `${principal.type}: ${principal.id}`)
        : t.identityUnverified;
  const connectionText = active
    ? t.activeContext
    : state
      ? t.notConnected
      : loading
        ? t.loadingState
        : t.stateUnavailable;
  return (
    <div className="topbar" ref={wrap}>
      <div className="menu-wrap">
        <button
          className="menu-trigger"
          aria-expanded={open === "connection"}
          aria-haspopup="menu"
          aria-controls={connectionMenuId}
          aria-label={`${t.current}: ${activeProfile?.name ?? connectionText}`}
          ref={connectionTrigger}
          onClick={() => {
            menuStart.current = 0;
            setOpen(open === "connection" ? null : "connection");
          }}
          onKeyDown={(event) => {
            if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
            event.preventDefault();
            menuStart.current = event.key === "ArrowUp" ? -1 : 0;
            setOpen("connection");
          }}
        >
          <span className="label">{activeProfile?.name ?? connectionText}</span>
          <span aria-hidden="true">▾</span>
        </button>
        {open === "connection" && (
          <div
            className="menu"
            role="menu"
            aria-label={t.manageConnections}
            id={connectionMenuId}
            ref={connectionMenu}
            onBlur={(event) => {
              if (
                event.relatedTarget instanceof Node &&
                !connectionMenu.current?.contains(event.relatedTarget) &&
                event.relatedTarget !== connectionTrigger.current
              )
                setOpen(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                event.preventDefault();
                closePopup();
                return;
              }
              if (event.key === "Tab") {
                closePopup();
                return;
              }
              const items = Array.from(
                connectionMenu.current?.querySelectorAll<HTMLButtonElement>(
                  '[role="menuitem"]:not(:disabled)',
                ) ?? [],
              );
              const current = items.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              let index: number;
              if (event.key === "ArrowDown")
                index = (current + 1) % items.length;
              else if (event.key === "ArrowUp")
                index = (current + items.length - 1) % items.length;
              else if (event.key === "Home") index = 0;
              else if (event.key === "End") index = items.length - 1;
              else return;
              event.preventDefault();
              items[index]?.focus();
            }}
          >
            {state?.profiles.map((p) => (
              <button
                key={p.id}
                className="menu-item"
                role="menuitem"
                tabIndex={-1}
                onClick={() => {
                  closePopup();
                  onManageConnections(p.id);
                }}
              >
                <span>
                  {p.name}
                  {state.active?.connectionId === p.id
                    ? ` · ${ct.activeBadge}`
                    : ""}
                </span>
                <span className="meta">{p.endpoint}</span>
              </button>
            ))}
            {state?.profiles.length ? <div className="menu-separator" /> : null}
            <button
              className="menu-item"
              role="menuitem"
              tabIndex={-1}
              onClick={() => {
                closePopup();
                onManageConnections();
              }}
            >
              {t.manageConnections}
            </button>
            {state?.active && (
              <>
                <div className="menu-separator" />
                <button
                  className="menu-item"
                  role="menuitem"
                  tabIndex={-1}
                  disabled={contextPending}
                  onClick={() => {
                    closePopup();
                    onDisconnect();
                  }}
                >
                  {ct.disconnect}
                </button>
              </>
            )}
            <p className="menu-note">{t.switchHint}</p>
          </div>
        )}
      </div>
      <span className="status-pill" role="status">
        <span aria-hidden="true" className="dot" />
        {connectionText}
      </span>
      <span aria-hidden="true" className="divider" />
      <div className="topbar-group">
        <span className="topbar-label">{t.scopeLabel}</span>
        <button
          className="menu-trigger scope-trigger"
          onClick={(event) => onOpenScopes(event.currentTarget)}
          disabled={!state || contextPending}
          aria-haspopup="dialog"
        >
          <span className="scope-label">
            {active?.scope?.title ?? t.noScopeSelected}
            {active?.scope && <code>{active.scope.scope_id}</code>}
          </span>
          <span aria-hidden="true">▾</span>
        </button>
      </div>
      <span className="topbar-spacer" />
      <div className="menu-wrap">
        <button
          className="menu-trigger"
          aria-expanded={open === "identity"}
          aria-controls={identityId}
          ref={identityTrigger}
          onClick={() => setOpen(open === "identity" ? null : "identity")}
        >
          <span className="label">{identityText}</span>
          <span aria-hidden="true">▾</span>
        </button>
        {open === "identity" && (
          <div
            className="menu right"
            role="region"
            aria-label={t.identityDetails}
            id={identityId}
          >
            <p className="menu-note">
              <strong>{t.identityDetails}</strong>
            </p>
            {principal ? (
              <>
                <p className="menu-note">
                  {principal.type}: {principal.id}
                  {principal.description ? ` · ${principal.description}` : ""}
                </p>
                <p className="menu-note">
                  {t.accessMode}: {report?.identity.value?.mode ?? t.unverified}
                </p>
              </>
            ) : (
              <p className="menu-note">
                {report?.anonymousAccess ? ct.noIdentity : t.identityUnverified}
                {report?.identity.error && (
                  <> {connectionError(report.identity.error, language)}</>
                )}
              </p>
            )}
          </div>
        )}
      </div>
      <span aria-hidden="true" className="avatar">
        {identityText.charAt(0).toUpperCase()}
      </span>
      {active && (
        <div
          className="global-context"
          role="group"
          aria-label={t.globalContext}
        >
          {activeProfile && (
            <p className="context-endpoint">{activeProfile.endpoint}</p>
          )}
          <dl className="context-facts">
            <div>
              <dt>{t.compatibility}</dt>
              <dd>
                {report?.compatibilityVerified
                  ? t.compatibilityVerified
                  : t.unverified}
              </dd>
            </div>
            <div>
              <dt>{t.liveness}</dt>
              <dd>
                {report?.liveness.error
                  ? connectionError(report.liveness.error, language)
                  : (report?.liveness.value?.status ?? t.unverified)}
              </dd>
            </div>
            <div>
              <dt>{t.readiness}</dt>
              <dd>
                {report?.readiness.error
                  ? connectionError(report.readiness.error, language)
                  : report?.readiness.value?.status === "ready"
                    ? t.serviceReady
                    : report?.readiness.value
                      ? t.serviceNotReady
                      : t.unverified}
              </dd>
            </div>
          </dl>
          <p className="small">
            {report && (
              <>
                {t.lastChecked}:{" "}
                {new Date(report.checkedAt * 1000).toLocaleString(
                  language === "zh" ? "zh-CN" : "en-US",
                )}
                .{" "}
              </>
            )}
            {t.readinessNote}
          </p>
        </div>
      )}
    </div>
  );
}
