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
import type {
  DesktopState,
  MemoryCitation,
  MemoryEntry,
  SearchMemoryHit,
  WriteOutcome,
} from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import { connectionError } from "./connection-messages";
import { memoryMessages } from "./memory-messages";
import type { Language } from "./messages";
import memoryIcon from "../assets/memory.svg";
type Props = {
  state: DesktopState | null;
  language: Language;
  onState: (state: DesktopState) => void;
  onDirty: (dirty: boolean) => void;
};
export const textBytes = (value: string) =>
  new TextEncoder().encode(value).length;
export function NoteForm({ state, language, onState, onDirty }: Props) {
  const t = memoryMessages[language];
  const active = state?.active;
  const profile = state?.profiles.find((p) => p.id === active?.connectionId);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [outcome, setOutcome] = useState<WriteOutcome | null>(null);
  const submitting = useRef(false);
  const composing = useRef(false);
  const generation = useRef(active?.generation);
  generation.current = active?.generation;
  const enabled = !!active?.scope && active.report.compatibilityVerified;
  const bytes = textBytes(text);
  useEffect(() => {
    setText("");
    setError(null);
    setOutcome(null);
    onDirty(false);
  }, [active?.generation]);
  async function save() {
    if (!active?.scope || submitting.current || composing.current) return;
    if (!text.trim() || bytes > 8192) {
      setError({ code: "invalid_input" });
      return;
    }
    if (
      (outcome?.record.status === "unknown" ||
        state?.lastWrite?.status === "unknown") &&
      !window.confirm(t.retry)
    )
      return;
    const original = active.generation;
    submitting.current = true;
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      const result = await desktopApi.remember(original, text);
      if (generation.current === original) {
        setOutcome(result);
        if (result.record.status === "succeeded") {
          setText("");
          onDirty(false);
        }
      }
    } catch (e) {
      if (generation.current === original) setError(e);
    } finally {
      submitting.current = false;
      setBusy(false);
      try {
        onState(await desktopApi.state());
      } catch {
        /* Keep the submission result. */
      }
    }
  }
  return (
    <section className="card stack">
      <h2>{t.saveOne}</h2>
      <div className="save-row">
        <label>
          {t.note}
          <textarea
            rows={2}
            disabled={!enabled || busy}
            value={text}
            onCompositionStart={() => {
              composing.current = true;
            }}
            onCompositionEnd={() => {
              composing.current = false;
            }}
            onChange={(e) => {
              setText(e.target.value);
              onDirty(e.target.value.length > 0);
            }}
          />
        </label>
        <div className="save-action">
          <button
            className="primary"
            disabled={!enabled || busy || !text.trim() || bytes > 8192}
            onClick={() => void save()}
          >
            {busy ? t.saving : t.save}
          </button>
          <p className="hint">{t.writeOnClick}</p>
        </div>
      </div>
      <p className="small">
        {enabled ? (
          <>
            {t.saveTarget}
            <strong>
              {profile?.name} / {active?.scope?.title}
            </strong>{" "}
            <code>{active?.scope?.scope_id}</code>
          </>
        ) : (
          t.noScope
        )}
      </p>
      <p className="small">
        {bytes} / 8192 {t.budget}
      </p>
      {bytes > 8192 && <p role="alert">{t.bytesError}</p>}
      {error != null && (
        <p role="alert">
          {t.failed} {connectionError(error, language)}
        </p>
      )}
      {outcome && (
        <p role={outcome.record.status === "succeeded" ? "status" : "alert"}>
          {outcome.record.status === "succeeded"
            ? outcome.result?.entry
              ? t.success
              : t.noEntry
            : outcome.record.status === "unknown"
              ? t.unknown
              : `${t.failed} ${connectionError(outcome.record.error, language)}`}
        </p>
      )}
    </section>
  );
}
export function MemoryWorkspace(props: Props) {
  const { state, language, onState, onDirty } = props;
  const t = memoryMessages[language];
  const active = state?.active;
  const readerHeading = useRef<HTMLHeadingElement>(null);
  const readButton = useRef<HTMLButtonElement | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchMemoryHit[] | null>(null);
  const [entry, setEntry] = useState<MemoryEntry | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<"search" | "detail" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState("");
  const sequence = useRef(0);
  const pendingCancel = useRef(Promise.resolve());
  const current = useRef(active?.generation);
  current.current = active?.generation;
  const enabled = !!active?.scope && active.report.compatibilityVerified;
  function clearRead() {
    sequence.current++;
    setHits(null);
    setEntry(null);
    setSelected(null);
    setError(null);
    setCopied("");
    setBusy(null);
  }
  function cancel() {
    clearRead();
    if (active) {
      const generation = active.generation;
      pendingCancel.current = pendingCancel.current
        .then(() => desktopApi.cancelMemory(generation))
        .catch(() => {});
    }
  }
  useEffect(() => {
    clearRead();
    setQuery("");
    return () => {
      sequence.current++;
      if (active)
        void desktopApi.cancelMemory(active.generation).catch(() => {});
    };
  }, [active?.generation]);
  async function read(citation?: MemoryCitation) {
    if (!active?.scope) return;
    const generation = active.generation;
    const ticket = ++sequence.current;
    setError(null);
    setEntry(null);
    setCopied("");
    setBusy(citation ? "detail" : "search");
    if (citation) setSelected(JSON.stringify(citation));
    else setHits(null);
    try {
      await pendingCancel.current;
      if (ticket !== sequence.current || generation !== current.current) return;
      if (citation) {
        const result = await desktopApi.entry(generation, citation);
        if (ticket === sequence.current && generation === current.current)
          setEntry(result);
      } else {
        const result = await desktopApi.search(generation, query);
        if (ticket === sequence.current && generation === current.current) {
          setHits(result.hits);
          setSelected(null);
        }
      }
    } catch (e) {
      if (ticket === sequence.current) {
        setError(e);
        setHits(null);
        setEntry(null);
        setSelected(null);
        try {
          onState(await desktopApi.state());
        } catch {
          /* Keep safe operation error. */
        }
      }
    } finally {
      if (ticket === sequence.current) setBusy(null);
    }
  }
  useEffect(() => {
    if (entry) readerHeading.current?.focus();
  }, [entry]);
  async function copy(reference: boolean) {
    if (!entry) return;
    try {
      await navigator.clipboard.writeText(
        reference ? JSON.stringify(entry.citation, null, 2) : entry.text,
      );
      setCopied(t.copied);
    } catch {
      setCopied(t.copyFailed);
    }
  }
  const record = state?.lastWrite;
  return (
    <div className="stack memory-workspace">
      {record &&
        (record.status === "unknown" ||
          record.status === "pending" ||
          record.context.generation !== active?.generation) && (
          <section className="card" role="status">
            <p>
              {t.original}:{" "}
              <code>
                {record.context.endpoint} · {record.context.scopeId}
              </code>
            </p>
            <p>
              {record.status === "unknown"
                ? t.unknown
                : record.status === "pending"
                  ? t.pending
                  : t.changed}
            </p>
          </section>
        )}
      <NoteForm
        state={state}
        language={language}
        onState={onState}
        onDirty={onDirty}
      />
      <section className="card stack">
        <div className="search-row">
          <label>
            {t.query}
            <span className="search-input">
              <input
                disabled={!enabled}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  cancel();
                }}
              />
              {query && (
                <button
                  className="clear-query"
                  aria-label={t.clearQuery}
                  disabled={!enabled}
                  onClick={() => {
                    setQuery("");
                    cancel();
                  }}
                >
                  ✕
                </button>
              )}
            </span>
          </label>
          <button
            disabled={
              !enabled || !!busy || !query.trim() || textBytes(query) > 8192
            }
            onClick={() => void read()}
          >
            {busy === "search" ? t.searching : t.search}
          </button>
        </div>
        <div className="search-meta">
          <span>
            {t.searchModeLabel}
            <select disabled value="fts" aria-label={t.searchModeLabel}>
              <option value="fts">{t.ftsLabel}</option>
            </select>
          </span>
          <span>
            {t.limitLabel}
            <select disabled value="10" aria-label={t.limitLabel}>
              <option value="10">10</option>
            </select>
          </span>
          <span aria-hidden="true" className="divider" />
          <span className="note-line">{t.searchConfigNote}</span>
        </div>
        {!enabled && <p>{t.noScope}</p>}
        {textBytes(query) > 8192 && <p role="alert">{t.inputError}</p>}
        {error != null && (
          <p role="alert">{connectionError(error, language)}</p>
        )}
      </section>
      <div className="memory-grid">
        <section className="card stack" aria-label={t.results}>
          <div className="card-heading">
            <h2>{t.results}</h2>
          </div>
          {hits === null && !busy && error == null && <p>{t.start}</p>}
          {hits?.length === 0 && <p>{t.none}</p>}
          {hits?.length === 10 && <p>{t.limit}</p>}
          {hits && (
            <p className="small">
              {t.returned(hits.length)}
              {active?.scope?.title ? ` · ${active.scope.title}` : ""}
            </p>
          )}
          {hits && hits.length > 0 && (
            <ul className="memory-hits">
              {hits.map((hit) => {
                const key = JSON.stringify(hit.citation);
                const title = hit.text.split("\n", 1)[0] || hit.text;
                return (
                  <li key={key}>
                    <div className="hit-card">
                      <img src={memoryIcon} alt="" />
                      <div className="hit-main">
                        <div className="hit-title">{title}</div>
                        <div className="hit-snippet">{hit.text}</div>
                      </div>
                      {hit.matched_by.includes("fts") && (
                        <span className="badge success">{t.hitFts}</span>
                      )}
                      {!hit.matched_by.includes("fts") &&
                        hit.matched_by.includes("vector") && (
                          <span className="badge">{t.hitVector}</span>
                        )}
                      <button
                        disabled={!!busy}
                        aria-pressed={selected === key}
                        onClick={(event) => {
                          readButton.current = event.currentTarget;
                          void read(hit.citation);
                        }}
                      >
                        {busy === "detail" && selected === key
                          ? t.loading
                          : t.read}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="note-line">{t.browseNote}</p>
        </section>
        {entry && (
          <section className="card stack reader" aria-label={t.detail}>
            <div className="card-heading">
              <h2 ref={readerHeading} tabIndex={-1}>
                {t.detail}
              </h2>
              <span>
                <span className="badge">{entry.kind}</span>{" "}
                <span
                  className={
                    entry.state === "active" ? "badge success" : "badge subtle"
                  }
                >
                  {entry.state === "active" ? t.badgeActive : t.badgeInactive}
                </span>
              </span>
            </div>
            <div className="plain-text reader-body" tabIndex={0}>
              {entry.text}
            </div>
            {active?.scope && (
              <p className="small">
                {t.scopeBelong} <strong>{active.scope.title}</strong>
              </p>
            )}
            <div className="citation-block">
              <h3>{t.reference}</h3>
              <dl className="citation-summary">
                <dt>Memory revision</dt>
                <dd>{entry.citation.memory_ref.revision}</dd>
                <dt>Entry ID</dt>
                <dd>{entry.citation.entry_id}</dd>
                <dt>Entry version</dt>
                <dd>{entry.citation.entry_version_id}</dd>
              </dl>
              <p className="small">{t.citationNote}</p>
              <button
                className="primary-outline"
                onClick={() => void copy(true)}
              >
                {t.copyReference}
              </button>
            </div>
            <div className="actions">
              <button onClick={() => void copy(false)}>{t.copyText}</button>
              <button
                onClick={() => {
                  setEntry(null);
                  setCopied("");
                  requestAnimationFrame(() => readButton.current?.focus());
                }}
              >
                {t.close}
              </button>
            </div>
            {copied && <p role="status">{copied}</p>}
            <details>
              <summary>{t.referenceFull}</summary>
              <pre className="plain-text">
                {JSON.stringify(entry.citation, null, 2)}
              </pre>
            </details>
            <details>
              <summary>{t.sources}</summary>
              {entry.source_refs.length ? (
                <ul>
                  {entry.source_refs.map((source) => (
                    <li key={source.source_id}>
                      {source.name} <code>{source.source_id}</code>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>{t.noSources}</p>
              )}
            </details>
            <p className="note-line">{t.agentScopeNote}</p>
          </section>
        )}
      </div>
    </div>
  );
}
