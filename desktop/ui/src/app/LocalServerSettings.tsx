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

import type { Ref } from "react";
import { messages, type Language } from "./messages";
import { PageFeedback } from "./PageFeedback";

export function LocalServerSettings({
  language,
  headingRef,
  onManageConnections,
}: {
  language: Language;
  headingRef?: Ref<HTMLHeadingElement>;
  onManageConnections: () => void;
}) {
  const t = messages[language];
  return (
    <section
      id="local-server"
      className="card stack"
      aria-labelledby="local-server-heading"
    >
      <h2 id="local-server-heading" ref={headingRef} tabIndex={-1}>
        {t.localServer}
      </h2>
      <p>{t.localServerIntro}</p>
      <PageFeedback
        compact
        headingLevel={3}
        kind="unavailable"
        title={t.localServerUnavailable}
      >
        <p>{t.localServerUnavailableHint}</p>
      </PageFeedback>
      <h3>{t.prepareServer}</h3>
      <ol>
        <li>{t.prepareServerDocs}</li>
        <li>{t.prepareServerAddress}</li>
        <li>{t.prepareServerConnect}</li>
      </ol>
      <div className="actions">
        <button onClick={onManageConnections}>{t.connectExisting}</button>
      </div>
      <p className="small">{t.localServerDiagnostics}</p>
    </section>
  );
}
