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

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { messages, type Language } from "./messages";
import { Modal } from "./Modal";

export function useConfirmation(language: Language) {
  const [request, setRequest] = useState<{
    message: string;
    id: number;
  } | null>(null);
  const sequence = useRef(0);
  const descriptionId = useId();
  const pending = useRef<((answer: boolean) => void) | null>(null);
  const confirm = useCallback((text: string): Promise<boolean> => {
    if (pending.current) return Promise.resolve(false);
    return new Promise((resolve) => {
      pending.current = resolve;
      setRequest({ message: text, id: ++sequence.current });
    });
  }, []);
  useEffect(
    () => () => {
      pending.current?.(false);
      pending.current = null;
    },
    [],
  );
  function answer(value: boolean) {
    const resolve = pending.current;
    pending.current = null;
    setRequest(null);
    resolve?.(value);
  }
  const t = messages[language];
  const confirmation =
    request === null ? null : (
      <Modal
        key={request.id}
        label={t.confirmTitle}
        role="alertdialog"
        descriptionId={descriptionId}
        onClose={() => answer(false)}
      >
        <h2>{t.confirmTitle}</h2>
        <p id={descriptionId}>{request.message}</p>
        <div className="actions">
          <button onClick={() => answer(false)}>{t.cancelAction}</button>
          <button className="primary" onClick={() => answer(true)}>
            {t.confirmAction}
          </button>
        </div>
      </Modal>
    );
  return { confirm, confirmation };
}
