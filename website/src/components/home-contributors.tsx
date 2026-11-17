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

const VISIBLE = 5;
/** One avatar width minus the overlap, matching size-7 and -space-x-2. */
const STEP = 'calc(1.75rem - 0.5rem)';
const FLIP_MS = 560;
const HOLD_MS = 2400;

export function HomeContributors({ contributors, url, label }: {
  contributors: Contributor[];
  url: string;
  label: string;
}) {
  const [start, setStart] = useState(0);
  const [advancing, setAdvancing] = useState(false);
  const cycling = contributors.length > VISIBLE;

  useEffect(() => {
    if (!cycling) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let reset = 0;
    const tick = window.setInterval(() => {
      setAdvancing(true);
      reset = window.setTimeout(() => {
        setStart((value) => (value + 1) % contributors.length);
        setAdvancing(false);
      }, FLIP_MS);
    }, HOLD_MS);
    return () => {
      window.clearInterval(tick);
      window.clearTimeout(reset);
    };
  }, [contributors.length, cycling]);

  const shown = cycling ? VISIBLE + 1 : contributors.length;
  const avatars = Array.from({ length: shown }, (_, index) => contributors[(start + index) % contributors.length]);

  return <div className="flex -space-x-2">
    <span className={advancing ? 'pc-contrib-avatars is-advancing' : 'pc-contrib-avatars'}>
      <span
        className="pc-contrib-track flex -space-x-2"
        style={advancing ? { transform: `translateX(calc(-1 * ${STEP}))` } : undefined}
      >
        {avatars.map((contributor) => <img
          key={contributor.login}
          src={withBasePath(contributor.avatar)}
          alt={contributor.login}
          width={28}
          height={28}
          className="size-7 rounded-full border-2 border-[var(--home-hero-background)] object-cover"
        />)}
      </span>
    </span>
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
