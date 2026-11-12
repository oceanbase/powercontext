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

import { useRef, useState, type Ref } from "react";
import type {
  OpsOperation,
  OpsOptions,
  OpsPlanApproval,
} from "../generated/ipc";
import type { LocalServerController } from "./useLocalServer";
import type { Language } from "./messages";
import { localText, localError } from "./local-server-messages";
import { PageFeedback } from "./PageFeedback";
import { Modal } from "./Modal";
import { useConfirmation } from "./useConfirmation";

export function LocalServerSettings({
  language,
  headingRef,
  controller: c,
  onManageConnections,
  onConnect,
  onBeforeApply,
  connecting = false,
  connectionAvailable = true,
}: {
  language: Language;
  headingRef?: Ref<HTMLHeadingElement>;
  controller: LocalServerController;
  onManageConnections: () => void;
  onConnect: () => Promise<void>;
  onBeforeApply: (plan: OpsPlanApproval) => Promise<boolean>;
  connecting?: boolean;
  connectionAvailable?: boolean;
}) {
  const t = (key: string) => localText(language, key);
  const [port, setPort] = useState("8000");
  const [login, setLogin] = useState(true);
  const trigger = useRef<HTMLElement | null>(null);
  const approving = useRef(false);
  const { confirm, confirmation } = useConfirmation(language);
  const facts = c.inspection?.components;
  const available =
    c.inspection?.outcome === "succeeded" && c.references?.bindingAvailable;
  const missingResources =
    c.inspection?.bridgeError === "unavailable" ||
    (c.references?.bindingAvailable === false &&
      (!c.inspection || c.inspection.outcome === "succeeded"));
  const disabled = !!c.busy || connecting || !available || c.loading;
  const status = c.record?.status;
  const running = c.busy === "execute";
  const cancelAvailable =
    (running && !!c.progress?.attempt) ||
    (status?.outcome === "pending" && status.executionState !== "interrupted");
  const unresolved =
    c.record && ["pending", "unknown", "uncertain"].includes(c.record.outcome);
  const portValid =
    /^\d+$/.test(port) && Number(port) >= 1 && Number(port) <= 65535;
  function preview(operation: OpsOperation, button: HTMLElement) {
    trigger.current = button;
    const options: OpsOptions = {};
    if (operation === "install") {
      options.httpPort = Number(port);
      options.startOnLogin = login;
    } else if (operation === "register") {
      options.startOnLogin = login;
    }
    void c.preflight({ operation, options });
  }
  return (
    <section
      id="local-server"
      className="card stack local-server"
      aria-labelledby="local-server-heading"
    >
      <h2 id="local-server-heading" ref={headingRef} tabIndex={-1}>
        {t("title")}
      </h2>
      <p>{t("intro")}</p>
      <div className="actions">
        <button disabled={c.loading} onClick={() => void c.refresh()}>
          {t("refresh")}
        </button>
        <button onClick={onManageConnections}>{t("existing")}</button>
      </div>
      {c.loading && (
        <PageFeedback compact kind="loading" title={t("loading")} />
      )}
      {!c.loading && missingResources && (
        <PageFeedback
          compact
          headingLevel={3}
          kind="unavailable"
          title={t("unavailable")}
        >
          <p>{t("unavailableHint")}</p>
        </PageFeedback>
      )}
      {c.error && <PageFeedback compact kind="error" title={t(c.error)} />}
      {!missingResources &&
        (c.inspection?.error || c.inspection?.bridgeError) && (
          <PageFeedback
            compact
            kind="warning"
            title={t(
              localError(c.inspection.error ?? c.inspection.bridgeError),
            )}
          />
        )}
      {facts && (
        <dl className="fact-grid" aria-label={t("state")}>
          {(
            [
              "resources",
              "configuration",
              "registration",
              "managerOwnership",
              "manager",
              "liveness",
              "readiness",
              "database",
              "installationStage",
            ] as const
          ).map((key) => (
            <div className="fact" key={key}>
              <dt>{t(key)}</dt>
              <dd>{t(facts[key] ?? "unknown")}</dd>
            </div>
          ))}
          {facts.endpoint && (
            <div className="fact">
              <dt>{t("endpoint")}</dt>
              <dd>{facts.endpoint}</dd>
            </div>
          )}
        </dl>
      )}
      {facts?.managerOwnership === "foreign" && (
        <p role="alert">{t("ownership_conflict")}</p>
      )}
      <p>{t("policy")}</p>
      <fieldset disabled={!!c.busy || connecting}>
        <legend>{t("preview")}</legend>
        <label htmlFor="local-port">{t("port")}</label>
        <input
          id="local-port"
          type="number"
          min={1}
          max={65535}
          value={port}
          onChange={(event) => setPort(event.target.value)}
        />
        <label className="setting-row">
          <input
            type="checkbox"
            checked={login}
            disabled={!c.references?.loginStartChoice}
            onChange={(event) => setLogin(event.target.checked)}
          />
          {t("login")}
        </label>
        <div className="actions">
          {(
            [
              "install",
              "repair",
              "start",
              "stop",
              "restart",
              "register",
              "remove_registration",
            ] as const
          ).map((operation) => (
            <button
              key={operation}
              disabled={
                disabled ||
                !!unresolved ||
                (operation === "install" && !portValid)
              }
              onClick={(event) => preview(operation, event.currentTarget)}
            >
              {t(operation)}
            </button>
          ))}
        </div>
      </fieldset>
      {c.busy && (
        <PageFeedback
          compact
          kind="loading"
          title={t(running ? "executing" : "preflighting")}
        >
          {running && (
            <>
              <p>{t(c.progress?.phase ?? "pending")}</p>
              {!c.listening && <p>{t("progressMissing")}</p>}
            </>
          )}
        </PageFeedback>
      )}
      <div className="actions">
        <button
          disabled={
            disabled ||
            !connectionAvailable ||
            !facts?.installationId ||
            facts.manager !== "active" ||
            facts.managerOwnership !== "owned"
          }
          onClick={async () => {
            if (await confirm(t("connectConsent"))) await onConnect();
          }}
        >
          {t("connect")}
        </button>
        <a href="#local-diagnostics">{t("diagnostics")}</a>
      </div>
      <p className="small">{t("independent")}</p>
      <p className="small">{t("preservation")}</p>
      <h3>{t("history")}</h3>
      {c.references?.operations.length ? (
        <>
          <label htmlFor="local-operation">{t("select")}</label>
          <select
            id="local-operation"
            value={c.selected}
            disabled={!!c.busy}
            onChange={(event) => c.choose(event.target.value)}
          >
            {[...c.references.operations].reverse().map((reference) => (
              <option key={reference.operationId} value={reference.operationId}>
                {reference.operation ? t(reference.operation) : t("operation")}{" "}
                · {reference.operationId}
              </option>
            ))}
          </select>
          {c.reference?.bindingStatus !== "current" && (
            <p role="alert">
              {t(
                c.reference?.bindingStatus === "different"
                  ? "different"
                  : "unavailableHint",
              )}
            </p>
          )}
          {c.reference && !c.reference.operation && <p>{t("legacyHint")}</p>}
        </>
      ) : (
        <p>{t("none")}</p>
      )}
      {c.selected && (
        <p>
          <code>{c.selected}</code>
        </p>
      )}
      {c.record && (
        <PageFeedback
          compact
          kind={c.record.outcome === "succeeded" ? "success" : "warning"}
          title={t(c.record.outcome)}
        >
          {c.record.attempt && (
            <p>
              {t("attempt")}: {c.record.attempt}
            </p>
          )}
          {status && <p>{t(status.executionState)}</p>}
          {!!unresolved && <p>{t("uncertainHint")}</p>}
          {status?.cancelRequested && <p>{t("cancelHint")}</p>}
          {(c.record.error ||
            c.record.bridgeError ||
            status?.result?.error) && (
            <p>
              {t(
                localError(
                  c.record.error ??
                    c.record.bridgeError ??
                    status?.result?.error,
                ),
              )}
            </p>
          )}
          {status && (
            <ol aria-label={t("attempts")}>
              {status.history.map((attempt) => (
                <li key={attempt.attempt}>
                  {attempt.attempt}: {t(attempt.outcome ?? "pending")}
                </li>
              ))}
            </ol>
          )}
        </PageFeedback>
      )}
      <div className="actions">
        <button
          disabled={
            !c.selected ||
            c.querying ||
            (c.reference?.bindingStatus !== "current" &&
              !(running && c.progress?.operationId === c.selected))
          }
          onClick={() => void c.query()}
        >
          {t("status")}
        </button>
        <button
          disabled={!cancelAvailable || c.controlling}
          onClick={() => void c.cancel()}
        >
          {t("cancel")}
        </button>
        <button
          disabled={!c.canResume || disabled}
          onClick={(event) => {
            if (!c.reference?.operation || !c.reference.options || !status)
              return;
            trigger.current = event.currentTarget;
            void c.preflight({
              operation: c.reference.operation,
              options: c.reference.options,
              resume: {
                operationId: status.operationId,
                expectedAttempt: status.attempt,
              },
            });
          }}
        >
          {t("resume")}
        </button>
      </div>
      <p className="small">{t("recoveryHint")}</p>
      {c.approval && (
        <Modal
          label={t("plan")}
          onClose={c.dismiss}
          returnFocus={() => trigger.current}
        >
          <div className="local-server-plan stack">
            <h2>{t("plan")}</h2>
            <p>{t(c.approval.plan.operation)}</p>
            <dl>
              {(
                [
                  ["version", c.approval.plan.target.packageVersion],
                  ["program", c.approval.plan.target.pythonExecutable],
                  ["configPath", c.approval.plan.target.envFile],
                  ["dataPath", c.approval.plan.target.dataDir],
                  ["endpoint", c.approval.plan.target.endpoint],
                ] as const
              ).map(([key, value]) => (
                <div key={key}>
                  <dt>{t(key)}</dt>
                  <dd>{value ?? t("unknown")}</dd>
                </div>
              ))}
              <div>
                <dt>{t("login")}</dt>
                <dd>
                  {c.approval.plan.target.startOnLogin ? t("yes") : t("no")}
                </dd>
              </div>
              {c.approval.plan.installation && (
                <>
                  <div>
                    <dt>{t("installationOwnership")}</dt>
                    <dd>
                      {t(
                        `classification_${c.approval.plan.installation.classification}`,
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("database")}</dt>
                    <dd>{t(c.approval.plan.installation.database)}</dd>
                  </div>
                  <div>
                    <dt>{t("resources")}</dt>
                    <dd>{t(c.approval.plan.installation.resources)}</dd>
                  </div>
                  <div>
                    <dt>{t("space")}</dt>
                    <dd>{c.approval.plan.installation.spaceRequired}</dd>
                  </div>
                </>
              )}
            </dl>
            <p>{t(c.approval.plan.effects.mayStart ? "starts" : "noStart")}</p>
            <p>{t("preservation")}</p>
            <p>{t("expiry")}</p>
            <div className="actions">
              <button onClick={c.dismiss}>{t("dismiss")}</button>
              <button
                className="primary"
                onClick={async () => {
                  if (approving.current || !c.approval) return;
                  approving.current = true;
                  const approved = c.approval.plan;
                  try {
                    if (await onBeforeApply(approved))
                      await c.execute(approved.approvalId);
                  } finally {
                    approving.current = false;
                  }
                }}
              >
                {t("approve")}
              </button>
            </div>
          </div>
        </Modal>
      )}
      {confirmation}
    </section>
  );
}
