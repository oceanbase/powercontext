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

import { withBasePath } from '@/lib/urls';

/** Licensed photographs; sources and licence in website/assets/backdrops/README.md. */
const backdrops = {
  iris: {
    file: 'iris-motion',
    author: 'Eugene Golovesov',
    url: 'https://unsplash.com/photos/blue-irises-with-motion-blur-cJIUBpFEDBw',
    position: '60% 50%',
  },
  lupine: {
    file: 'lupine-motion',
    author: 'Eugene Golovesov',
    url: 'https://unsplash.com/photos/blurred-blue-and-purple-flowers-with-green-foliage-PFDRQ6C8SV0',
    position: '50% 40%',
  },
} as const;

export type BackdropId = keyof typeof backdrops;

const WIDTHS = [960, 1600, 2400] as const;

/**
 * A photograph used as ground. `hero` covers the stage; `tour` is the one
 * frame every feature card is a window onto. Decorative: no alt text.
 */
export function Backdrop({ id, priority = false }: { id: BackdropId; priority?: boolean }) {
  const { file, position } = backdrops[id];
  const source = (width: number) => withBasePath(`/backdrops/${file}-${width}.webp`);
  return (
    <img
      alt=""
      aria-hidden="true"
      className="pc-ground"
      decoding="async"
      draggable={false}
      fetchPriority={priority ? 'high' : 'auto'}
      height={1000}
      loading={priority ? 'eager' : 'lazy'}
      sizes={priority ? '100vw' : '(min-width: 1400px) 1368px, 100vw'}
      src={source(1600)}
      srcSet={WIDTHS.map((width) => `${source(width)} ${width}w`).join(', ')}
      style={{ objectPosition: position }}
      width={1600}
    />
  );
}
