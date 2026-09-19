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
import { desktopApi } from "../shared/ipc";
import { connectionMessages, connectionError } from "./connection-messages";
import type { Language } from "./messages";

type Props = {
  state: DesktopState | null;
  language: Language;
  onState: (value: DesktopState) => void;
  onDirty: (value: boolean) => void;
};
export function Connections({ state, language, onState, onDirty }: Props) {
  const t = connectionMessages[language];
  const [selected, setSelected] = useState<string | null>(null);
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const invalidated = useRef(false);
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
    reset(profile);
  }, [selected, profile?.revision]);
  function change(target = false) {
    setDirty(true);
    onDirty(true);
    if (target) {
      setKeep(false);
      setSecret("");
      if (profile && !invalidated.current) {
        invalidated.current = true;
        void desktopApi.invalidate(profile.id).then(onState).catch(setError);
      }
    }
  }
  function select(id: string | null) {
    if (dirty && !window.confirm(t.discard)) return;
    setSecret("");
    setSelected(id);
    reset(state?.profiles.find((p) => p.id === id));
  }
  async function action(work: () => Promise<DesktopState>) {
    setBusy(true);
    setError(null);
    try {
      const next = await work();
      onState(next);
      return next;
    } catch (e) {
      setError(e);
      try {
        onState(await desktopApi.state());
      } catch {
        /* Keep the explicit failure visible. */
      }
    } finally {
      setBusy(false);
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
    const next = await action(() => desktopApi.save(input));
    if (next) {
      const saved = next.profiles.find(
        (p) => p.id === selected || p.name === name.trim(),
      );
      if (saved) {
        setSelected(saved.id);
        reset(saved);
      }
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
  return (
    <div className="connection-grid">
      <section className="card">
        <button onClick={() => select(null)} disabled={busy}>
          {t.add}
        </button>
        <ul className="profile-list">
          {state?.profiles.map((p) => (
            <li key={p.id}>
              <button
                aria-pressed={selected === p.id}
                onClick={() => select(p.id)}
                disabled={busy}
              >
                {p.name}
              </button>
              {state.active?.connectionId === p.id && (
                <span className="badge">{t.active}</span>
              )}
            </li>
          ))}
        </ul>
        {state?.pendingCredentialCleanup ? (
          <p role="status">{t.cleanup}</p>
        ) : null}
      </section>
      <section className="card stack">
        <h2>{profile ? profile.name : t.add}</h2>
        {dirty && <p role="status">{t.dirty}</p>}
        {error != null && (
          <p role="alert">{connectionError(error, language)}</p>
        )}
        <fieldset disabled={busy || !state} className="profile-form">
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
              {profile && <p>{t[profile.credentialState]}</p>}
              {profile &&
                (profile.credentialState === "stored" ||
                  profile.credentialState === "session_only") &&
                !invalidated.current && (
                  <label className="inline">
                    <input
                      type="checkbox"
                      checked={keep}
                      onChange={(e) => {
                        setKeep(e.target.checked);
                        setSecret("");
                        change();
                      }}
                    />
                    {t.keep}
                  </label>
                )}
              {!keep && (
                <>
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
              {state?.compatibilityProfiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}
                </option>
              ))}
            </select>
          </label>
          <p className="small">{t.compatibilityHint}</p>
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
              {t.save}
            </button>
            <button
              onClick={() => {
                if (!dirty || window.confirm(t.discard)) reset(profile);
              }}
            >
              {t.cancel}
            </button>
          </div>
        </fieldset>
        {profile && (
          <>
            <div className="actions">
              <button
                disabled={dirty || busy}
                onClick={() =>
                  void action(() => desktopApi.check(profile.id, false))
                }
              >
                {busy ? t.wait : t.check}
              </button>
              <button
                className="primary"
                disabled={dirty || busy}
                onClick={() =>
                  void action(() => desktopApi.check(profile.id, true))
                }
              >
                {t.activate}
              </button>
              <button
                disabled={busy}
                onClick={() => {
                  if (dirty && !window.confirm(t.discard)) return;
                  if (window.confirm(t.removeConfirm))
                    void action(() =>
                      desktopApi.remove(profile.id, profile.revision),
                    ).then((next) => {
                      if (next) {
                        setSelected(null);
                        reset();
                      }
                    });
                }}
              >
                {t.remove}
              </button>
            </div>
            <details open>
              <summary>{t.selected}</summary>
              <dl>
                <div>
                  <dt>{t.live}</dt>
                  <dd>{fact(report?.liveness, (v) => v.status)}</dd>
                </div>
                <div>
                  <dt>{t.readiness}</dt>
                  <dd>{fact(report?.readiness, (v) => v.status)}</dd>
                </div>
                <div>
                  <dt>{t.identity}</dt>
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
              <p className="small">{t.noPermissionClaim}</p>
            </details>
          </>
        )}
      </section>
    </div>
  );
}
