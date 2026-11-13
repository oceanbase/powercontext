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

import { useEffect, useLayoutEffect, useRef, type ReactNode } from 'react';
import { Backdrop } from './backdrop';

/** Scroll distance, in viewport heights, the full-bleed stage holds before the page moves on. */
const HOLD = 0.5;
/** Where the demo's top edge sits in the opening card, as a share of the card's height. */
const OPENING_TOP = 0.1;
/** Room kept clear above the demo at rest, and below it, where a cursor rests. */
const REST_MARGIN = 24;
const REST_FOOT = 64;
/** From this stage width the demo's windows sit side by side and may grow to fill a large viewport. */
const WIDE = 900;
const MAX_SCALE = 1.3;

const clamp = (value: number) => Math.min(1, Math.max(0, value));
const easeOut = (value: number) => 1 - (1 - value) ** 3;

const useIsomorphicLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/**
 * The hero's stage. The first screen shows it as a rounded card under the
 * copy: the photograph with the top of the demo on it, cut off by the bottom
 * of the viewport. As the reader scrolls, the card's top rises with the page
 * while its bottom stays put, so it grows; its gutter and corners shrink to
 * nothing as it reaches the top, and the demo travels to the centre. The
 * stage then holds for a moment before the page moves on. Under reduced
 * motion it shows the final frame and does not move.
 */
export function HeroStage({ children }: { children: ReactNode }) {
  const track = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const demo = useRef<HTMLDivElement>(null);

  useIsomorphicLayoutEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    let layout: { trackTop: number; width: number; height: number; demoWidth: number; demoHeight: number; navHeight: number } | null = null;
    let frame = 0;

    const paint = () => {
      frame = 0;
      if (!layout || !card.current || !demo.current) return;
      const { trackTop, width, height, demoWidth, demoHeight, navHeight } = layout;
      const scroll = window.scrollY;
      const still = reduce.matches;
      const grow = still ? 1 : clamp(trackTop > 0 ? scroll / trackTop : 1);
      const gutter = (width < 640 ? 8 : 12) * (1 - grow);
      const radius = (width < 1024 ? 24 : 40) * (1 - grow);
      const below = still ? 0 : Math.max(0, trackTop - scroll) + gutter;

      // The demo scales to fit the viewport, and rests centred below the nav.
      const room = height - navHeight - REST_MARGIN - REST_FOOT;
      const side = width < 640 ? 16 : 48;
      const scale = Math.min(width >= WIDE ? MAX_SCALE : 1, room / demoHeight, (width - side * 2) / demoWidth);
      const restTop = navHeight + REST_MARGIN + Math.max(0, (room - demoHeight * scale) / 2);
      const openingCard = Math.max(0, height - trackTop - gutter);
      const openingTop = Math.max(28, OPENING_TOP * openingCard);
      const top = openingTop + (restTop - openingTop) * easeOut(grow);

      card.current.style.clipPath = `inset(0px ${gutter}px ${below}px round ${radius}px)`;
      demo.current.style.transform = `translate3d(-50%, ${top}px, 0) scale(${scale})`;
    };

    const request = () => {
      if (!frame) frame = window.requestAnimationFrame(paint);
    };

    const measure = () => {
      if (!track.current || !stage.current || !demo.current) return;
      const nav = document.getElementById('nd-nav');
      layout = {
        trackTop: track.current.getBoundingClientRect().top + window.scrollY,
        width: stage.current.clientWidth,
        height: stage.current.clientHeight,
        demoWidth: demo.current.offsetWidth,
        demoHeight: demo.current.offsetHeight,
        navHeight: nav ? nav.getBoundingClientRect().height : 0,
      };
      paint();
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    if (stage.current) observer.observe(stage.current);
    if (demo.current) observer.observe(demo.current);
    void document.fonts.ready.then(measure);
    window.addEventListener('scroll', request, { passive: true });
    reduce.addEventListener('change', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', request);
      reduce.removeEventListener('change', measure);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div className="pc-stage-track" ref={track} style={{ ['--pc-stage-hold' as string]: HOLD }}>
      <div className="pc-stage" ref={stage}>
        <div className="pc-stage-card" ref={card}>
          <Backdrop id="iris" priority />
          <div className="pc-stage-demo" ref={demo}>{children}</div>
        </div>
      </div>
    </div>
  );
}
