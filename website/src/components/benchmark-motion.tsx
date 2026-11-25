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

import { animate, useInView, useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * Marks its content as in view the first time a third of it is visible, so
 * the bars inside grow from their baseline then. Until then the stylesheet
 * holds them at zero; without scripts a noscript style shows them in full.
 */
export function RevealOnView({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.3 });
  return (
    <div className={className} data-inview={inView ? '' : undefined} data-reveal="" ref={ref}>
      {children}
    </div>
  );
}

const NUMBER = /\d[\d,]*(?:\.\d+)?/;

/**
 * A figure that counts up from zero the first time it scrolls into view,
 * keeping its prefix, suffix, decimals and thousands separators. The server
 * renders the final text, so the reading never depends on the animation.
 */
export function CountUp({ value, delay = 0 }: { value: string; delay?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const reduce = useReducedMotion();
  const [text, setText] = useState(value);
  const [pending, setPending] = useState(true);
  const match = NUMBER.exec(value);

  useEffect(() => {
    if (!match || reduce) {
      setPending(false);
      return;
    }
    const raw = match[0];
    const target = Number.parseFloat(raw.replaceAll(',', ''));
    const decimals = raw.split('.')[1]?.length ?? 0;
    const grouped = raw.includes(',');
    const format = (current: number) => {
      const fixed = current.toFixed(decimals);
      const digits = grouped ? Number(fixed).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : fixed;
      return value.slice(0, match.index) + digits + value.slice(match.index + raw.length);
    };
    if (!inView) {
      setText(format(0));
      setPending(false);
      return;
    }
    const controls = animate(0, target, {
      delay,
      duration: 1.1,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (current) => setText(format(current)),
      onComplete: () => setText(value),
    });
    return () => controls.stop();
    // The parsed match follows `value`; re-running on it would restart the count.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, reduce, value, delay]);

  return (
    <span className="tabular-nums" data-count={pending ? 'pending' : undefined} ref={ref}>
      {text}
    </span>
  );
}
