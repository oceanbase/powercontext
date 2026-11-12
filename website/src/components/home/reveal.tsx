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

import { useEffect } from 'react';
import { REVEAL_ATTR } from './intro';

/** Share of an element that must be visible before it reveals. */
const THRESHOLD = 0.2;

/**
 * Reveals every `[data-reveal]` block on the home page once, the first time
 * it scrolls into view. The static HTML is fully visible; the pre-paint script
 * hides the blocks only when it can be sure this component will reveal them,
 * and never under reduced motion.
 */
export function RevealOnScroll() {
  useEffect(() => {
    const html = document.documentElement;
    if (!html.hasAttribute(REVEAL_ATTR)) return;
    html.setAttribute(REVEAL_ATTR, 'live');
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.setAttribute('data-revealed', '');
        observer.unobserve(entry.target);
      }
    }, { threshold: THRESHOLD });
    for (const element of document.querySelectorAll('.pc-home [data-reveal]')) observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return null;
}
