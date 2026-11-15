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

import { useId, useState } from "react";
import type { DesktopState } from "../generated/ipc";
import { messages, type Language } from "./messages";
import { activeCheckReport, operationSupported } from "./context";
import { operationRestriction } from "./connection-messages";
import { PageFeedback } from "./PageFeedback";
import settingsIcon from "../assets/settings.svg";
import connectionsIcon from "../assets/connections.svg";

type Props = {
  state: DesktopState | null;
  language: Language;
  stateLoading: boolean;
  contextPending: boolean;
  dismissed: boolean;
  onDismissedChange: (dismissed: boolean) => void;
  onOpenLocalServer: () => void;
  onOpenConnections: () => void;
  onOpenProfile: (id: string) => void;
  onOpenScopes: (trigger: HTMLElement) => void;
  onOpenMemories: () => void;
  refreshing: boolean;
  onRefresh?: () => void;
};

export function OverviewGuide({
  state,
  language,
  stateLoading,
  contextPending,
  dismissed,
  onDismissedChange,
  onOpenLocalServer,
  onOpenConnections,
  onOpenProfile,
  onOpenScopes,
  onOpenMemories,
  refreshing,
  onRefresh,
}: Props) {
  const t = messages[language];
  const titleId = useId();
  const stepsId = useId();
  const [selectedProfile, setSelectedProfile] = useState("");
  const profiles = state?.profiles ?? [];
  const profileId = profiles.some((p) => p.id === selectedProfile)
    ? selectedProfile
    : (profiles[0]?.id ?? "");
  const active = state?.active;
  const profile = profiles.find((p) => p.id === active?.connectionId);
  const report = activeCheckReport(state);
  const hasScope = !!active?.scope;
  const steps = [
    [t.guideStepConnect, t.guideStepConnectHint],
    [t.guideStepScope, t.guideStepScopeHint],
    [t.guideStepMemory, t.guideStepMemoryHint],
  ];
  const currentStep = !active ? 0 : !hasScope ? 1 : 2;
  return (
    <>
      <section className="card overview-guide" aria-labelledby={titleId}>
        <div className="card-heading">
          <h2 id={titleId}>{t.guideTitle}</h2>
          <div className="actions">
            {state && !active && (
              <h3 className="badge subtle guide-status">{t.noActiveTitle}</h3>
            )}
            {onRefresh && (
              <button
                disabled={refreshing || !state || contextPending}
                onClick={onRefresh}
              >
                {refreshing ? t.refreshing : t.refresh}
              </button>
            )}
            <button
              aria-expanded={!dismissed}
              aria-controls={stepsId}
              onClick={() => onDismissedChange(!dismissed)}
            >
              {dismissed ? t.guideRevisit : t.guideDismiss}
            </button>
          </div>
        </div>
        {!dismissed && <p className="guide-intro">{t.guideIntro}</p>}
        {!state && (
          <p className="note-line">
            {stateLoading ? t.loadingState : t.stateUnavailable}
          </p>
        )}
        {contextPending && <p role="status">{t.contextUpdating}</p>}
        {!active ? (
          <>
            <div className="guide-paths">
              <div className="guide-path local-path">
                <div className="guide-path-heading">
                  <img src={settingsIcon} alt="" />
                  <h3>{t.guideLocalTitle}</h3>
                  {!dismissed && (
                    <span className="badge">{t.guidePersonal}</span>
                  )}
                </div>
                {!dismissed && <p>{t.guideLocalHint}</p>}
                <button className="primary" onClick={onOpenLocalServer}>
                  {t.guideLocalOpen}
                </button>
              </div>
              <div className="guide-path">
                <div className="guide-path-heading">
                  <img src={connectionsIcon} alt="" />
                  <h3>{t.connectExisting}</h3>
                </div>
                {!dismissed && <p>{t.guideExistingHint}</p>}
                <button onClick={onOpenConnections}>
                  {t.guideAddConnection}
                </button>
              </div>
            </div>
            <p className="note-line">{t.guideLocalUnavailable}</p>
            {profiles.length > 0 && (
              <div className="guide-saved">
                <label>
                  {t.guideSavedLabel}
                  <select
                    value={profileId}
                    disabled={contextPending}
                    onChange={(event) => setSelectedProfile(event.target.value)}
                  >
                    {profiles.map((saved) => (
                      <option key={saved.id} value={saved.id}>
                        {saved.name} · {saved.endpoint} · {saved.id}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={contextPending}
                  onClick={() => onOpenProfile(profileId)}
                >
                  {t.guideReviewProfile}
                </button>
                <p className="small">{t.guideSavedHint}</p>
              </div>
            )}
          </>
        ) : (
          <div className="guide-next stack">
            <h3>{hasScope ? t.guideMemoryTitle : t.chooseScopeTitle}</h3>
            <p className="guide-target">
              <strong>{profile?.name ?? active.connectionId}</strong>
              {profile && <span>{profile.endpoint}</span>}
              {active.scope && (
                <>
                  <span>{active.scope.title}</span>
                  <code>{active.scope.scope_id}</code>
                </>
              )}
            </p>
            {!report?.compatibilityVerified ? (
              <PageFeedback
                compact
                kind="unavailable"
                title={t.compatibilityNeeded}
              >
                <p>{t.recoverCompatibility}</p>
              </PageFeedback>
            ) : !hasScope ? (
              <>
                <p>{t.recoverScope}</p>
                {(["list_scopes", "get_scope"] as const).map((operation) =>
                  !operationSupported(report, operation) ? (
                    <p key={operation}>
                      {operationRestriction(report, operation, language)}
                    </p>
                  ) : null,
                )}
              </>
            ) : (
              <>
                {!dismissed && <p>{t.guideMemoryHint}</p>}
                {(
                  [
                    ["remember_memory", t.featureSave],
                    ["search_memory", t.featureSearch],
                    ["get_artifact_revision", t.featureRead],
                  ] as const
                ).map(([operation, name]) =>
                  !operationSupported(report, operation) ? (
                    <p key={operation}>
                      {name}:{" "}
                      {operationRestriction(report, operation, language)}
                    </p>
                  ) : null,
                )}
              </>
            )}
            <div className="actions">
              {report?.compatibilityVerified &&
                (hasScope ? (
                  <button
                    className="primary"
                    disabled={contextPending}
                    onClick={onOpenMemories}
                  >
                    {t.openMemories}
                  </button>
                ) : (
                  <button
                    className="primary"
                    disabled={contextPending}
                    onClick={(event) => onOpenScopes(event.currentTarget)}
                  >
                    {t.chooseScopeTitle}
                  </button>
                ))}
              <button onClick={onOpenConnections}>{t.chooseConnection}</button>
            </div>
          </div>
        )}
        {!dismissed && <p className="guide-info">{t.guideInfo}</p>}
      </section>
      <section id={stepsId} className="card" hidden={dismissed}>
        <h2>{t.guideAfterConnection}</h2>
        <ol className="guide-steps">
          {steps.map(([title, hint], index) => (
            <li
              className="step"
              key={title}
              aria-current={state && currentStep === index ? "step" : undefined}
            >
              <span
                aria-hidden="true"
                className="step-no"
              >{`0${index + 1}`}</span>
              <div>
                <h3>{title}</h3>
                <p>{hint}</p>
              </div>
            </li>
          ))}
        </ol>
        <p className="note-line">{t.overviewBoundary}</p>
      </section>
    </>
  );
}
