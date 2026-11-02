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
import { PageFeedback } from "./PageFeedback";

const documentation = {
  zh: "https://github.com/oceanbase/powercontext/blob/master/desktop/README.zh.md",
  en: "https://github.com/oceanbase/powercontext/blob/master/desktop/README.md",
};
const issues = "https://github.com/oceanbase/powercontext/issues";

export function Help({ language }: { language: Language }) {
  const t = messages[language];
  const [result, setResult] = useState<{
    target: "documents" | "issues";
    success: boolean;
  } | null>(null);
  const copySequence = useRef(0);
  useEffect(() => {
    copySequence.current++;
    setResult(null);
    return () => {
      copySequence.current++;
    };
  }, [language]);
  async function copy(target: "documents" | "issues") {
    const ticket = ++copySequence.current;
    const address = target === "documents" ? documentation[language] : issues;
    try {
      await navigator.clipboard.writeText(address);
      if (ticket === copySequence.current) setResult({ target, success: true });
    } catch {
      if (ticket === copySequence.current)
        setResult({ target, success: false });
    }
  }
  return (
    <section className="card stack help">
      <h2>{t.help}</h2>
      <p>{t.helpPrepare}</p>
      <ol>
        <li>{t.helpConnect}</li>
        <li>{t.helpScope}</li>
        <li>{t.helpMemory}</li>
      </ol>
      <p>{t.helpRecovery}</p>
      <p className="small">{t.helpAddressHint}</p>
      {(["documents", "issues"] as const).map((target) => (
        <div className="stack help-address" key={target}>
          <h3>{target === "documents" ? t.helpDocuments : t.helpIssues}</h3>
          {result?.target === target && (
            <PageFeedback
              compact
              kind={result.success ? "success" : "error"}
              title={result.success ? t.addressCopied : t.addressCopyFailed}
            />
          )}
          <textarea
            aria-label={target === "documents" ? t.helpDocuments : t.helpIssues}
            readOnly
            rows={2}
            value={target === "documents" ? documentation[language] : issues}
            onFocus={(event) => event.currentTarget.select()}
          />
          <div className="actions">
            <button onClick={() => void copy(target)}>
              {target === "documents" ? t.copyDocuments : t.copyIssues}
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}
