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

import Link from 'next/link';
import { SegmentThumb } from '@astrafoundry/design/ui';

/*
 * Sunbeam's Segmented control for a choice of pages: the same well, equal
 * segments and sliding thumb as `Segmented` from @astrafoundry/design, with
 * each segment a link and the current page marked by aria-current.
 */
export function SegmentedNav({ items, current, label }: {
  items: readonly { href: string; label: string }[];
  /** The href of the page being shown. */
  current: string;
  label: string;
}) {
  const chosen = items.findIndex((item) => item.href === current);
  return (
    <nav aria-label={label} className="material-well relative isolate inline-grid shrink-0 auto-cols-fr grid-flow-col gap-1 rounded-full p-1">
      <SegmentThumb className="-z-10" count={items.length} index={chosen} />
      {items.map((item, index) => (
        <Link
          aria-current={index === chosen ? 'page' : undefined}
          className={[
            'inline-flex h-8 select-none items-center justify-center gap-1.5 rounded-full border border-transparent px-3',
            'text-sm font-medium whitespace-nowrap transition-colors duration-(--duration-press) ease-out',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
            index === chosen ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
          ].join(' ')}
          href={item.href}
          key={item.href}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
