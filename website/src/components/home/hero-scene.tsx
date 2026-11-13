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

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { HomeContent } from '@/lib/home-content';
import { agentBySlug } from './agents';
import { playOpening, type Actor, type OpeningCast, type OpeningParts } from './choreography';
import { HandoffFigure, type HandoffFigureHandle } from './handoff-figure';
import { INTRO_ATTR, PART_ATTR, PLAYED_KEY } from './intro';
import { SessionWindow } from './session-windows';

type Point = { x: number; y: number };
type Hero = Omit<HomeContent['hero'], 'label'>;

const ACTORS: Array<{ id: Actor; label: keyof Hero['cursors'] }> = [
  { id: 'visitor', label: 'visitor' },
  { id: 'claude-code', label: 'claude_code' },
  { id: 'codex', label: 'codex' },
  { id: 'opencode', label: 'opencode' },
];
/** The visitor is handed a cursor only with a mouse. */
const FINE_POINTER = '(hover: hover) and (pointer: fine)';
/** The arrow outline in a 20×22 box, tip at (2, 2). */
const ARROW = 'M2 2 L2 17.5 L6.8 13 L13.8 12.8 Z';

function part(name: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[${PART_ATTR}="${name}"]`);
  if (!element) throw new Error(`Home hero: missing part "${name}"`);
  return element;
}

function within<T extends HTMLElement>(root: HTMLElement, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) throw new Error(`Home hero: missing "${selector}"`);
  return element;
}

/**
 * The hero composition: the handoff figure with the ending and the next
 * session on its corners. On a first visit in this tab, agent cursors finish
 * the card around it (see choreography.ts); otherwise the figure plays its
 * turn once on its own. The cursor layer is decorative: hidden from assistive
 * technology, never interactive, skipped by any key and never played under
 * reduced motion (the pre-paint script in intro.ts decides).
 */
export function HeroScene({ hero }: { hero: Hero }) {
  const figure = useRef<HandoffFigureHandle>(null);
  const layer = useRef<HTMLDivElement>(null);
  const pointer = useRef<Point | null>(null);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    const html = document.documentElement;
    if (html.getAttribute(INTRO_ATTR) === 'pending') {
      // Claim the draft before the pre-paint script's timeout can drop it.
      html.setAttribute(INTRO_ATTR, 'running');
      setPlaying(true);
    } else {
      void figure.current?.play();
    }
  }, []);

  useEffect(() => {
    if (!playing || !layer.current) return;
    const html = document.documentElement;
    html.setAttribute(INTRO_ATTR, 'running');
    const root = layer.current;
    const stage = part('card');
    const parts: OpeningParts = {
      card: stage,
      ground: within(stage, '[data-backdrop]'),
      title: part('title'),
      lead: part('lead'),
      ending: within(stage, '[data-session="ending"]'),
      next: within(stage, '[data-session="next"]'),
    };
    const cast: OpeningCast = {
      cursors: Object.fromEntries(
        ACTORS.map(({ id }) => [id, within(root, `.pc-cursor[data-agent="${id}"]`)]),
      ) as Record<Actor, HTMLElement>,
      boxes: {
        card: within(root, '[data-box="card"]'),
        title: within(root, '[data-box="title"]'),
        marquee: within(root, '[data-box="marquee"]'),
      },
      photo: within(root, '.pc-intro-photo'),
    };
    const image = parts.ground.querySelector('img');
    if (image?.currentSrc || image?.src) cast.photo.style.backgroundImage = `url("${image.currentSrc || image.src}")`;

    const fine = window.matchMedia(FINE_POINTER).matches;
    const onPointer = (event: PointerEvent) => {
      if (event.pointerType === 'mouse') pointer.current = { x: event.clientX, y: event.clientY };
    };
    window.addEventListener('pointermove', onPointer, { passive: true });

    const run = playOpening({
      cast,
      parts,
      playFigure: () => figure.current?.play() ?? Promise.resolve(),
      pointer: () => (fine ? pointer.current : null),
      onDone: () => {
        // Hide the cursors before their animations are cleared, so none jumps back.
        root.style.visibility = 'hidden';
        html.removeAttribute(INTRO_ATTR);
        try {
          sessionStorage.setItem(PLAYED_KEY, '1');
        } catch {
          // Blocked storage only means the opening may play again in this tab.
        }
        setPlaying(false);
      },
    });

    // The layer is fixed; keep the cursors on the hero if the page scrolls.
    const scrollStart = window.scrollY;
    const onScroll = () => {
      root.style.transform = `translateY(${scrollStart - window.scrollY}px)`;
    };
    const onKey = () => run.finish();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('keydown', onKey, { once: true });
    return () => {
      run.stop();
      window.removeEventListener('pointermove', onPointer);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('keydown', onKey);
      // Unmounted mid-opening: back to the draft, so a remount replays it.
      if (html.getAttribute(INTRO_ATTR) === 'running') {
        for (const element of document.querySelectorAll('[data-session][data-done]')) element.removeAttribute('data-done');
        html.setAttribute(INTRO_ATTR, 'pending');
      }
    };
  }, [playing]);

  return (
    <>
      <div className="pc-hero-composition">
        <div className="pc-figure-slot">
          <HandoffFigure labels={hero.figure} ref={figure} />
        </div>
        <div aria-hidden="true" inert>
          <SessionWindow agent={agentBySlug('claude-code')} copy={hero.sessions} side="ending" />
          <SessionWindow agent={agentBySlug('codex')} copy={hero.sessions} side="next" />
        </div>
      </div>
      {/* Portalled to <body>: the stage's clip-path would otherwise clip the cursors. */}
      {playing ? createPortal(
        <div aria-hidden="true" className="pc-cursors">
          <div ref={layer}>
            <div className="pc-intro-box" data-agent="visitor" data-box="card"><i /><i /><i /><i /></div>
            <div className="pc-intro-box" data-agent="codex" data-box="title"><i /><i /><i /><i /></div>
            <div className="pc-intro-box" data-agent="opencode" data-box="marquee" />
            {ACTORS.map(({ id, label }) => (
              <div className="pc-cursor" data-agent={id} key={id}>
                {id === 'opencode' ? <div className="pc-intro-photo" style={{ transform: 'translate(14px, 22px)' }} /> : null}
                <svg className="pc-cursor-arrow" fill="none" height="22" viewBox="0 0 20 22" width="20">
                  <path d={ARROW} strokeLinejoin="round" strokeWidth="3.5" />
                  <path d={ARROW} strokeLinejoin="round" strokeWidth="1.75" />
                </svg>
                <span className="pc-cursor-tag">{hero.cursors[label]}</span>
              </div>
            ))}
          </div>
        </div>,
        document.body,
      ) : null}
    </>
  );
}
