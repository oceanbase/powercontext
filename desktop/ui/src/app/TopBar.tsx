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

import { useEffect, useRef, useState } from "react";
import type { DesktopState } from "../generated/ipc";
import { messages, type Language } from "./messages";
import { connectionMessages } from "./connection-messages";

type Props = {
  state: DesktopState | null;
  language: Language;
  onManageConnections: () => void;
  onOpenScopes: () => void;
  onDisconnect: () => void;
};
export function TopBar({
  state,
  language,
  onManageConnections,
  onOpenScopes,
  onDisconnect,
}: Props) {
  const t = messages[language];
  const ct = connectionMessages[language];
  const [open, setOpen] = useState<"connection" | "identity" | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
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
      if (event.key === "Escape") setOpen(null);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);
  const active = state?.active;
  const report = active?.report;
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
  return (
    <div className="topbar" ref={wrap}>
      <div className="menu-wrap">
        <button
          className="menu-trigger"
          aria-expanded={open === "connection"}
          aria-haspopup="menu"
          onClick={() => setOpen(open === "connection" ? null : "connection")}
        >
          <span className="label">{activeProfile?.name ?? t.notConnected}</span>
          <span aria-hidden="true">▾</span>
        </button>
        {open === "connection" && (
          <div className="menu" role="menu">
            {state?.profiles.map((p) => (
              <button
                key={p.id}
                className="menu-item"
                role="menuitem"
                onClick={() => {
                  setOpen(null);
                  onManageConnections();
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
              onClick={() => {
                setOpen(null);
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
                  onClick={() => {
                    setOpen(null);
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
      <span className={active ? "status-pill ok" : "status-pill"} role="status">
        <span aria-hidden="true" className={active ? "dot ok" : "dot"} />
        {active ? t.connected : t.notConnected}
      </span>
      <span aria-hidden="true" className="divider" />
      <div className="topbar-group">
        <span className="topbar-label">{t.scopeLabel}</span>
        <button className="menu-trigger" onClick={onOpenScopes}>
          <span className="label">
            {active?.scope?.title ?? t.noScopeSelected}
          </span>
          <span aria-hidden="true">▾</span>
        </button>
        <button onClick={onOpenScopes}>{t.exactScope}</button>
      </div>
      <span className="topbar-spacer" />
      <div className="menu-wrap">
        <button
          className="menu-trigger"
          aria-expanded={open === "identity"}
          aria-haspopup="menu"
          onClick={() => setOpen(open === "identity" ? null : "identity")}
        >
          <span className="label">{identityText}</span>
          <span aria-hidden="true">▾</span>
        </button>
        {open === "identity" && (
          <div className="menu right" role="menu">
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
              </p>
            )}
          </div>
        )}
      </div>
      <span aria-hidden="true" className="avatar">
        {identityText.charAt(0).toUpperCase()}
      </span>
    </div>
  );
}
