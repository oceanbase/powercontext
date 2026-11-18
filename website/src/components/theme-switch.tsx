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

import type { ThemeSwitchProps } from 'fumadocs-ui/layouts/shared/slots/theme-switch';
import { useTheme } from 'fumadocs-ui/provider/base';
import { Moon, Sun } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import type { Language } from '@/lib/i18n';

const labels = {
  en: { toDark: 'Switch to dark theme', toLight: 'Switch to light theme' },
  zh: { toDark: '切换到深色主题', toLight: '切换到浅色主题' },
} as const;

/**
 * The landing-page theme control from Arion: one circular icon button.
 * Clicking it reveals the next theme in a circle that opens from the
 * top-right, the same view transition Arion's top bar uses.
 */
export function ThemeSwitch({ className }: ThemeSwitchProps) {
  const pathname = usePathname();
  const language: Language = pathname.split('/').filter(Boolean)[0] === 'zh' ? 'zh' : 'en';
  const copy = labels[language];
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [iconKey, setIconKey] = useState(0);
  const [swapIcon, setSwapIcon] = useState(false);

  useEffect(() => setMounted(true), []);

  const isDark = mounted && resolvedTheme === 'dark';

  function toggle() {
    const next = isDark ? 'light' : 'dark';
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const canReveal = !reduce && typeof document.startViewTransition === 'function';
    const apply = () => {
      flushSync(() => setTheme(next));
      setIconKey((value) => value + 1);
      setSwapIcon(true);
    };

    if (!canReveal) {
      apply();
      return;
    }

    const root = document.documentElement;
    root.style.setProperty('--pc-vt-origin', '100% 0%');
    root.dataset.pcVt = 'circle';
    const transition = document.startViewTransition(apply);
    transition.finished.finally(() => {
      delete root.dataset.pcVt;
    });
  }

  return (
    <button
      type="button"
      aria-label={isDark ? copy.toLight : copy.toDark}
      className={['pc-theme-toggle', className].filter(Boolean).join(' ')}
      onClick={toggle}
    >
      <span className={swapIcon ? 'pc-theme-toggle-icon is-swapping' : 'pc-theme-toggle-icon'} key={iconKey}>
        {mounted ? (isDark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />) : null}
      </span>
    </button>
  );
}
