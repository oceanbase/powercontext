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

import type { ReactNode } from "react";
import { messages, type Language } from "./messages";
import { PageFeedback } from "./PageFeedback";

export type ObservationTab = "runtime" | "inventory" | "session";

export function Observation({
  language,
  tab,
  onTabChange,
  panels = {},
}: {
  language: Language;
  tab: ObservationTab;
  onTabChange: (tab: ObservationTab) => void;
  panels?: Partial<Record<ObservationTab, ReactNode>>;
}) {
  const t = messages[language];
  const tabs: ObservationTab[] = ["runtime", "inventory", "session"];
  return (
    <div className="stack">
      <div
        className="observation-tabs"
        role="tablist"
        aria-label={t.observation}
      >
        {tabs.map((item, index) => (
          <button
            key={item}
            id={`tab-${item}`}
            role="tab"
            aria-selected={tab === item}
            aria-controls={`panel-${item}`}
            tabIndex={tab === item ? 0 : -1}
            onClick={() => onTabChange(item)}
            onKeyDown={(event) => {
              let next: ObservationTab;
              if (event.key === "ArrowRight")
                next = tabs[(index + 1) % tabs.length];
              else if (event.key === "ArrowLeft")
                next = tabs[(index + tabs.length - 1) % tabs.length];
              else if (event.key === "Home") next = tabs[0];
              else if (event.key === "End") next = tabs[tabs.length - 1];
              else return;
              event.preventDefault();
              onTabChange(next);
              document.getElementById(`tab-${next}`)?.focus();
            }}
          >
            {t[item]}
          </button>
        ))}
      </div>
      {tabs.map((item) => (
        <div
          key={item}
          id={`panel-${item}`}
          role="tabpanel"
          aria-labelledby={`tab-${item}`}
          hidden={tab !== item}
          tabIndex={0}
        >
          {tab === item &&
            (panels[item] ?? (
              <PageFeedback kind="unavailable" title={t.observationUnavailable}>
                <p>{t[`${item}Description`]}</p>
              </PageFeedback>
            ))}
        </div>
      ))}
    </div>
  );
}
