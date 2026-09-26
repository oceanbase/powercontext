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
import type { DesktopState, ScopeDescriptor } from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import { connectionMessages, connectionError } from "./connection-messages";
import type { Language } from "./messages";
export function Scopes({
  state,
  language,
  onState,
  confirmSwitch,
  hideHeading = false,
}: {
  state: DesktopState | null;
  language: Language;
  onState: (state: DesktopState) => void;
  confirmSwitch: () => boolean;
  hideHeading?: boolean;
}) {
  const t = connectionMessages[language];
  const active = state?.active;
  const [query, setQuery] = useState("");
  const [exact, setExact] = useState("");
  const [items, setItems] = useState<ScopeDescriptor[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const pendingCancellation = useRef(Promise.resolve());
  const currentGeneration = useRef(active?.generation);
  currentGeneration.current = active?.generation;
  useEffect(() => {
    sequence.current++;
    setItems(null);
    setCursor(null);
    setError(null);
    setQuery("");
    setExact("");
    setBusy(false);
  }, [active?.generation]);
  async function load(next: string | null, suggestion = false) {
    if (!active) return;
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
      const result = suggestion
        ? {
            items: [await desktopApi.defaultScope(generation)],
            next_cursor: null,
          }
        : await desktopApi.scopes(generation, query, next);
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
    if (!active || !confirmSwitch()) return;
    setBusy(true);
    setError(null);
    try {
      onState(await desktopApi.selectScope(active.generation, id));
    } catch (e) {
      setError(e);
      try {
        onState(await desktopApi.state());
      } catch {
        /* Preserve the operation error. */
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card stack scope-picker">
      {!hideHeading && <h2>{t.scope}</h2>}
      <p className="small">{t.scopeHint}</p>
      {!active ? (
        <p>{t.noConnection}</p>
      ) : (
        <>
          {active.scope && (
            <p>
              {t.currentScope}: <strong>{active.scope.title}</strong>{" "}
              <code>{active.scope.scope_id}</code>
            </p>
          )}
          {!active.report.compatibilityVerified && (
            <p role="status">{t.noCompatibility}</p>
          )}
          {error != null && (
            <p role="alert">{connectionError(error, language)}</p>
          )}
          <fieldset disabled={!active.report.compatibilityVerified}>
            <label>
              {t.scopeQuery}
              <input
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
              <button disabled={busy} onClick={() => void load(null)}>
                {t.find}
              </button>
              <button disabled={busy} onClick={() => void load(null, true)}>
                {t.defaultScope}
              </button>
            </div>
            {items && (
              <ul className="scope-list">
                {items.map((scope) => (
                  <li key={scope.scope_id}>
                    <button
                      disabled={busy}
                      onClick={() => void select(scope.scope_id)}
                    >
                      {scope.title}
                    </button>
                    <code>{scope.scope_id}</code>
                  </li>
                ))}
              </ul>
            )}
            {items?.length === 0 && <p>{t.empty}</p>}
            {cursor && (
              <button disabled={busy} onClick={() => void load(cursor)}>
                {t.more}
              </button>
            )}
            <details>
              <summary>{t.exact}</summary>
              <label>
                {t.exact}
                <input
                  value={exact}
                  maxLength={256}
                  onChange={(e) => setExact(e.target.value)}
                />
              </label>
              <button
                disabled={busy || !exact.trim()}
                onClick={() => void select(exact)}
              >
                {t.choose}
              </button>
            </details>
          </fieldset>
        </>
      )}
    </section>
  );
}
