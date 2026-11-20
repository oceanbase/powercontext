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
import type { Language } from '@/lib/i18n';
import { buttonClasses } from '@astrafoundry/design/ui';

const copy = {
  en: { label: 'Copy page', done: 'Copied' },
  zh: { label: '复制页面', done: '已复制' },
} as const;

/**
 * Copies the page as Markdown, ready to paste into an agent. A Sunbeam
 * secondary button: the icon turns into a check once the text is copied.
 * Where the clipboard is unavailable, the Markdown opens instead.
 */
export function CopyPageButton({ markdownUrl, lang, className }: {
  markdownUrl: string;
  lang: Language;
  className?: string;
}) {
  const text = copy[lang];
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function copyPage() {
    try {
      // Safari keeps the user gesture only when the item is created synchronously.
      const markdown = fetch(markdownUrl).then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      });
      if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
        await navigator.clipboard.write([
          new ClipboardItem({ 'text/plain': markdown.then((value) => new Blob([value], { type: 'text/plain' })) }),
        ]);
      } else {
        await navigator.clipboard.writeText(await markdown);
      }
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      window.open(markdownUrl, '_blank', 'noopener');
    }
  }

  return (
    <button className={buttonClasses('secondary', 'sm', className)} onClick={copyPage} type="button">
      <span aria-hidden="true" className="pc-copy-page-icon" data-copied={copied ? '' : undefined}>
        <Copy strokeWidth={1.75} />
        <Check strokeWidth={2} />
      </span>
      {text.label}
      <span aria-live="polite" className="sr-only">{copied ? text.done : ''}</span>
    </button>
  );
}
