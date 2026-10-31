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
  Authentication,
  DesktopState,
  ProfileView,
  StorageChoice,
  Fact,
} from "../generated/ipc";
import { contractSha256 } from "../generated/operations";
import { desktopApi } from "../shared/ipc";
import {
  connectionMessages,
  connectionError,
  discoveryMessage,
} from "./connection-messages";
import { messages, type Language } from "./messages";
import { PageFeedback, ErrorFeedback } from "./PageFeedback";
import { useConfirmation } from "./useConfirmation";
import connectionsIcon from "../assets/connections.svg";

type Props = {
  state: DesktopState | null;
  language: Language;
  onState: (value: DesktopState) => boolean | void;
  onDirty: (value: boolean) => void;
  onPendingChange?: (pending: boolean) => void;
  initialProfileId?: string;
  onActivated?: (trigger: HTMLElement) => void;
  discardDraft?: number;
};
export function Connections({
  state,
  language,
  onState,
  onDirty,
  onPendingChange,
  initialProfileId,
  onActivated,
  discardDraft = 0,
}: Props) {
  const t = connectionMessages[language];
  const { confirm, confirmation } = useConfirmation(language);
  const [selected, setSelected] = useState<string | null>(
    initialProfileId ?? null,
  );
  const profile = state?.profiles.find((p) => p.id === selected);
  const report = state?.reports.find((r) => r.connectionId === selected);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [authentication, setAuthentication] = useState<Authentication>(
    "unauthenticated_loopback",
  );
  const [secret, setSecret] = useState("");
  const [storage, setStorage] = useState<StorageChoice>("persistent");
  const [keep, setKeep] = useState(false);
  const [ca, setCa] = useState("");
  const [compatibility, setCompatibility] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<
    "save" | "check" | "activate" | "remove" | null
  >(null);
  const pending = useRef(false);
  const [notice, setNotice] = useState<{
    id: string;
    kind: "saved" | "checkedComplete" | "activationBlocked";
  } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const invalidated = useRef(false);
  const lifetime = useRef(0);
  useEffect(
    () => () => {
      lifetime.current++;
    },
    [],
  );
  function reset(p?: ProfileView) {
    setName(p?.name ?? "");
    setEndpoint(p?.endpoint ?? "");
    setAuthentication(p?.authentication ?? "unauthenticated_loopback");
    setCa(p?.caPem ?? "");
    setCompatibility(p?.compatibility ?? "");
    setSecret("");
    setKeep(
      p?.credentialState === "stored" || p?.credentialState === "session_only",
    );
    setDirty(false);
    onDirty(false);
    setError(null);
    invalidated.current = false;
  }
  useEffect(() => {
    // A response from an earlier editor can update this revision after remount.
    // Only refresh a clean form; newer input keeps its discard guard.
    if (!dirty) reset(profile);
  }, [selected, profile?.revision]);
  useEffect(() => {
    // A confirmed global Scope switch also discards an open connection draft.
    if (dirty) reset(profile);
  }, [discardDraft]);
  function change(target = false) {
    setNotice(null);
    setDirty(true);
    onDirty(true);
    if (target) {
      setKeep(false);
      setSecret("");
      if (profile && !invalidated.current) {
        invalidated.current = true;
        onPendingChange?.(true);
        void desktopApi
          .invalidate(profile.id)
          .then(onState)
          .catch(setError)
          .finally(() => onPendingChange?.(false));
      }
    }
  }
  async function select(id: string | null) {
    if (dirty && !(await confirm(t.discard))) return;
    setSecret("");
    setNotice(null);
    setSelected(id);
    reset(state?.profiles.find((p) => p.id === id));
  }
  async function action(
    kind: NonNullable<typeof busy>,
    work: () => Promise<DesktopState>,
  ) {
    if (pending.current) return;
    pending.current = true;
    const ticket = lifetime.current;
    setBusy(kind);
    setNotice(null);
    setError(null);
    onPendingChange?.(true);
    try {
      const next = await work();
      const accepted = onState(next);
      if (ticket === lifetime.current && accepted !== false) return next;
      if (ticket === lifetime.current && accepted === false)
        setError({ code: "stale_context" });
    } catch (e) {
      if (ticket === lifetime.current) setError(e);
      try {
        onState(await desktopApi.state());
      } catch {
        /* Keep the explicit failure visible. */
      }
    } finally {
      onPendingChange?.(false);
      if (ticket === lifetime.current) {
        pending.current = false;
        setBusy(null);
      }
    }
  }
  async function save() {
    const input = {
      id: profile?.id ?? null,
      revision: profile?.revision ?? null,
      name,
      endpoint,
      authentication,
      caPem: ca || null,
      compatibility: compatibility || null,
      keepCredential: keep,
      credential: secret ? { secret, storage } : null,
    };
    setSecret("");
    const next = await action("save", () => desktopApi.save(input));
    if (next) {
      const saved = next.profiles.find(
        (p) => p.id === selected || p.name === name.trim(),
      );
      if (saved) {
        setSelected(saved.id);
        reset(saved);
        setNotice({ id: saved.id, kind: "saved" });
      }
    }
  }
  async function check(activate: boolean, trigger: HTMLElement) {
    if (!profile || dirty) return;
    const next = await action(activate ? "activate" : "check", () =>
      desktopApi.check(profile.id, activate),
    );
    if (!next) return;
    if (!activate) {
      setNotice({ id: profile.id, kind: "checkedComplete" });
      return;
    }
    const active = next.active;
    if (
      active?.connectionId === profile.id &&
      active.report.revision === profile.revision &&
      active.report.compatibilityVerified &&
      (active.report.anonymousAccess || active.report.identity.value)
    ) {
      onActivated?.(trigger);
    } else {
      setNotice({ id: profile.id, kind: "activationBlocked" });
    }
  }
  function fact<T>(
    value: Fact<T> | undefined,
    render: (value: T) => string,
  ): string {
    return value?.value != null
      ? render(value.value)
      : value?.error
        ? connectionError(value.error, language)
        : t.unverified;
  }
  const isActive = !!profile && state?.active?.connectionId === profile.id;
  const readiness = report?.readiness.value?.status;
  const hasCredential =
    !!profile &&
    (profile.credentialState === "stored" ||
      profile.credentialState === "session_only") &&
    !invalidated.current;
  const availableProfiles = state?.compatibilityProfiles.filter(
    (p) => p.contractSha256 === contractSha256,
  );
  const historicalProfiles = state?.compatibilityProfiles.filter(
    (p) => p.contractSha256 !== contractSha256,
  );
  const selectedCompatibility = state?.compatibilityProfiles.find(
    (p) => p.id === compatibility,
  );
  const unavailableCompatibility =
    compatibility !== "" &&
    selectedCompatibility?.contractSha256 !== contractSha256;
  return (
    <div className="connection-grid">
      {confirmation}
      <section className="card stack">
        <div className="card-heading">
          <h2>{t.savedConnections}</h2>
          <button
            className="primary"
            disabled={!!busy}
            onClick={() => select(null)}
          >
            <span aria-hidden="true">+ </span>
            {t.add}
          </button>
        </div>
        {state && state.profiles.length === 0 && (
          <PageFeedback
            compact
            kind="empty"
            title={messages[language].noConnections}
          >
            <p>{messages[language].connectionsEmptyHint}</p>
          </PageFeedback>
        )}
        <ul className="profile-list">
          {state?.profiles.map((p) => (
            <li key={p.id}>
              <button
                className="profile-item"
                aria-pressed={selected === p.id}
                onClick={() => select(p.id)}
                disabled={!!busy}
              >
                <img src={connectionsIcon} alt="" />
                <span className="profile-main">
                  <span className="profile-name">{p.name}</span>
                  <span className="profile-endpoint">{p.endpoint}</span>
                </span>
                {state.active?.connectionId === p.id ? (
                  <span className="badge success">{t.activeBadge}</span>
                ) : (
                  <span className="badge subtle">{t.notActiveBadge}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
        {state?.pendingCredentialCleanup ? (
          <p role="status">{t.cleanup}</p>
        ) : null}
        <p className="note-line">{t.selectHint}</p>
      </section>
      <section className="card stack">
        <div className="card-heading">
          <h2>{profile ? profile.name : t.add}</h2>
          {isActive && <span className="badge success">{t.activeBadge}</span>}
        </div>
        {dirty && <p role="status">{t.dirty}</p>}
        {notice?.id === selected && <p role="status">{t[notice.kind]}</p>}
        {busy && (
          <PageFeedback
            compact
            kind="loading"
            title={
              busy === "save"
                ? t.saving
                : busy === "activate"
                  ? t.activating
                  : busy === "remove"
                    ? t.removing
                    : t.wait
            }
          />
        )}
        {error != null && <ErrorFeedback error={error} language={language} />}
        <fieldset disabled={!!busy || !state} className="profile-form">
          <label>
            {t.name}
            <input
              value={name}
              maxLength={128}
              onChange={(e) => {
                setName(e.target.value);
                change();
              }}
            />
          </label>
          <label>
            {t.endpoint}
            <input
              value={endpoint}
              maxLength={2048}
              autoComplete="off"
              spellCheck={false}
              placeholder="http://127.0.0.1:8000"
              onChange={(e) => {
                setEndpoint(e.target.value);
                change(true);
              }}
            />
          </label>
          <p className="field-hint">{t.endpointHint}</p>
          <label>
            {t.authentication}
            <select
              value={authentication}
              onChange={(e) => {
                setAuthentication(e.target.value as Authentication);
                change(true);
              }}
            >
              <option value="unauthenticated_loopback">{t.anonymous}</option>
              <option value="bearer">{t.bearer}</option>
            </select>
          </label>
          {authentication === "bearer" && (
            <>
              {hasCredential && keep ? (
                <div className="credential-block">
                  <span aria-hidden="true" className="lock">
                    🔒
                  </span>
                  <div className="credential-main">
                    <strong>{t[profile.credentialState]}</strong>
                    <p>{t.credentialNote}</p>
                  </div>
                  <button
                    onClick={() => {
                      setKeep(false);
                      setSecret("");
                      change();
                    }}
                  >
                    {t.changeCredential}
                  </button>
                </div>
              ) : (
                <>
                  {profile && (
                    <p className="field-hint">{t[profile.credentialState]}</p>
                  )}
                  <label>
                    {t.secret}
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={secret}
                      maxLength={2048}
                      onChange={(e) => {
                        setSecret(e.target.value);
                        change();
                      }}
                    />
                  </label>
                  <label>
                    {t.storage}
                    <select
                      value={storage}
                      onChange={(e) => {
                        setStorage(e.target.value as StorageChoice);
                        change();
                      }}
                    >
                      <option value="persistent">{t.persistent}</option>
                      <option value="session_only">{t.session}</option>
                    </select>
                  </label>
                </>
              )}
            </>
          )}
          <label>
            {t.compatibility}
            <select
              value={compatibility}
              onChange={(e) => {
                setCompatibility(e.target.value);
                change();
              }}
            >
              <option value="">{t.unselected}</option>
              {!!availableProfiles?.length && (
                <optgroup label={t.availableProfiles}>
                  {availableProfiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.id}
                    </option>
                  ))}
                </optgroup>
              )}
              {!!historicalProfiles?.length && (
                <optgroup label={t.historicalProfiles}>
                  {historicalProfiles.map((p) => (
                    <option key={p.id} value={p.id} disabled>
                      {p.id} ({t.unavailableProfile})
                    </option>
                  ))}
                </optgroup>
              )}
              {compatibility && !selectedCompatibility && (
                <option value={compatibility} disabled>
                  {compatibility} ({t.unavailableProfile})
                </option>
              )}
            </select>
          </label>
          <p className="field-hint">{t.compatibilityHint}</p>
          {unavailableCompatibility && (
            <PageFeedback
              compact
              kind="error"
              title={
                selectedCompatibility
                  ? t.profileContractMismatch
                  : t.profileMissing
              }
            >
              <p>{messages[language].recoverCompatibility}</p>
            </PageFeedback>
          )}
          <details>
            <summary>{t.ca}</summary>
            <label>
              {t.ca}
              <textarea
                rows={5}
                value={ca}
                maxLength={65536}
                spellCheck={false}
                onChange={(e) => {
                  setCa(e.target.value);
                  change(true);
                }}
              />
            </label>
            <p>{t.systemTrust}</p>
          </details>
          <div className="actions">
            <button
              className="primary"
              onClick={() => void save()}
              disabled={!name.trim() || !endpoint}
            >
              {busy === "save" ? t.saving : t.save}
            </button>
            <button
              onClick={async () => {
                if (!dirty || (await confirm(t.discard))) reset(profile);
              }}
            >
              {t.cancel}
            </button>
          </div>
        </fieldset>
        {profile && (
          <>
            <h3>{t.recentCheck}</h3>
            <dl className="detail-grid">
              <div>
                <dt>{t.serviceStatus}</dt>
                <dd>
                  {readiness === "ready" ? (
                    <>
                      <span aria-hidden="true" className="dot ok" /> {t.readyOK}
                    </>
                  ) : (
                    fact(report?.readiness, (v) => v.status)
                  )}
                </dd>
              </div>
              <div>
                <dt>{t.actualIdentity}</dt>
                <dd>
                  {report?.anonymousAccess
                    ? t.noIdentity
                    : fact(
                        report?.identity,
                        (v) => `${v.principal.type}: ${v.principal.id}`,
                      )}
                </dd>
              </div>
              <div>
                <dt>{t.ftsCapability}</dt>
                <dd>
                  {report?.capabilities.value?.search_modes.includes("fts")
                    ? t.available
                    : t.unverified}
                </dd>
              </div>
              <div>
                <dt>{t.live}</dt>
                <dd>{fact(report?.liveness, (v) => v.status)}</dd>
              </div>
              <div>
                <dt>{t.access}</dt>
                <dd>
                  {report?.anonymousAccess
                    ? "disabled"
                    : fact(report?.identity, (v) => v.mode)}
                </dd>
              </div>
              <div>
                <dt>{t.compatibility}</dt>
                <dd>
                  {report?.compatibilityVerified ? t.verified : t.unverified}
                </dd>
              </div>
              {report?.discovery && (
                <div>
                  <dt>{t.discovery}</dt>
                  <dd aria-live="polite">
                    {discoveryMessage(report.discovery, language)}
                  </dd>
                </div>
              )}
              <div>
                <dt>{t.capabilities}</dt>
                <dd>
                  {fact(report?.capabilities, (v) =>
                    v.artifact_families.join(", "),
                  )}
                </dd>
              </div>
              <div>
                <dt>{t.checked}</dt>
                <dd>
                  {report
                    ? new Date(report.checkedAt * 1000).toLocaleString(
                        language === "zh" ? "zh-CN" : "en-US",
                      )
                    : t.unverified}
                </dd>
              </div>
            </dl>
            <p className="note-line">{t.noPermissionClaim}</p>
            <div className="actions">
              <button
                disabled={dirty || !!busy}
                onClick={(event) => void check(false, event.currentTarget)}
              >
                {busy === "check" ? t.wait : t.check}
              </button>
              <button
                className="primary"
                disabled={dirty || !!busy}
                onClick={(event) => void check(true, event.currentTarget)}
              >
                {busy === "activate" ? t.activating : t.activate}
              </button>
              <span className="spacer" />
              <button
                className="link-danger"
                disabled={!!busy}
                onClick={async () => {
                  if (dirty && !(await confirm(t.discard))) return;
                  if (await confirm(t.removeConfirm))
                    void action("remove", () =>
                      desktopApi.remove(profile.id, profile.revision),
                    ).then((next) => {
                      if (next) {
                        setSelected(null);
                        reset();
                      }
                    });
                }}
              >
                {busy === "remove" ? t.removing : t.remove}
              </button>
            </div>
            <p className="field-hint">{t.removeNote}</p>
          </>
        )}
        <p className="note-line">{t.manageNote}</p>
      </section>
    </div>
  );
}
