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
import { Ellipsis } from 'lucide-react';
import { withBasePath } from '@/lib/urls';

type Contributor = { login: string; avatar: string };

export function HomeContributors({ contributors, url, label }: {
  contributors: Contributor[];
  url: string;
  label: string;
}) {
  const [visible, setVisible] = useState(() => contributors.slice(0, 5));

  // Keep exported HTML deterministic, then choose a fresh sample after hydration.
  useEffect(() => {
    const shuffled = [...contributors];
    for (let index = 0; index < Math.min(5, shuffled.length); index++) {
      const other = index + Math.floor(Math.random() * (shuffled.length - index));
      [shuffled[index], shuffled[other]] = [shuffled[other], shuffled[index]];
    }
    setVisible(shuffled.slice(0, 5));
  }, [contributors]);

  return <div className="flex -space-x-2">
    {visible.map((contributor) => <img
      key={contributor.login}
      src={withBasePath(contributor.avatar)}
      alt={contributor.login}
      width={28}
      height={28}
      className="size-7 rounded-full border-2 border-[var(--home-hero-background)] object-cover"
    />)}
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      className="flex size-7 items-center justify-center rounded-full border-2 border-[var(--home-hero-background)] bg-fd-muted text-fd-muted-foreground hover:bg-fd-accent hover:text-fd-foreground focus-visible:outline-2 focus-visible:outline-fd-primary"
    >
      <Ellipsis aria-hidden="true" className="size-4" />
    </a>
  </div>;
}
