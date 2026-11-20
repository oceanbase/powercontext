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
import type {
  DesktopState,
  ScopeDescriptor,
  ScopePage,
} from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import { connectionError } from "./connection-messages";
import { activeCheckReport, connectionCheckReport } from "./context";
import { messages, type Language } from "./messages";
import { previewScopes } from "./preview-data";

type Props = {
  state: DesktopState | null;
  language: Language;
  loading?: boolean;
  contextPending?: boolean;
  preview?: boolean;
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
  preview = false,
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
  const canListSpaces =
    !!active?.report.supportedOperations.includes("list_scopes");
  const hasScopeMenu = hasService && (!!active?.scope || canListSpaces);
  const scopeTitle =
    active?.scope?.title ??
    (language === "zh" ? "选择记忆空间" : "Select a memory space");
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
          {hasScopeMenu && active && (
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
                    aria-label={
                      active.scope
                        ? `${language === "zh" ? "当前记忆空间" : "Current memory space"}: ${scopeTitle}`
                        : scopeTitle
                    }
                  >
                    <span className="context-switcher-label">{scopeTitle}</span>
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
                    key={active.generation}
                    active={active.scope}
                    generation={active.generation}
                    list={canListSpaces && open === "scope"}
                    preview={preview}
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
  generation,
  list,
  preview,
  language,
  onSelect,
  onCreate,
}: {
  active: ScopeDescriptor | null;
  generation: number;
  list: boolean;
  preview: boolean;
  language: Language;
  onSelect: (scope: ScopeDescriptor) => void;
  onCreate: () => void;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [page, setPage] = useState<ScopePage>();
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!list || preview) return;
    let current = true;
    setError(null);
    void desktopApi.scopes(generation, "", cursor).then(
      (next) => {
        if (current)
          setPage((previous) =>
            cursor && previous
              ? { ...next, items: [...previous.items, ...next.items] }
              : next,
          );
      },
      (reason: unknown) => {
        if (current) setError(reason);
      },
    );
    return () => {
      current = false;
    };
  }, [cursor, generation, list, preview]);

  const scopes = preview ? previewScopes(language) : (page?.items ?? []);
  const choices =
    active && !scopes.some((scope) => scope.scope_id === active.scope_id)
      ? [active, ...scopes]
      : scopes;

  return (
    <>
      <DropdownMenuRadioGroup
        value={active?.scope_id ?? ""}
        onValueChange={(value) => {
          const scope = choices.find(
            (candidate) => candidate.scope_id === value,
          );
          if (scope) onSelect(scope);
        }}
      >
        {choices.map((scope) => (
          <DropdownMenuRadioItem
            key={scope.scope_id}
            value={scope.scope_id}
            className="menu-item"
          >
            {scope.title}
          </DropdownMenuRadioItem>
        ))}
      </DropdownMenuRadioGroup>
      {error != null && (
        <DropdownMenuItem disabled>
          {connectionError(error, language)}
        </DropdownMenuItem>
      )}
      {!preview && list && !page && error == null && (
        <DropdownMenuItem disabled>
          {messages[language].loadingScopes}
        </DropdownMenuItem>
      )}
      {page?.next_cursor && (
        <DropdownMenuItem
          onSelect={(event) => {
            event.preventDefault();
            setCursor(page.next_cursor ?? null);
          }}
        >
          {language === "zh" ? "加载更多空间" : "Load more spaces"}
        </DropdownMenuItem>
      )}
      {preview && (
        <>
          <DropdownMenuSeparator className="menu-separator" />
          <DropdownMenuItem
            className="menu-item context-menu-create"
            onSelect={onCreate}
          >
            <Plus aria-hidden="true" />
            {language === "zh" ? "创建记忆空间" : "Create memory space"}
          </DropdownMenuItem>
        </>
      )}
    </>
  );
}
