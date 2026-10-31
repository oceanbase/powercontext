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

import { useEffect, useId, useRef, useState } from "react";
import type { DesktopState, ScopeDescriptor } from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import {
  connectionMessages,
  operationRestriction,
} from "./connection-messages";
import { operationSupported } from "./context";
import { messages, type Language } from "./messages";
import { PageFeedback, ErrorFeedback } from "./PageFeedback";
export function Scopes({
  state,
  language,
  onState,
  confirmSwitch,
  onSelected,
  hideHeading = false,
  onManageConnections,
  onPendingChange,
  onSelectionError,
}: {
  state: DesktopState | null;
  language: Language;
  onState: (state: DesktopState) => boolean | void;
  confirmSwitch: () => boolean | Promise<boolean>;
  onSelected?: () => void;
  hideHeading?: boolean;
  onManageConnections?: () => void;
  onPendingChange?: (pending: boolean) => void;
  onSelectionError?: (error: unknown) => void;
}) {
  const t = connectionMessages[language];
  const listId = useId();
  const common = messages[language];
  const manage = onManageConnections && (
    <button onClick={onManageConnections}>{common.chooseConnection}</button>
  );
  const active = state?.active;
  const listSupported = operationSupported(active?.report, "list_scopes");
  const selectionSupported = operationSupported(active?.report, "get_scope");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ScopeDescriptor[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [operation, setOperation] = useState<"list" | "select">("list");
  const lifetime = useRef(0);
  const sequence = useRef(0);
  useEffect(
    () => () => {
      lifetime.current++;
      sequence.current++;
    },
    [],
  );
  const pendingCancellation = useRef(Promise.resolve());
  const currentGeneration = useRef(active?.generation);
  currentGeneration.current = active?.generation;
  useEffect(() => {
    sequence.current++;
    setItems(null);
    setCursor(null);
    setError(null);
    setQuery("");
    setBusy(false);
  }, [active?.generation]);
  async function load(next: string | null) {
    if (!active || !listSupported) return;
    setOperation("list");
    const generation = active.generation;
    const ticket = ++sequence.current;
    setBusy(true);
    setError(null);
    setItems(null);
    setCursor(null);
    try {
      await pendingCancellation.current;
      if (
        ticket !== sequence.current ||
        generation !== currentGeneration.current
      )
        return;
      const result = await desktopApi.scopes(generation, query, next);
      if (
        generation === currentGeneration.current &&
        ticket === sequence.current
      ) {
        setItems(result.items);
        setCursor(result.next_cursor ?? null);
      }
    } catch (e) {
      if (ticket === sequence.current) {
        setError(e);
        try {
          const next = await desktopApi.state();
          if (ticket === sequence.current) onState(next);
        } catch {
          /* Keep the operation error if state refresh is unavailable. */
        }
      }
    } finally {
      if (ticket === sequence.current) setBusy(false);
    }
  }
  async function select(id: string) {
    if (!active || !selectionSupported) return;
    const ticket = lifetime.current;
    const generation = active.generation;
    if (
      !(await confirmSwitch()) ||
      ticket !== lifetime.current ||
      generation !== currentGeneration.current
    )
      return;
    setOperation("select");
    setBusy(true);
    setError(null);
    onPendingChange?.(true);
    try {
      const next = await desktopApi.selectScope(active.generation, id);
      const accepted = onState(next);
      if (
        ticket === lifetime.current &&
        accepted !== false &&
        generation === currentGeneration.current &&
        next.active?.connectionId === active.connectionId &&
        next.active.scope?.scope_id === id
      )
        onSelected?.();
      else if (
        ticket === lifetime.current &&
        generation === currentGeneration.current
      )
        setError({ code: "stale_context" });
    } catch (e) {
      if (ticket === lifetime.current) setError(e);
      else onSelectionError?.(e);
      try {
        onState(await desktopApi.state());
      } catch {
        /* Preserve the operation error. */
      }
    } finally {
      onPendingChange?.(false);
      if (ticket === lifetime.current) setBusy(false);
    }
  }
  return (
    <section className="card stack scope-picker">
      {!hideHeading && <h2>{t.scope}</h2>}
      <p className="small">{t.scopeHint}</p>
      {!active ? (
        <PageFeedback
          compact
          kind="unavailable"
          title={t.noConnection}
          action={manage}
        />
      ) : (
        <>
          {active.scope && (
            <p>
              {t.currentScope}: <strong>{active.scope.title}</strong>{" "}
              <code>{active.scope.scope_id}</code>
            </p>
          )}
          {!listSupported && (
            <PageFeedback
              compact
              kind="unavailable"
              title={t.scopeListUnavailable}
              action={manage}
            >
              <p>
                {operationRestriction(active.report, "list_scopes", language)}
              </p>
            </PageFeedback>
          )}
          {!selectionSupported && (
            <PageFeedback
              compact
              kind="unavailable"
              title={t.scopeSelectUnavailable}
              action={manage}
            >
              <p>
                {operationRestriction(active.report, "get_scope", language)}
              </p>
            </PageFeedback>
          )}
          {error != null && (
            <ErrorFeedback
              error={error}
              language={language}
              action={
                operation === "list" && listSupported ? (
                  <button disabled={busy} onClick={() => void load(null)}>
                    {common.restartScopeSearch}
                  </button>
                ) : undefined
              }
            />
          )}
          {busy && (
            <PageFeedback
              compact
              kind="loading"
              title={
                operation === "list"
                  ? common.loadingScopes
                  : common.selectingScope
              }
            />
          )}
          {!busy && error == null && items === null && listSupported && (
            <PageFeedback compact kind="empty" title={common.scopeIdle}>
              <p>{common.scopeIdleHint}</p>
            </PageFeedback>
          )}
          <fieldset disabled={busy && operation === "select"}>
            <label>
              {t.scopeQuery}
              <input
                disabled={!listSupported}
                value={query}
                maxLength={256}
                onChange={(e) => {
                  setQuery(e.target.value);
                  sequence.current++;
                  setBusy(false);
                  setError(null);
                  const generation = active.generation;
                  pendingCancellation.current = pendingCancellation.current
                    .then(() => desktopApi.cancelScopes(generation))
                    .catch(() => {
                      /* Context may already have changed. */
                    });
                  setItems(null);
                  setCursor(null);
                }}
              />
            </label>
            <div className="actions">
              <button
                disabled={busy || !listSupported}
                onClick={() => void load(null)}
              >
                {t.find}
              </button>
            </div>
            {items && (
              <ul className="scope-list">
                {items.map((scope, index) => (
                  <li key={scope.scope_id}>
                    <button
                      aria-describedby={`${listId}-${index}`}
                      disabled={busy || !selectionSupported}
                      onClick={() => void select(scope.scope_id)}
                    >
                      {scope.title}
                    </button>
                    <code id={`${listId}-${index}`}>{scope.scope_id}</code>
                  </li>
                ))}
              </ul>
            )}
            {items?.length === 0 && (
              <PageFeedback
                compact
                kind="empty"
                title={t.empty}
                action={manage}
              >
                <p>{t.emptyScopeHint}</p>
              </PageFeedback>
            )}
            {cursor && (
              <button
                disabled={busy || !listSupported}
                onClick={() => void load(cursor)}
              >
                {t.more}
              </button>
            )}
          </fieldset>
        </>
      )}
    </section>
  );
}
