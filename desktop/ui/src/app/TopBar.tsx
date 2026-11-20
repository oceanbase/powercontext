/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Check, ChevronsUpDown, Plus } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { DesktopState, ScopeDescriptor } from "../generated/ipc";
import { activeCheckReport, connectionCheckReport } from "./context";
import { messages, type Language } from "./messages";
import { previewScopes } from "./preview-data";

type Props = {
  state: DesktopState | null;
  language: Language;
  loading?: boolean;
  contextPending?: boolean;
  onManageConnections: (profileId?: string) => void;
  onSelectConnection?: (profileId: string) => void;
  onSelectScope?: (scope: ScopeDescriptor) => void;
  onCreateScope?: (trigger: HTMLElement) => void;
  onOpenScopes?: (trigger: HTMLElement) => void;
  /** Kept optional for callers while the disconnect action is retired. */
  onDisconnect?: () => void;
  hideContent?: boolean;
};

export function TopBar({
  state,
  language,
  loading = false,
  contextPending = false,
  onManageConnections,
  onSelectConnection,
  onSelectScope,
  onCreateScope,
  hideContent = false,
}: Props) {
  const t = messages[language];
  const [open, setOpen] = useState<"connection" | "scope" | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const scopeTrigger = useRef<HTMLButtonElement>(null);
  const active = state?.active;
  const activeProfile = state?.profiles.find(
    (profile) => profile.id === active?.connectionId,
  );
  const hasService = !!activeProfile;
  const hasScope = hasService && !!active?.scope;
  const activeReport = activeCheckReport(state);
  const serviceUnavailable =
    !!activeReport &&
    !!(
      activeReport.liveness.error ||
      activeReport.readiness.error ||
      (activeReport.readiness.value &&
        activeReport.readiness.value.status !== "ready")
    );
  const serviceStatus = loading
    ? "checking"
    : serviceUnavailable
      ? "unavailable"
      : activeReport?.readiness.value?.status === "ready"
        ? "ready"
        : "unknown";
  const serviceStatusLabel =
    language === "zh"
      ? {
          checking: "正在检查",
          ready: "服务正常",
          unavailable: "服务异常",
          unknown: "状态未知",
        }[serviceStatus]
      : {
          checking: "Checking",
          ready: "Service ready",
          unavailable: "Service unavailable",
          unknown: "Status unknown",
        }[serviceStatus];

  useEffect(() => {
    if (hideContent) setOpen(null);
  }, [hideContent]);

  useEffect(() => {
    const element = wrap.current;
    if (!element) return;
    const update = () =>
      document.documentElement.style.setProperty(
        "--topbar-height",
        `${element.getBoundingClientRect().height}px`,
      );
    update();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(element);
    return () => {
      observer?.disconnect();
      document.documentElement.style.removeProperty("--topbar-height");
    };
  }, []);

  return (
    <div className="topbar" ref={wrap}>
      {hasService && !hideContent && (
        <div className="context-switchers" aria-label={t.globalContext}>
          <DropdownMenu
            open={open === "connection"}
            onOpenChange={(nextOpen) => setOpen(nextOpen ? "connection" : null)}
          >
            <DropdownMenuTrigger asChild>
              <Button
                variant="context"
                size="sm"
                className="context-switcher service-switcher"
                aria-label={`${language === "zh" ? "当前服务" : "Current service"}: ${activeProfile.name}, ${serviceStatusLabel}`}
              >
                <span className="context-switcher-label">
                  {activeProfile.name}
                </span>
                <span
                  className={`service-status-dot${serviceStatus === "ready" ? " service-status-dot-success" : serviceStatus === "unavailable" ? " service-status-dot-danger" : ""}`}
                  aria-hidden="true"
                />
                <ChevronsUpDown
                  className="context-swap-icon"
                  aria-hidden="true"
                />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              aria-label={t.manageConnections}
              className="context-menu min-w-[260px] max-w-[360px]"
            >
              <ConnectionMenu
                state={state}
                activeId={active?.connectionId}
                label={t.manageConnections}
                activeLabel={language === "zh" ? "当前" : "Active"}
                language={language}
                onSelectConnection={(profileId) => {
                  if (onSelectConnection) onSelectConnection(profileId);
                  else onManageConnections(profileId);
                }}
                onManageConnections={onManageConnections}
              />
            </DropdownMenuContent>
          </DropdownMenu>
          {hasScope && (
            <>
              <span className="context-separator" aria-hidden="true">
                /
              </span>
              <DropdownMenu
                open={open === "scope"}
                onOpenChange={(nextOpen) => setOpen(nextOpen ? "scope" : null)}
              >
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="context"
                    size="sm"
                    ref={scopeTrigger}
                    className="context-switcher scope-switcher"
                    disabled={contextPending}
                    aria-label={`${language === "zh" ? "当前记忆空间" : "Current memory space"}: ${active?.scope?.title}`}
                  >
                    <span className="context-switcher-label">
                      {active?.scope?.title}
                    </span>
                    <ChevronsUpDown
                      className="context-swap-icon"
                      aria-hidden="true"
                    />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  className="context-menu min-w-[260px] max-w-[360px]"
                >
                  <ScopeMenu
                    active={active!.scope!}
                    language={language}
                    onSelect={(scope) => onSelectScope?.(scope)}
                    onCreate={() => {
                      const trigger = scopeTrigger.current;
                      if (trigger) onCreateScope?.(trigger);
                    }}
                  />
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          )}
        </div>
      )}
      <span
        className="topbar-spacer"
        data-tauri-drag-region
        aria-hidden="true"
      />
    </div>
  );
}

function ConnectionMenu({
  state,
  activeId,
  label,
  activeLabel,
  language,
  onSelectConnection,
  onManageConnections,
}: {
  state: DesktopState | null;
  activeId?: string;
  label: string;
  activeLabel: string;
  language: Language;
  onSelectConnection: (profileId: string) => void;
  onManageConnections: (profileId?: string) => void;
}) {
  return (
    <>
      {state?.profiles.map((profile) => {
        const report = connectionCheckReport(state, profile.id);
        const unavailable = !!(
          report?.liveness.error ||
          report?.readiness.error ||
          (report?.readiness.value && report.readiness.value.status !== "ready")
        );
        const status = unavailable
          ? "unavailable"
          : report?.readiness.value?.status === "ready"
            ? "ready"
            : "unknown";
        const statusLabel =
          language === "zh"
            ? {
                ready: "服务正常",
                unavailable: "服务异常",
                unknown: "状态未知",
              }[status]
            : {
                ready: "Service ready",
                unavailable: "Service unavailable",
                unknown: "Status unknown",
              }[status];
        return (
          <DropdownMenuItem
            key={profile.id}
            className="menu-item"
            onSelect={() => onSelectConnection(profile.id)}
          >
            <span className="context-menu-option">
              <span className="context-menu-option-title">
                <span>{profile.name}</span>
                <span
                  className={`service-status-dot${status === "ready" ? " service-status-dot-success" : status === "unavailable" ? " service-status-dot-danger" : ""}`}
                  role="img"
                  aria-label={statusLabel}
                />
              </span>
              {profile.id === activeId && <Check aria-label={activeLabel} />}
            </span>
            <span className="meta">{profile.endpoint}</span>
          </DropdownMenuItem>
        );
      })}
      <DropdownMenuSeparator className="menu-separator" />
      <DropdownMenuItem
        className="menu-item"
        onSelect={() => onManageConnections()}
      >
        {label}
      </DropdownMenuItem>
    </>
  );
}

function ScopeMenu({
  active,
  language,
  onSelect,
  onCreate,
}: {
  active: ScopeDescriptor;
  language: Language;
  onSelect: (scope: ScopeDescriptor) => void;
  onCreate: () => void;
}) {
  // Only the desktop development connection exposes simulated Spaces. A real
  // service must never receive locally invented choices.
  const scopes = active.scope_id.startsWith("preview-")
    ? previewScopes(language)
    : [active];

  return (
    <>
      <DropdownMenuRadioGroup
        value={active.scope_id}
        onValueChange={(value) => {
          const scope = scopes.find(
            (candidate) => candidate.scope_id === value,
          );
          if (scope) onSelect(scope);
        }}
      >
        {scopes.map((scope) => (
          <DropdownMenuRadioItem
            key={scope.scope_id}
            value={scope.scope_id}
            className="menu-item"
          >
            {scope.title}
          </DropdownMenuRadioItem>
        ))}
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator className="menu-separator" />
      <DropdownMenuItem
        className="menu-item context-menu-create"
        onSelect={onCreate}
      >
        <Plus aria-hidden="true" />
        {language === "zh" ? "创建记忆空间" : "Create memory space"}
      </DropdownMenuItem>
    </>
  );
}
