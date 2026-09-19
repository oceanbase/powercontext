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
type Props = {
  state: DesktopState | null;
  language: Language;
  home: boolean;
  onState: (state: DesktopState) => void;
  onDirty: (dirty: boolean) => void;
};
export const textBytes = (value: string) =>
  new TextEncoder().encode(value).length;
export function NoteForm({
  state,
  language,
  onState,
  onDirty,
}: Omit<Props, "home">) {
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
      <h2>{t.quick}</h2>
      <p>{t.hint}</p>
      <p>
        {enabled ? (
          <>
            {t.target}: <strong>{profile?.name}</strong> ·{" "}
            {active?.scope?.title} <code>{active?.scope?.scope_id}</code>
          </>
        ) : (
          t.noScope
        )}
      </p>
      <label>
        {t.note}
        <textarea
          rows={6}
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
      <p className="small">
        {bytes} / 8192 {t.budget}
      </p>
      {bytes > 8192 && <p role="alert">{t.bytesError}</p>}
      <button
        className="primary"
        disabled={!enabled || busy || !text.trim() || bytes > 8192}
        onClick={() => void save()}
      >
        {busy ? t.saving : t.save}
      </button>
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
  const { state, language, home, onState, onDirty } = props;
  const t = memoryMessages[language];
  const active = state?.active;
  const [adding, setAdding] = useState(false);
  const readerHeading = useRef<HTMLHeadingElement>(null);
  const readButton = useRef<HTMLButtonElement | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchMemoryHit[] | null>(null);
  const [entry, setEntry] = useState<MemoryEntry | null>(null);
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
    if (!citation) setHits(null);
    try {
      await pendingCancel.current;
      if (ticket !== sequence.current || generation !== current.current) return;
      if (citation) {
        const result = await desktopApi.entry(generation, citation);
        if (ticket === sequence.current && generation === current.current)
          setEntry(result);
      } else {
        const result = await desktopApi.search(generation, query);
        if (ticket === sequence.current && generation === current.current)
          setHits(result.hits);
      }
    } catch (e) {
      if (ticket === sequence.current) {
        setError(e);
        setHits(null);
        setEntry(null);
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
      {!home && !adding && (
        <button onClick={() => setAdding(true)}>{t.add}</button>
      )}
      {(home || adding) && (
        <NoteForm
          state={state}
          language={language}
          onState={onState}
          onDirty={onDirty}
        />
      )}
      <section className="card stack">
        <h2>{t.search}</h2>
        <p>{t.searchHint}</p>
        {!enabled && <p>{t.noScope}</p>}
        <label>
          {t.query}
          <input
            disabled={!enabled}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              cancel();
            }}
          />
        </label>
        <button
          disabled={
            !enabled || !!busy || !query.trim() || textBytes(query) > 8192
          }
          onClick={() => void read()}
        >
          {busy === "search" ? t.searching : t.search}
        </button>
        {textBytes(query) > 8192 && <p role="alert">{t.inputError}</p>}
        {error != null && (
          <p role="alert">{connectionError(error, language)}</p>
        )}
        {hits === null && !busy && error == null && <p>{t.start}</p>}
        {hits?.length === 0 && <p>{t.none}</p>}
        {hits?.length === 10 && <p>{t.limit}</p>}
        {hits && (
          <ul className="memory-hits">
            {hits.map((hit) => (
              <li key={JSON.stringify(hit.citation)}>
                <p className="plain-text">{hit.text}</p>
                <button
                  disabled={!!busy}
                  onClick={(event) => {
                    readButton.current = event.currentTarget;
                    void read(hit.citation);
                  }}
                >
                  {t.read}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {busy === "detail" && <p role="status">{t.loading}</p>}
      {entry && (
        <section className="card stack reader" aria-label={t.detail}>
          <h2 ref={readerHeading} tabIndex={-1}>
            {t.detail}
          </h2>
          <div className="plain-text" tabIndex={0}>
            {entry.text}
          </div>
          <div className="actions">
            <button onClick={() => void copy(false)}>{t.copyText}</button>
            <button onClick={() => void copy(true)}>{t.copyReference}</button>
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
            <summary>{t.reference}</summary>
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
        </section>
      )}
    </div>
  );
}
