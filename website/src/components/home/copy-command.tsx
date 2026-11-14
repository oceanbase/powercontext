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

'use client';

import { Check, Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

/** How long the copied state holds before the button returns to copy. */
const COPIED_MS = 1800;

/**
 * A shell command in a well that scrolls sideways instead of wrapping, with a
 * copy button. Copying swaps the icon for a check and announces the result.
 */
export function CopyCommand({ command, copyLabel, copiedLabel }: {
  command: string;
  copyLabel: string;
  copiedLabel: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      // Without clipboard access the command stays selectable in the well.
      return;
    }
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_MS);
  };

  return (
    <div className="pc-command">
      <pre tabIndex={0}><code>{command}</code></pre>
      <button
        aria-label={copied ? copiedLabel : copyLabel}
        className="pc-copy"
        data-copied={copied ? '' : undefined}
        onClick={copy}
        title={copyLabel}
        type="button"
      >
        <Copy aria-hidden="true" className="pc-copy-idle" strokeWidth={1.75} />
        <Check aria-hidden="true" className="pc-copy-done" strokeWidth={2} />
      </button>
      <span aria-live="polite" className="sr-only">{copied ? copiedLabel : ''}</span>
    </div>
  );
}
