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

const STAGE = '.pc-home .pc-stage';

/**
 * As a homepage photograph card scrolls upward, it grows from its framed
 * size until it covers the viewport, then travels off with the page.
 * The resting layout is unchanged at scroll 0. Reduced motion keeps the
 * framed cards.
 */
export function StageFill() {
  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stages = () => Array.from(document.querySelectorAll<HTMLElement>(STAGE));

    const clear = (stage: HTMLElement) => {
      stage.style.transform = '';
      stage.style.borderRadius = '';
      stage.closest('section')?.removeAttribute('data-filling');
    };

    const update = () => {
      if (reduce.matches) {
        stages().forEach(clear);
        return;
      }

      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const scrollY = window.scrollY;
      const measured = stages().map((stage) => {
        stage.style.transform = 'none';
        return stage;
      }).map((stage) => ({ stage, rect: stage.getBoundingClientRect() }));

      for (const { stage, rect } of measured) {
        const docTop = rect.top + scrollY;
        const start = Math.max(0, docTop - viewportHeight * 0.82);
        const expandDistance = viewportHeight * 0.5;
        const hold = viewportHeight * 0.12;
        const releaseDistance = viewportHeight * 0.45;
        const traveled = scrollY - start;
        const progress = clamp(traveled / expandDistance);
        const release = clamp((traveled - expandDistance - hold) / releaseDistance);
        const amount = progress * (1 - release);

        if (amount <= 0 || rect.width === 0 || rect.height === 0) {
          clear(stage);
          continue;
        }

        const cover = Math.max(viewportWidth / rect.width, viewportHeight / rect.height);
        const scale = 1 + (cover - 1) * amount;
        const dx = (viewportWidth / 2 - (rect.left + rect.width / 2)) * amount;
        const dy = (viewportHeight / 2 - (rect.top + rect.height / 2)) * amount;
        stage.style.transformOrigin = 'center center';
        stage.style.transform = `translate3d(${dx}px, ${dy}px, 0) scale(${scale})`;
        stage.style.borderRadius = `calc(var(--pc-radius-stage) * ${1 - amount})`;
        stage.closest('section')?.setAttribute('data-filling', amount.toFixed(3));
      }
    };

    let frame = 0;
    const request = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        update();
      });
    };

    update();
    window.addEventListener('scroll', request, { passive: true });
    window.addEventListener('resize', request);
    reduce.addEventListener('change', request);
    return () => {
      window.removeEventListener('scroll', request);
      window.removeEventListener('resize', request);
      reduce.removeEventListener('change', request);
      if (frame) window.cancelAnimationFrame(frame);
      stages().forEach(clear);
    };
  }, []);

  return null;
}

function clamp(value: number) {
  return Math.min(1, Math.max(0, value));
}
