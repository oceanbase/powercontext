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
import { messages, type Language } from "./messages";
import { getFoundationInfo, desktopApi } from "../shared/ipc";
import type { FoundationInfo, DesktopState } from "../generated/ipc";
import logo from "../../../src-tauri/icons/brand.png";
import homeIcon from "../assets/overview.svg";
import connectionsIcon from "../assets/connections.svg";
import memoryIcon from "../assets/memory.svg";

import { MemoryWorkspace } from "./MemoryWorkspace";
import { Diagnostics } from "./Diagnostics";
import { Connections } from "./Connections";
import { Scopes } from "./Scopes";
import { connectionMessages, connectionError } from "./connection-messages";

type Page = "home" | "connections" | "memories" | "settings";
type Theme = "light" | "dark" | "system";
export function App() {
  const [language, setLanguage] = useState<Language>("zh");
  const [theme, setTheme] = useState<Theme>("system");
  const [page, setPage] = useState<Page>("home");
  const [menu, setMenu] = useState(false);
  const [host, setHost] = useState<FoundationInfo | null>(null);
  const [desktop, setDesktop] = useState<DesktopState | null>(null);
  const [nativeError, setNativeError] = useState<unknown>(null);
  const [dirty, setDirty] = useState(false);
  function receiveState(next: DesktopState) {
    setDesktop((current) =>
      !current || next.generation >= current.generation ? next : current,
    );
  }
  function confirmSwitch() {
    return !dirty || window.confirm(connectionMessages[language].discard);
  }
  const activeProfile = desktop?.profiles.find(
    (p) => p.id === desktop.active?.connectionId,
  );
  const heading = useRef<HTMLHeadingElement>(null);
  const t = messages[language];
  useEffect(() => {
    desktopApi.state().then(receiveState).catch(setNativeError);
    getFoundationInfo()
      .then(setHost)
      .catch(() => setHost(null));
  }, []);
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  function navigate(next: Page) {
    if (next === page || !confirmSwitch()) return;
    setDirty(false);
    setPage(next);
    setMenu(false);
    requestAnimationFrame(() => heading.current?.focus());
  }
  const connectButton = (
    <button className="primary" onClick={() => navigate("connections")}>
      {t.connect}
      <span aria-hidden="true"> →</span>
    </button>
  );
  return (
    <div className="app">
      <a className="skip" href="#main">
        {t.skip}
      </a>
      <header className="mobile-bar">
        <span>PowerContext</span>
        <button
          aria-expanded={menu}
          aria-controls="navigation"
          onClick={() => setMenu(!menu)}
        >
          {t.menu}
        </button>
      </header>
      <aside id="navigation" className={menu ? "sidebar open" : "sidebar"}>
        <div className="brand">
          <img src={logo} alt="" />
          <strong>PowerContext</strong>
        </div>
        <nav aria-label={t.menu}>
          {(["home", "connections", "memories"] as const).map((item, index) => (
            <button
              key={item}
              aria-current={page === item ? "page" : undefined}
              onClick={() => navigate(item)}
            >
              <img
                className="nav-symbol"
                alt=""
                src={[homeIcon, connectionsIcon, memoryIcon][index]}
              />
              {t[item]}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <p className="status">
            <span aria-hidden="true" className="dot" />
            {activeProfile?.name ?? t.disconnected}
          </p>
          <button
            aria-current={page === "settings" ? "page" : undefined}
            onClick={() => navigate("settings")}
          >
            {t.settings}
          </button>
        </div>
      </aside>
      <main id="main">
        <div className="page-heading">
          <div>
            <h1 tabIndex={-1} ref={heading}>
              {t[page]}
            </h1>
            <p>
              {page === "home"
                ? t.tagline
                : page === "connections"
                  ? t.connectionIntro
                  : page === "memories"
                    ? t.findHint
                    : t.foundation}
            </p>
          </div>
          <span className="badge">{t.foundation}</span>
        </div>
        {nativeError != null && (
          <p role="alert">{connectionError(nativeError, language)}</p>
        )}
        {desktop?.active && (
          <div className="active-context">
            <span>
              {activeProfile?.name} · {activeProfile?.endpoint}
            </span>
            <button
              onClick={() => {
                if (confirmSwitch())
                  void desktopApi
                    .disconnect()
                    .then(receiveState)
                    .catch(setNativeError);
              }}
            >
              {connectionMessages[language].disconnect}
            </button>
          </div>
        )}
        {(page === "home" || page === "memories") && (
          <Scopes
            key={`scopes-${desktop?.active?.generation ?? "none"}`}
            state={desktop}
            language={language}
            onState={receiveState}
            confirmSwitch={confirmSwitch}
          />
        )}
        {page === "home" && !desktop?.active && connectButton}
        {(page === "home" || page === "memories") && (
          <MemoryWorkspace
            key={`memory-${desktop?.active?.generation ?? "none"}`}
            state={desktop}
            language={language}
            home={page === "home"}
            onState={receiveState}
            onDirty={setDirty}
          />
        )}
        {page === "connections" && (
          <Connections
            state={desktop}
            language={language}
            onState={receiveState}
            onDirty={setDirty}
          />
        )}
        {page === "settings" && (
          <div className="stack settings">
            <section className="card">
              <div className="setting-row">
                <label htmlFor="language">{t.language}</label>
                <select
                  id="language"
                  value={language}
                  onChange={(e) => setLanguage(e.target.value as Language)}
                >
                  <option value="zh">简体中文</option>
                  <option value="en">English</option>
                </select>
              </div>
              <div className="setting-row">
                <label htmlFor="theme">{t.theme}</label>
                <select
                  id="theme"
                  value={theme}
                  onChange={(e) => setTheme(e.target.value as Theme)}
                >
                  <option value="system">{t.system}</option>
                  <option value="light">{t.light}</option>
                  <option value="dark">{t.dark}</option>
                </select>
              </div>
            </section>
            <Diagnostics language={language} />
            <section className="card">
              <h2>{t.version}</h2>
              <dl>
                <div>
                  <dt>{t.version}</dt>
                  <dd>{host?.version ?? "0.1.0"}</dd>
                </div>
                <div>
                  <dt>{t.native}</dt>
                  <dd role="status">{host ? t.nativeReady : t.unavailable}</dd>
                </div>
              </dl>
              <p>{t.boundary}</p>
            </section>
          </div>
        )}
        <footer>{t.privacy}</footer>
      </main>
    </div>
  );
}
