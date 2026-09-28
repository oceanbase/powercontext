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

import { useEffect, useId, useRef, useState } from 'react';
import { loadGitHubStars } from '@/lib/github-stars';
import type { Language } from '@/lib/i18n';

const shownPrompts = new Set<string>();

export function GitHubStars({ lang, url, initialCount = null }: {
  lang: Language;
  url: string;
  initialCount?: number | null;
}) {
  const [count, setCount] = useState<number | string | null>(initialCount);
  const [promptOpen, setPromptOpen] = useState(false);
  const link = useRef<HTMLAnchorElement>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const promptId = useId();
  const locale = lang === 'zh' ? 'zh-CN' : 'en-US';
  const prompt = lang === 'zh' ? '喜欢这个项目？点个 Star' : 'Star us on GitHub';
  const newTab = lang === 'zh' ? '在新标签页打开' : 'opens in a new tab';
  const countLabel = typeof count === 'number' ? count.toLocaleString(locale) : count;
  const displayCount = typeof count === 'number'
    ? new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(count) : count;

  function clearPromptTimers() {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }

  function dismissPrompt() {
    clearPromptTimers();
    setPromptOpen(false);
  }

  function showPrompt() {
    if (!window.matchMedia('(min-width: 1024px)').matches) return;
    clearPromptTimers();
    shownPrompts.add(`powercontext:github-click-guide:${url}`);
    setPromptOpen(true);
  }

  useEffect(() => {
    if (!promptOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        clearPromptTimers();
        setPromptOpen(false);
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [promptOpen]);

  useEffect(() => {
    let active = true;
    setCount(initialCount);
    void loadGitHubStars(url).then((value) => {
      if (active && value !== null) setCount(value);
    });
    return () => { active = false; };
  }, [url, initialCount]);

  useEffect(() => {
    const key = `powercontext:github-click-guide:${url}`;
    const media = window.matchMedia(
      '(min-width: 1024px) and (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)',
    );
    function schedulePrompt() {
      clearPromptTimers();
      setPromptOpen(false);
      if (!media.matches) return;
      timers.current.push(setTimeout(() => {
        // Hidden responsive copies must not consume the one-time prompt.
        if (!link.current?.getClientRects().length || document.visibilityState !== 'visible') return;
        if (shownPrompts.has(key)) return;
        try {
          if (sessionStorage.getItem(key)) return;
          sessionStorage.setItem(key, '1');
        } catch {
          // The per-page memory fallback also avoids repeated prompts without storage.
        }
        shownPrompts.add(key);
        setPromptOpen(true);
        timers.current.push(setTimeout(() => setPromptOpen(false), 4500));
      }, 700));
    }
    schedulePrompt();
    media.addEventListener('change', schedulePrompt);
    return () => {
      clearPromptTimers();
      media.removeEventListener('change', schedulePrompt);
    };
  }, [url]);

  return (
    <span
      className="pc-github"
      data-prompt-open={promptOpen}
      onPointerEnter={(event) => { if (event.pointerType === 'mouse') showPrompt(); }}
      onPointerLeave={() => { if (document.activeElement !== link.current) dismissPrompt(); }}
    >
      <a
        ref={link}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="pc-github-link"
        aria-label={`GitHub${countLabel === null ? '' : ` · ${countLabel} stars`} · ${newTab}`}
        aria-describedby={promptOpen ? promptId : undefined}
        onFocus={showPrompt}
        onBlur={dismissPrompt}
        onClick={dismissPrompt}
      >
        <svg className="pc-github-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M12 .75a11.25 11.25 0 0 0-3.558 21.923c.563.104.768-.244.768-.543 0-.267-.01-.974-.015-1.912-3.13.68-3.791-1.508-3.791-1.508-.512-1.3-1.25-1.646-1.25-1.646-1.022-.7.078-.686.078-.686 1.13.08 1.724 1.16 1.724 1.16 1.005 1.724 2.638 1.226 3.28.938.102-.73.393-1.226.714-1.508-2.499-.285-5.126-1.25-5.126-5.564 0-1.23.44-2.232 1.16-3.02-.117-.285-.503-1.43.11-2.979 0 0 .944-.302 3.093 1.154A10.79 10.79 0 0 1 12 6.181c.957.004 1.922.13 2.82.378 2.15-1.456 3.092-1.154 3.092-1.154.614 1.55.228 2.694.112 2.979.721.788 1.158 1.79 1.158 3.02 0 4.325-2.631 5.276-5.138 5.555.404.349.766 1.04.766 2.096 0 1.514-.014 2.736-.014 3.108 0 .3.203.652.774.542A11.252 11.252 0 0 0 12 .75Z" />
        </svg>
        <span className="pc-github-count" aria-hidden="true">
          {displayCount}
        </span>
      </a>
      <span className="pc-github-click-guide" aria-hidden="true">
        <span className="pc-github-click-ring" />
        <svg className="pc-github-cursor" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 13V4a2 2 0 0 1 4 0v6.25a1.75 1.75 0 0 1 3.5 0v1a1.75 1.75 0 0 1 3.5 0v1a1.5 1.5 0 0 1 3 0V16c0 4-2.5 6-6 6h-2c-2 0-3.4-.8-4.7-2.3l-4.7-5.6a1.75 1.75 0 0 1 2.6-2.3L8 13Z" />
          <path d="M12 10.25V14m3.5-2.75V15M19 12.25V16" fill="none" />
        </svg>
      </span>
      <span id={promptId} role="tooltip" className="pc-github-prompt" aria-hidden={!promptOpen}>
        {prompt}
      </span>
    </span>
  );
}
