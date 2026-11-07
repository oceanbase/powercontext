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

import { useState, type Ref } from "react";
import { messages, type Language } from "./messages";
import { PageFeedback } from "./PageFeedback";
import { opsApi } from "../shared/ops";
import type { OpsComponents, OpsInspectionReply } from "../generated/ipc";

function statusValue(value: string | undefined, unknownLabel: string) {
  return value?.replaceAll("_", " ") ?? unknownLabel;
}

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
  const [inspection, setInspection] = useState<OpsInspectionReply | null>(null);
  const [inspectionPending, setInspectionPending] = useState(false);

  async function inspectLocalServer() {
    setInspectionPending(true);
    try {
      setInspection(await opsApi.inspect());
    } catch {
      setInspection({ outcome: "unknown", bridgeError: "process_failure" });
    } finally {
      setInspectionPending(false);
    }
  }

  const components: OpsComponents | undefined = inspection?.components;
  const facts = [
    [t.serviceSupport, components?.support],
    [t.serviceRegistration, components?.registration],
    [t.serviceDefinition, components?.definition],
    [t.serviceOwnership, components?.managerOwnership],
    [t.serviceManager, components?.manager],
    [t.serviceLiveness, components?.liveness],
    [t.serviceReadiness, components?.readiness],
    [t.serviceMaintenance, components?.maintenance],
  ] as const;
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
        <button
          onClick={() => void inspectLocalServer()}
          disabled={inspectionPending}
        >
          {inspectionPending ? t.localServerInspecting : t.localServerInspect}
        </button>
      </div>
      {inspection && (
        <PageFeedback
          compact
          headingLevel={3}
          kind={
            inspection.bridgeError || inspection.error ? "warning" : "success"
          }
          title={
            inspection.bridgeError === "unavailable"
              ? t.localServerInspectionUnavailable
              : inspection.bridgeError || inspection.error
                ? t.localServerInspectionFailed
                : t.localServerInspectionTitle
          }
        >
          <p>
            {inspection.bridgeError === "unavailable" || inspection.bridgeError
              ? t.localServerInspectionUnavailable
              : t.localServerInspectionComplete}
          </p>
          {components && (
            <dl>
              {facts.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{statusValue(value, t.unknown)}</dd>
                </div>
              ))}
            </dl>
          )}
        </PageFeedback>
      )}
      <p className="small">{t.localServerDiagnostics}</p>
    </section>
  );
}
