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

import { useEffect, useState } from 'react';
import { Star } from 'lucide-react';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { loadGitHubStars } from '@/lib/github-stars';
import type { Language } from '@/lib/i18n';

export function GitHubStarButton({ url, label, lang, initialCount }: {
  url: string;
  label: string;
  lang: Language;
  initialCount: number | null;
}) {
  const [count, setCount] = useState<number | string | null>(initialCount);

  useEffect(() => {
    let active = true;
    void loadGitHubStars(url).then((value) => {
      if (active && value !== null) setCount(value);
    });
    return () => { active = false; };
  }, [url]);

  const displayCount = typeof count === 'number'
    ? count.toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US') : count;

  return <a
    href={url}
    target="_blank"
    rel="noopener noreferrer"
    aria-label={`${label}${displayCount === null ? '' : ` · ${displayCount} stars`}`}
    className={buttonVariants({ variant: 'outline', className: 'gap-2 whitespace-nowrap' })}
  >
    <Star aria-hidden="true" className="size-4" />
    <span className="hidden min-[400px]:inline">{label}</span>
    <span className="min-[400px]:hidden">Star</span>
    {displayCount !== null && <span className="hidden text-xs font-normal text-fd-muted-foreground tabular-nums sm:inline">{displayCount}</span>}
  </a>;
}
