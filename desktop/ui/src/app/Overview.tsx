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

import type { DesktopState } from "../generated/ipc";
import { messages, type Language } from "./messages";
import memoryIcon from "../assets/memory.svg";
import connectionsIcon from "../assets/connections.svg";
import retrievalIcon from "../assets/retrieval.svg";

type Page = "connections" | "memories" | "settings";
type Props = {
  state: DesktopState | null;
  language: Language;
  onNavigate: (page: Page) => void;
};
export function Overview({ state, language, onNavigate }: Props) {
  const t = messages[language];
  const active = state?.active;
  const profile = state?.profiles.find((p) => p.id === active?.connectionId);
  const report =
    state?.reports.find((item) => item.connectionId === active?.connectionId) ??
    active?.report;
  const readiness = report?.readiness.value?.status;
  const enabled = !!active?.scope && !!report?.compatibilityVerified;
  const principal = report?.anonymousAccess
    ? null
    : report?.identity.value?.principal;
  const identity = !active
    ? t.unverified
    : report?.anonymousAccess
      ? t.anonymousIdentity
      : principal
        ? (principal.description ?? `${principal.type}: ${principal.id}`)
        : t.unverified;
  const features = [
    { icon: memoryIcon, name: t.featureSave, hint: t.featureSaveHint },
    { icon: retrievalIcon, name: t.featureSearch, hint: t.featureSearchHint },
    { icon: connectionsIcon, name: t.featureRead, hint: t.featureReadHint },
  ];
  const environment = [
    t.envService,
    t.envOwnership,
    t.envAgent,
    t.envConfig,
    t.envHostLoad,
    t.envCaptureRecall,
  ];
  return (
    <div className="stack">
      <section className="card">
        <div className="card-heading">
          <h2>{t.current}</h2>
          {active && (
            <span className={readyBadge(readiness)}>
              {readiness === "ready"
                ? t.serviceReady
                : readiness
                  ? t.serviceNotReady
                  : t.unverified}
            </span>
          )}
        </div>
        {active && profile ? (
          <>
            <div className="connection-summary">
              <img src={connectionsIcon} alt="" />
              <div>
                <p className="name">{profile.name}</p>
                <p className="endpoint">{profile.endpoint}</p>
              </div>
              <span className="topbar-spacer" />
              <button onClick={() => onNavigate("connections")}>
                {t.manage}
              </button>
            </div>
            <dl className="fact-grid">
              <div className="fact">
                <dt>{t.identityLabel}</dt>
                <dd>{identity}</dd>
              </div>
              <div className="fact">
                <dt>{t.authLabel}</dt>
                <dd>
                  {profile.authentication === "bearer"
                    ? t.authBearer
                    : t.authAnonymous}
                </dd>
              </div>
              <div className="fact">
                <dt>{t.currentScopeLabel}</dt>
                <dd>
                  {active.scope
                    ? `${active.scope.title} · ${t.exactScope}`
                    : t.noScopeSelected}
                </dd>
              </div>
              <div className="fact">
                <dt>{t.compatibilityLabel}</dt>
                <dd>
                  {profile.compatibility
                    ? `${profile.compatibility} · ${report?.compatibilityVerified ? t.compatibilityVerified : t.unverified}`
                    : t.noCompatibilitySelected}
                </dd>
              </div>
            </dl>
            <p className="note-line">{t.readinessNote}</p>
          </>
        ) : (
          <div className="empty-state">
            <h3>{t.noActiveTitle}</h3>
            <p>{t.noActiveBody}</p>
            <button
              className="primary"
              onClick={() => onNavigate("connections")}
            >
              {t.connect}
            </button>
          </div>
        )}
      </section>
      <div className="overview-grid">
        <section className="card">
          <div className="card-heading">
            <h2>{t.features}</h2>
          </div>
          {features.map((feature) => (
            <div className="feature-row" key={feature.name}>
              <div>
                <span className="feature-name">
                  <img src={feature.icon} alt="" />
                  {feature.name}
                </span>
                <p>{feature.hint}</p>
              </div>
              <span className={enabled ? "badge success" : "badge subtle"}>
                {enabled ? t.available : t.notAvailable}
              </span>
            </div>
          ))}
          <p className="note-line">{t.catalogNote}</p>
        </section>
        <section className="card">
          <div className="card-heading">
            <h2>{t.localEnv}</h2>
            <button onClick={() => onNavigate("settings")}>
              {t.viewDiagnostics}
            </button>
          </div>
          <dl>
            {environment.map((item) => (
              <div className="env-row" key={item}>
                <dt>{item}</dt>
                <dd>
                  <span aria-hidden="true" className="dot" />
                  {t.unverified}
                </dd>
              </div>
            ))}
          </dl>
          <p className="note-line">{t.envNote}</p>
        </section>
      </div>
      <section className="card">
        <div className="card-heading">
          <h2>{t.gettingStarted}</h2>
        </div>
        <p>{t.gettingStartedHint}</p>
        <div className="steps">
          <div className="step">
            <span aria-hidden="true" className="step-no">
              01
            </span>
            <div>
              <h3>{t.stepSave}</h3>
              <p>{t.stepSaveHint}</p>
            </div>
          </div>
          <div className="step">
            <span aria-hidden="true" className="step-no">
              02
            </span>
            <div>
              <h3>{t.stepSearch}</h3>
              <p>{t.stepSearchHint}</p>
            </div>
          </div>
        </div>
        <p className="note-line">{t.overviewBoundary}</p>
        <div className="actions getting-started-actions">
          <span className="spacer" />
          <button className="primary" onClick={() => onNavigate("memories")}>
            {t.openMemories}
          </button>
        </div>
      </section>
    </div>
  );
  function readyBadge(status: string | null | undefined) {
    return status === "ready" ? "badge success" : "badge subtle";
  }
}
