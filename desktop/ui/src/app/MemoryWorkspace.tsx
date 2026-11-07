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
  AtomicMemoryDetail,
  ArtifactReference,
  AtomicMemoryRecord,
  MemorySearchHit,
  MemoryContext,
  WriteOutcome,
  WriteRecord,
} from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import { connectionError, operationRestriction } from "./connection-messages";
import { operationSupported } from "./context";
import { memoryMessages } from "./memory-messages";
import { messages, type Language } from "./messages";
import { PageFeedback, ErrorFeedback } from "./PageFeedback";
import { useConfirmation } from "./useConfirmation";
import memoryIcon from "../assets/memory.svg";
type Props = {
  state: DesktopState | null;
  language: Language;
  onState: (state: DesktopState) => void;
  onDirty: (dirty: boolean) => void;
  onManageConnections?: () => void;
  onOpenScopes?: (trigger: HTMLElement) => void;
  onOpenLocalServer?: () => void;
  contextPending?: boolean;
  onSubmission?: (
    text: string,
    target: SubmittedTarget,
  ) => (record: WriteRecord | null) => void;
};
type SubmittedTarget = Pick<MemoryContext, "generation" | "scopeId"> &
  Partial<Pick<MemoryContext, "endpoint" | "serverId">>;
export type SubmittedDraft = {
  id: number;
  text: string;
  target: SubmittedTarget;
  phase: "pending" | "unknown" | "unconfirmed" | "failed";
};
export const textBytes = (value: string) =>
  new TextEncoder().encode(value).length;
function WriteTarget({ context }: { context: SubmittedTarget }) {
  return (
    <code>
      {context.endpoint} · {context.serverId && <>{context.serverId} · </>}
      {context.scopeId}
    </code>
  );
}

export function SubmittedDraftRecovery({
  draft,
  language,
  onDiscard,
}: {
  draft: SubmittedDraft;
  language: Language;
  onDiscard: () => void;
}) {
  const t = memoryMessages[language];
  const { confirm, confirmation } = useConfirmation(language);
  const [feedback, setFeedback] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(draft.text);
      setFeedback(t.copied);
    } catch {
      setFeedback(t.copyFailed);
    }
  }
  return (
    <section className="card stack" role="region" aria-label={t.recovery}>
      {confirmation}
      <h2>{t.recovery}</h2>
      <p>{t[draft.phase]}</p>
      <p>{t.recoveryHint}</p>
      <p>
        {t.original}: <WriteTarget context={draft.target} />
      </p>
      <label>
        {t.submittedText}
        <textarea readOnly value={draft.text} rows={3} />
      </label>
      <div className="actions">
        <button onClick={() => void copy()}>{t.copyText}</button>
        <button
          onClick={async () => {
            if (await confirm(t.discardSubmittedConfirm)) onDiscard();
          }}
        >
          {t.discardSubmitted}
        </button>
      </div>
      {feedback && <p role="status">{feedback}</p>}
    </section>
  );
}

type NoteFormProps = Props & {
  onReadSaved: (
    artifact: ArtifactReference,
    trigger: HTMLButtonElement,
  ) => void;
  invalidateSaved: number;
};
export function NoteForm({
  state,
  language,
  onState,
  onDirty,
  contextPending = false,
  onReadSaved,
  invalidateSaved,
  onSubmission,
}: NoteFormProps) {
  const t = memoryMessages[language];
  const { confirm, confirmation } = useConfirmation(language);
  const active = state?.active;
  const profile = state?.profiles.find((p) => p.id === active?.connectionId);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [outcome, setOutcome] = useState<WriteOutcome | null>(null);
  const [saved, setSaved] = useState<AtomicMemoryRecord[]>([]);
  // The save receipt records how many records the Server actually returned;
  // it stays intact when a later failed read hides the visible bodies.
  const [returnedCount, setReturnedCount] = useState<number | null>(null);
  const submitting = useRef(false);
  const composing = useRef(false);
  const lifetime = useRef(0);
  const generation = useRef(active?.generation);
  generation.current = active?.generation;
  const contentInvalidation = useRef(invalidateSaved);
  contentInvalidation.current = invalidateSaved;
  const enabled =
    !contextPending &&
    !!active?.scope &&
    operationSupported(active.report, "remember_memory");
  const savedReadEnabled =
    !contextPending &&
    !!active?.scope &&
    operationSupported(active.report, "get_artifact_revision");
  const bytes = textBytes(text);
  useEffect(() => {
    setText("");
    setError(null);
    setOutcome(null);
    setSaved([]);
    setReturnedCount(null);
    onDirty(false);
    submitting.current = false;
    setBusy(false);
    return () => {
      lifetime.current++;
    };
  }, [active?.generation]);
  // A failed read must clear returned record bodies like any other private
  // result, while the write outcome and its original target stay visible.
  useEffect(() => {
    if (invalidateSaved > 0) setSaved([]);
  }, [invalidateSaved]);
  async function save() {
    if (!enabled || !active?.scope || submitting.current || composing.current)
      return;
    if (!text.trim() || bytes > 8192) {
      setError({ code: "invalid_input" });
      return;
    }
    const original = active.generation;
    const ticket = lifetime.current;
    if (
      (outcome?.record.status === "unknown" ||
        state?.lastWrite?.status === "unknown") &&
      !(await confirm(t.retry))
    )
      return;
    if (ticket !== lifetime.current || generation.current !== original) return;
    const contentTicket = contentInvalidation.current;
    const isCurrent = () =>
      ticket === lifetime.current && generation.current === original;
    submitting.current = true;
    setBusy(true);
    setError(null);
    setOutcome(null);
    setSaved([]);
    setReturnedCount(null);
    // Retain intent before IPC. A concurrent read can disconnect and unmount
    // this form while the native write is still awaiting its response.
    const finishSubmission = onSubmission?.(text, {
      generation: original,
      scopeId: active.scope.scope_id,
      endpoint: profile?.endpoint,
      serverId: active.report.discovery?.server?.server_id ?? null,
    });
    try {
      const result = await desktopApi.remember(original, text);
      finishSubmission?.(result.record);
      if (isCurrent()) {
        setOutcome(result);
        if (result.record.status === "succeeded") {
          // Zero, one, or multiple returned records: represent each honestly.
          const records = result.result?.records ?? [];
          setReturnedCount(records.length);
          // A read failure may have hidden private results while this write
          // was in flight. Keep its receipt without restoring invalidated bodies.
          if (contentTicket === contentInvalidation.current) setSaved(records);
          setText("");
          onDirty(false);
        }
      }
    } catch (e) {
      finishSubmission?.(null);
      if (isCurrent()) setError(e);
    } finally {
      if (isCurrent()) {
        submitting.current = false;
        setBusy(false);
      }
      // The native write still completes against its original target after unmount.
      try {
        onState(await desktopApi.state());
      } catch {
        /* Keep the submission result. */
      }
    }
  }
  return (
    <section className="card stack">
      {confirmation}
      <h2>{t.saveOne}</h2>
      <div className="save-row">
        <label>
          <span className="sr-only">{t.note}</span>
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
        <button
          className="primary"
          disabled={!enabled || busy || !text.trim() || bytes > 8192}
          onClick={() => void save()}
        >
          {busy ? t.saving : t.save}
        </button>
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
        ) : contextPending ? (
          messages[language].contextUpdating
        ) : active?.scope ? (
          operationRestriction(active.report, "remember_memory", language)
        ) : (
          t.noScope
        )}
      </p>
      {busy && <PageFeedback compact kind="loading" title={t.saving} />}
      {bytes > 8192 && (
        <PageFeedback compact kind="error" title={t.bytesError} />
      )}
      {error != null && (
        <ErrorFeedback error={error} language={language} title={t.failed} />
      )}
      {outcome?.record.status === "failed" ? (
        <ErrorFeedback
          error={outcome.record.error}
          language={language}
          title={t.failed}
        >
          <p className="small">
            {t.original}: <WriteTarget context={outcome.record.context} />
          </p>
        </ErrorFeedback>
      ) : (
        outcome && (
          <PageFeedback
            compact
            kind={
              outcome.record.status === "succeeded"
                ? "success"
                : outcome.record.status === "unknown"
                  ? "warning"
                  : "error"
            }
            title={
              outcome.record.status === "succeeded"
                ? returnedCount
                  ? t.savedReturned(returnedCount) +
                    (saved.length > 0 && savedReadEnabled
                      ? ` ${t.savedOpen}`
                      : "")
                  : t.noEntry
                : outcome.record.status === "unknown"
                  ? t.unknown
                  : `${t.failed} ${connectionError(outcome.record.error, language)}`
            }
          >
            {outcome.record.status === "unknown" && (
              <p className="small">
                {t.original}: <WriteTarget context={outcome.record.context} />
              </p>
            )}
          </PageFeedback>
        )
      )}
      {saved.length > 0 && (
        <div className="stack">
          <ul className="saved-hits">
            {saved.map((record) => {
              const key = JSON.stringify(record.artifact);
              const title = record.text.split("\n", 1)[0] || record.text;
              return (
                <li key={key}>
                  <div className="hit-card">
                    <img src={memoryIcon} alt="" />
                    <div className="hit-main">
                      <div className="hit-title">{title}</div>
                      <div className="hit-snippet">{record.text}</div>
                    </div>
                    <div className="hit-actions">
                      <button
                        disabled={!savedReadEnabled}
                        onClick={(event) =>
                          onReadSaved(record.artifact, event.currentTarget)
                        }
                      >
                        {t.openSaved}
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
          {!savedReadEnabled && (
            <p>
              {contextPending
                ? messages[language].contextUpdating
                : operationRestriction(
                    active?.report,
                    "get_artifact_revision",
                    language,
                  )}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
export function MemoryWorkspace(props: Props) {
  const { state, language, onState, onDirty, contextPending = false } = props;
  const common = messages[language];
  const failedReference = useRef<ArtifactReference | undefined>(undefined);
  const t = memoryMessages[language];
  const active = state?.active;
  const readButton = useRef<HTMLButtonElement | null>(null);
  const queryInput = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<MemorySearchHit[] | null>(null);
  const [entry, setEntry] = useState<AtomicMemoryDetail | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [savedRead, setSavedRead] = useState(false);
  const [invalidateSaved, setInvalidateSaved] = useState(0);
  const [busy, setBusy] = useState<"search" | "detail" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const sequence = useRef(0);
  const pendingCancel = useRef(Promise.resolve());
  const current = useRef(active?.generation);
  current.current = active?.generation;
  const enabled =
    !contextPending && !!active?.scope && active.report.compatibilityVerified;
  const searchEnabled =
    enabled && operationSupported(active?.report, "search_memory");
  const readEnabled =
    enabled && operationSupported(active?.report, "get_artifact_revision");
  function clearRead() {
    failedReference.current = undefined;
    sequence.current++;
    setHits(null);
    setEntry(null);
    setSelected(null);
    setSavedRead(false);
    setError(null);
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
  async function read(reference?: ArtifactReference, fromSave = false) {
    if (!(reference ? readEnabled : searchEnabled) || !active?.scope) return;
    const generation = active.generation;
    const ticket = ++sequence.current;
    failedReference.current = reference;
    setError(null);
    setEntry(null);
    setSavedRead(fromSave);
    setBusy(reference ? "detail" : "search");
    if (reference) setSelected(JSON.stringify(reference));
    else setHits(null);
    try {
      await pendingCancel.current;
      if (ticket !== sequence.current || generation !== current.current) return;
      if (reference) {
        const result = await desktopApi.atomicEntry(generation, reference);
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
        setSavedRead(false);
        // A failed read invalidates private result content everywhere,
        // including returned record bodies in the save form.
        // Move only the disappearing active trigger; a user who already
        // moved elsewhere while awaiting the read keeps their focus.
        if (fromSave && document.activeElement === readButton.current)
          queryInput.current?.focus();
        setInvalidateSaved((value) => value + 1);
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
  async function readSaved(
    artifact: ArtifactReference,
    trigger: HTMLButtonElement,
  ) {
    readButton.current = trigger;
    await read(artifact, true);
  }
  const record = state?.lastWrite;
  return (
    <div className="stack memory-workspace">
      {state && !enabled && !contextPending && (
        <PageFeedback
          kind="unavailable"
          title={
            !active
              ? common.noActiveTitle
              : !active.report.compatibilityVerified
                ? common.compatibilityNeeded
                : common.chooseScopeTitle
          }
          action={
            !active ? (
              <>
                {props.onOpenLocalServer && (
                  <button onClick={props.onOpenLocalServer}>
                    {common.setUpLocally}
                  </button>
                )}
                {props.onManageConnections && (
                  <button onClick={props.onManageConnections}>
                    {common.connectExisting}
                  </button>
                )}
              </>
            ) : !active.report.compatibilityVerified ? (
              props.onManageConnections && (
                <button onClick={props.onManageConnections}>
                  {common.chooseConnection}
                </button>
              )
            ) : (
              props.onOpenScopes && (
                <button
                  onClick={(event) => props.onOpenScopes?.(event.currentTarget)}
                >
                  {common.chooseScopeTitle}
                </button>
              )
            )
          }
        >
          {!active && <p>{common.startPathsHint}</p>}
        </PageFeedback>
      )}

      {record &&
        (record.status === "unknown" ||
          record.status === "pending" ||
          record.context.generation !== active?.generation) && (
          <section className="card" role="status">
            <p>
              {t.original}: <WriteTarget context={record.context} />
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
        onReadSaved={(artifact, trigger) => void readSaved(artifact, trigger)}
        invalidateSaved={invalidateSaved}
        contextPending={contextPending}
        onSubmission={props.onSubmission}
      />
      <section className="card stack">
        <h2>{t.query}</h2>
        <div className="search-row">
          <span className="search-input">
            <input
              ref={queryInput}
              aria-label={t.query}
              disabled={!searchEnabled}
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
                disabled={!searchEnabled}
                onClick={() => {
                  setQuery("");
                  cancel();
                }}
              >
                ✕
              </button>
            )}
          </span>
          <button
            disabled={
              !searchEnabled ||
              !!busy ||
              !query.trim() ||
              textBytes(query) > 8192
            }
            onClick={() => void read()}
          >
            {busy === "search" ? t.searching : t.search}
          </button>
        </div>
        <p className="small search-meta">{t.searchSummary}</p>
        {!searchEnabled && (
          <p>
            {contextPending
              ? common.contextUpdating
              : active?.scope
                ? operationRestriction(active.report, "search_memory", language)
                : t.noScope}
          </p>
        )}
        {busy && (
          <PageFeedback
            compact
            kind="loading"
            title={busy === "search" ? t.searching : t.loading}
          />
        )}
        {textBytes(query) > 8192 && (
          <PageFeedback compact kind="error" title={t.inputError} />
        )}
        {error != null && (
          <ErrorFeedback
            error={error}
            language={language}
            action={
              (failedReference.current ? readEnabled : searchEnabled) && (
                <button
                  disabled={!!busy}
                  onClick={() => void read(failedReference.current)}
                >
                  {common.retryRead}
                </button>
              )
            }
          />
        )}
      </section>
      <div className="memory-grid">
        <section className="card stack" aria-label={t.results}>
          <div className="card-heading">
            <h2>{t.results}</h2>
          </div>
          {hits === null && !busy && error == null && (
            <PageFeedback compact kind="empty" title={t.start} />
          )}
          {hits?.length === 0 && (
            <PageFeedback compact kind="empty" title={t.none} />
          )}
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
                const key = JSON.stringify(hit.artifact);
                const title = hit.text.split("\n", 1)[0] || hit.text;
                return (
                  <li key={key}>
                    <div className="hit-card" data-selected={selected === key}>
                      <img src={memoryIcon} alt="" />
                      <div className="hit-main">
                        <div className="hit-title">{title}</div>
                        <div className="hit-snippet">{hit.text}</div>
                      </div>
                      <div className="hit-actions">
                        {(hit.matched_by.includes("fts") ||
                          hit.matched_by.includes("text")) && (
                          <span className="badge success">{t.hitFts}</span>
                        )}
                        {!hit.matched_by.includes("fts") &&
                          !hit.matched_by.includes("text") &&
                          hit.matched_by.includes("vector") && (
                            <span className="badge">{t.hitVector}</span>
                          )}
                        <button
                          disabled={!readEnabled || !!busy}
                          aria-pressed={selected === key}
                          onClick={(event) => {
                            readButton.current = event.currentTarget;
                            void read(hit.artifact);
                          }}
                        >
                          {busy === "detail" && selected === key
                            ? t.loading
                            : t.read}
                        </button>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="note-line">{t.browseNote}</p>
          {active?.scope && !contextPending && !readEnabled && (
            <p>
              {t.read}:{" "}
              {operationRestriction(
                active.report,
                "get_artifact_revision",
                language,
              )}
            </p>
          )}
        </section>
        {entry && (
          <ReaderPane
            entry={entry}
            activeScope={active?.scope ?? null}
            savedRead={savedRead}
            language={language}
            onClose={() => {
              setEntry(null);
              setSavedRead(false);
              requestAnimationFrame(() => {
                // Another save can replace the button that opened this reader.
                const target = readButton.current?.isConnected
                  ? readButton.current
                  : queryInput.current;
                target?.focus();
              });
            }}
          />
        )}
      </div>
    </div>
  );
}
type ReaderPaneProps = {
  entry: AtomicMemoryDetail;
  activeScope: { title: string } | null;
  savedRead: boolean;
  language: Language;
  onClose: () => void;
};
function ReaderPane({
  entry,
  activeScope,
  savedRead,
  language,
  onClose,
}: ReaderPaneProps) {
  const t = memoryMessages[language];
  const readerHeading = useRef<HTMLHeadingElement>(null);
  const [copied, setCopied] = useState("");
  useEffect(() => {
    if (entry) readerHeading.current?.focus();
  }, [entry]);
  async function copy(reference: boolean) {
    try {
      await navigator.clipboard.writeText(
        reference ? JSON.stringify(entry.artifact, null, 2) : entry.text,
      );
      setCopied(t.copied);
    } catch {
      setCopied(t.copyFailed);
    }
  }
  return (
    <section className="card stack reader" aria-label={t.detail}>
      {savedRead && (
        <p role="status">
          {t.savedSection} · {entry.artifact.family}{" "}
          <code>{entry.artifact.artifact_id}</code> @{" "}
          <code>{entry.artifact.revision}</code>
        </p>
      )}
      <div className="card-heading">
        <h2 ref={readerHeading} tabIndex={-1}>
          {t.detail}
        </h2>
        <span className="badge">{entry.kind}</span>
      </div>
      <div className="plain-text reader-body" tabIndex={0}>
        {entry.text}
      </div>
      {activeScope && (
        <p className="small">
          {t.scopeBelong} <strong>{activeScope.title}</strong>
        </p>
      )}
      <div className="reference-block">
        <h3>{t.reference}</h3>
        <dl className="reference-summary">
          <dt>Artifact family</dt>
          <dd>{entry.artifact.family}</dd>
          <dt>Artifact ID</dt>
          <dd>{entry.artifact.artifact_id}</dd>
          <dt>Revision</dt>
          <dd>{entry.artifact.revision}</dd>
        </dl>
        <p className="small">{t.referenceNote}</p>
        <button className="primary-outline" onClick={() => void copy(true)}>
          {t.copyReference}
        </button>
      </div>
      <div className="actions">
        <button onClick={() => void copy(false)}>{t.copyText}</button>
        <button onClick={onClose}>{t.close}</button>
      </div>
      {copied && <p role="status">{copied}</p>}
      <details>
        <summary>{t.referenceFull}</summary>
        <pre className="plain-text">
          {JSON.stringify(entry.artifact, null, 2)}
        </pre>
      </details>
      <details>
        <summary>{t.sources}</summary>
        {entry.source_refs.length ? (
          <ul>
            {entry.source_refs.map((source) => (
              <li key={JSON.stringify([source.source_type, source.source_id])}>
                {source.source_type} <code>{source.source_id}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p>{t.noSources}</p>
        )}
      </details>
      <p className="note-line">{t.agentScopeNote}</p>
    </section>
  );
}
