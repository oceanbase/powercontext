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
import type { ObservationUnit, SessionSummary } from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import type { Language } from "./messages";
import { Modal } from "./Modal";
import { PageFeedback } from "./PageFeedback";
import { sessionMessages } from "./session-messages";
import { useConfirmation } from "./useConfirmation";

export function SessionView({ language }: { language: Language }) {
  const t = sessionMessages[language];
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState<boolean | null>(null);
  const [preview, setPreview] = useState(false);
  const sequence = useRef(0);
  const { confirm, confirmation } = useConfirmation(language);

  async function load() {
    const request = ++sequence.current;
    setLoading(true);
    setFailed(false);
    setSummary(null);
    try {
      const next = await desktopApi.sessionSummary();
      if (request === sequence.current) setSummary(next);
    } catch {
      if (request === sequence.current) setFailed(true);
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load();
    return () => {
      sequence.current++;
    };
  }, []);

  async function reset() {
    if (!(await confirm(t.resetConfirm))) return;
    const request = ++sequence.current;
    setResetting(true);
    setResetResult(null);
    try {
      await desktopApi.resetSessionObservations();
      if (request !== sequence.current) return;
      setResetResult(true);
      setResetting(false);
      await load();
    } catch {
      if (request === sequence.current) {
        setResetResult(false);
        setResetting(false);
      }
    }
  }

  const supported = summary?.schemaVersion === 2;
  const number = new Intl.NumberFormat(language, { maximumFractionDigits: 1 });
  function timestamp(value: number) {
    const date = new Date(value);
    return (
      <time dateTime={date.toISOString()}>
        {date.toISOString().slice(0, 19).replace("T", " ")}
      </time>
    );
  }
  return (
    <div className="stack session-view">
      <section className="card stack">
        <h2>{t.title}</h2>
        <p>{t.scope}</p>
        {supported && summary && (
          <dl className="session-totals">
            {(["http_request", "user_operation"] as const).map((unit) => (
              <div key={unit}>
                <dt>{unit === "http_request" ? t.http : t.actions}</dt>
                <dd>
                  {number.format(
                    summary.operations
                      .filter((group) => group.unit === unit)
                      .reduce((total, group) => total + group.sampleCount, 0),
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <div className="actions">
          <button
            disabled={loading || resetting}
            onClick={() => {
              setResetResult(null);
              void load();
            }}
          >
            {t.refresh}
          </button>
          <button
            disabled={!supported || loading || resetting}
            onClick={() => setPreview(true)}
          >
            {t.preview}
          </button>
          <button
            disabled={loading || resetting || (!!summary && !supported)}
            onClick={() => void reset()}
          >
            {t.reset}
          </button>
        </div>
        {resetting && (
          <PageFeedback compact kind="loading" title={t.resetting} />
        )}
        {resetResult !== null && (
          <PageFeedback
            compact
            kind={resetResult ? "success" : "error"}
            title={resetResult ? t.resetDone : t.resetFailed}
          />
        )}
      </section>
      {loading ? (
        <PageFeedback kind="loading" title={t.loading} />
      ) : failed ? (
        <PageFeedback
          kind="error"
          title={t.readFailed}
          action={<button onClick={() => void load()}>{t.retry}</button>}
        >
          <p>{t.retryHint}</p>
        </PageFeedback>
      ) : summary && !supported ? (
        <PageFeedback kind="unavailable" title={t.unsupported}>
          <p>{t.unsupportedHint}</p>
        </PageFeedback>
      ) : (
        summary && (
          <>
            <section className="card stack">
              <h3>{t.coverage}</h3>
              <dl className="session-facts">
                <div>
                  <dt>{t.sessionStart}</dt>
                  <dd>{timestamp(summary.sessionStartedAtMs)}</dd>
                </div>
                <div>
                  <dt>{t.windowStart}</dt>
                  <dd>{timestamp(summary.windowStartedAtMs)}</dd>
                </div>
                <div>
                  <dt>{t.snapshotAt}</dt>
                  <dd>{timestamp(summary.generatedAtMs)}</dd>
                </div>
                <div>
                  <dt>{t.retained}</dt>
                  <dd>{number.format(summary.recent.length)}</dd>
                </div>
                <div>
                  <dt>{t.capacity}</dt>
                  <dd>{number.format(summary.capacity)}</dd>
                </div>
                <div>
                  <dt>{t.evicted}</dt>
                  <dd>{number.format(summary.evicted)}</dd>
                </div>
                <div>
                  <dt>{t.dropped}</dt>
                  <dd>{number.format(summary.droppedSinceStart)}</dd>
                </div>
              </dl>
              <p>{t.bounds}</p>
              {(summary.evicted > 0 || summary.droppedSinceStart > 0) && (
                <PageFeedback compact kind="warning" title={t.incomplete}>
                  <p>{t.incompleteHint}</p>
                </PageFeedback>
              )}
              <details>
                <summary>{t.operationCoverage}</summary>
                {(["http_request", "user_operation"] as const).map((unit) => (
                  <div key={unit}>
                    <h4>{unit === "http_request" ? t.http : t.actions}</h4>
                    <ul className="session-coverage">
                      {(unit === "http_request"
                        ? summary.coverage.httpOperations
                        : summary.coverage.userOperations
                      ).map((operation) => (
                        <li key={operation}>{t.operations[operation]}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </details>
            </section>
            <section className="card stack" aria-label={t.localOps}>
              <h3>{t.localOps}</h3>
              {!summary.coverage.installerOps ? (
                <p>{t.localOpsUnavailable}</p>
              ) : (
                <>
                  <p>{t.localOpsHint}</p>
                  <p>{t.opsReferenceHint}</p>
                  <dl className="session-facts">
                    <div>
                      <dt>{t.opsRetained}</dt>
                      <dd>
                        {number.format(summary.installerOps.recent.length)}
                      </dd>
                    </div>
                    <div>
                      <dt>{t.opsCapacity}</dt>
                      <dd>{number.format(summary.installerOps.capacity)}</dd>
                    </div>
                    <div>
                      <dt>{t.evicted}</dt>
                      <dd>{number.format(summary.installerOps.evicted)}</dd>
                    </div>
                    <div>
                      <dt>{t.dropped}</dt>
                      <dd>
                        {number.format(summary.installerOps.droppedSinceStart)}
                      </dd>
                    </div>
                  </dl>
                  {(summary.installerOps.evicted > 0 ||
                    summary.installerOps.droppedSinceStart > 0) && (
                    <PageFeedback
                      compact
                      kind="warning"
                      title={t.opsIncomplete}
                    />
                  )}
                  {summary.installerOps.recent.length === 0 ? (
                    <p>{t.opsEmpty}</p>
                  ) : (
                    <div
                      className="session-table"
                      role="region"
                      aria-label={t.localOps}
                      tabIndex={0}
                    >
                      <table>
                        <caption>{t.localOps}</caption>
                        <thead>
                          <tr>
                            {[
                              t.opsReference,
                              t.opsAction,
                              t.opsAttempt,
                              t.opsPhase,
                              t.opsAttemptPhase,
                              t.opsOutcome,
                              t.opsError,
                              t.opsObservedAt,
                            ].map((label) => (
                              <th scope="col" key={label}>
                                {label}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {summary.installerOps.recent.map((record, index) => (
                            <tr key={index}>
                              <th scope="row">
                                <code>{record.operationId}</code>
                              </th>
                              <td>{t.opsActions[record.action]}</td>
                              <td>{record.attempt ?? t.notObserved}</td>
                              <td>
                                {record.phase
                                  ? t.opsPhases[record.phase]
                                  : t.notObserved}
                              </td>
                              <td>
                                {record.attemptPhase
                                  ? t.opsAttemptPhases[record.attemptPhase]
                                  : t.notObserved}
                              </td>
                              <td>{t.opsOutcomes[record.outcome]}</td>
                              <td>
                                {record.errorCode || record.bridgeError ? (
                                  <>
                                    <code>{record.errorCode}</code>{" "}
                                    <code>{record.bridgeError}</code>
                                  </>
                                ) : (
                                  t.notObserved
                                )}
                              </td>
                              <td>{timestamp(record.observedAtMs)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </section>
            {summary.recent.length === 0 && (
              <PageFeedback kind="empty" title={t.empty}>
                <p>{t.emptyHint}</p>
              </PageFeedback>
            )}
            <section className="card stack">
              <p>{t.outcomesHint}</p>
              <p>{t.timingHint}</p>
              <p className="small">{t.scrollHint}</p>
              {(["http_request", "user_operation"] as const).map((unit) => (
                <OperationTable
                  key={unit}
                  unit={unit}
                  summary={summary}
                  language={language}
                />
              ))}
            </section>
          </>
        )
      )}
      {confirmation}
      {preview && (
        <DiagnosticPreview
          language={language}
          onClose={() => setPreview(false)}
        />
      )}
    </div>
  );
}

function OperationTable({
  unit,
  summary,
  language,
}: {
  unit: ObservationUnit;
  summary: SessionSummary;
  language: Language;
}) {
  const t = sessionMessages[language];
  const groups = summary.operations.filter((group) => group.unit === unit);
  const title = unit === "http_request" ? t.http : t.actions;
  const number = new Intl.NumberFormat(language, { maximumFractionDigits: 1 });
  return (
    <div className="stack session-unit">
      <h3>{title}</h3>
      <p>{unit === "http_request" ? t.httpHint : t.actionHint}</p>
      {groups.length === 0 ? (
        <p>{t.noSamples}</p>
      ) : (
        <div
          className="session-table"
          role="region"
          aria-label={title}
          tabIndex={0}
        >
          <table>
            <caption>{title}</caption>
            <thead>
              <tr>
                {[
                  t.operation,
                  t.samples,
                  t.success,
                  t.failure,
                  t.cancelled,
                  t.unknown,
                  t.minimum,
                  t.mean,
                  t.maximum,
                ].map((label) => (
                  <th scope="col" key={label}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => (
                <tr key={group.operation}>
                  <th scope="row">
                    {t.operations[group.operation]}
                    <code>{group.operation}</code>
                  </th>
                  <td>{number.format(group.sampleCount)}</td>
                  <td>{number.format(group.outcomes.success)}</td>
                  <td>{number.format(group.outcomes.failure)}</td>
                  <td>{number.format(group.outcomes.cancelled)}</td>
                  <td>{number.format(group.outcomes.unknown)}</td>
                  <td>{number.format(group.minDurationMs)}</td>
                  <td>{number.format(group.meanDurationMs)}</td>
                  <td>{number.format(group.maxDurationMs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DiagnosticPreview({
  language,
  onClose,
}: {
  language: Language;
  onClose: () => void;
}) {
  const t = sessionMessages[language];
  const descriptionId = useId();
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState<boolean | null>(null);
  const sequence = useRef(0);
  const copySequence = useRef(0);
  async function load() {
    const request = ++sequence.current;
    copySequence.current++;
    setText(null);
    setFailed(false);
    setCopied(null);
    try {
      const value = await desktopApi.previewSessionDiagnostics();
      if (request === sequence.current) {
        if (value.length === 0) setFailed(true);
        else setText(value);
      }
    } catch {
      if (request === sequence.current) setFailed(true);
    }
  }
  useEffect(() => {
    void load();
    return () => {
      sequence.current++;
      copySequence.current++;
    };
  }, []);
  async function copy() {
    if (text === null || copying) return;
    const request = ++copySequence.current;
    setCopying(true);
    setCopied(null);
    try {
      await navigator.clipboard.writeText(text);
      if (request === copySequence.current) setCopied(true);
    } catch {
      if (request === copySequence.current) setCopied(false);
    } finally {
      if (request === copySequence.current) setCopying(false);
    }
  }
  return (
    <Modal
      label={t.previewTitle}
      descriptionId={descriptionId}
      onClose={onClose}
    >
      <div className="stack session-preview">
        <div className="modal-heading">
          <h2>{t.previewTitle}</h2>
          <button onClick={onClose}>{t.close}</button>
        </div>
        <p id={descriptionId}>{t.previewHint}</p>
        {failed ? (
          <PageFeedback
            compact
            kind="error"
            title={t.previewFailed}
            action={
              <button onClick={() => void load()}>{t.previewRetry}</button>
            }
          />
        ) : text === null ? (
          <PageFeedback compact kind="loading" title={t.previewLoading} />
        ) : (
          <>
            <textarea
              aria-label={t.previewText}
              readOnly
              value={text}
              rows={12}
              onFocus={(event) => event.currentTarget.select()}
            />
            {copied !== null && (
              <PageFeedback
                compact
                kind={copied ? "success" : "error"}
                title={copied ? t.copied : t.copyFailed}
              />
            )}
            <div className="actions">
              <button
                className="primary"
                disabled={copying}
                onClick={() => void copy()}
              >
                {copying ? t.copying : t.copy}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
