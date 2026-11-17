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

import { useEffect, useRef } from 'react';

const DURATION_MS = 1600;
const easeOut = (value: number) => 1 - (1 - value) ** 4;

/**
 * A figure such as `90.78%` that counts up from zero the first time it is on
 * screen. The server renders the final text, which is also what assistive
 * technology reads and what reduced motion keeps; the digits are tabular, so
 * the line never changes width while it counts.
 */
export function CountUp({ value }: { value: string }) {
  const node = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const element = node.current;
    const match = /^(\D*)(\d+(?:\.(\d+))?)(.*)$/.exec(value);
    if (!element || !match) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const [, prefix, digits, fraction = '', suffix] = match;
    const target = Number(digits);
    const render = (amount: number) => {
      element.textContent = `${prefix}${amount.toFixed(fraction.length)}${suffix}`;
    };
    let frame = 0;
    render(0);
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting) return;
      observer.disconnect();
      const started = performance.now();
      const tick = (now: number) => {
        const progress = Math.min(1, (now - started) / DURATION_MS);
        render(target * easeOut(progress));
        if (progress < 1) frame = window.requestAnimationFrame(tick);
        else element.textContent = value;
      };
      frame = window.requestAnimationFrame(tick);
    }, { threshold: 0.6 });
    observer.observe(element);
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
      element.textContent = value;
    };
  }, [value]);

  return (
    <>
      <span aria-hidden="true" ref={node}>{value}</span>
      <span className="sr-only">{value}</span>
    </>
  );
}
