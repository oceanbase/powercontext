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

import { Check } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { HomeContent } from '@/lib/home-content';
import { agentBySlug } from './agents';
import { MockWindow, SessionWindow } from './session-windows';

type Hero = HomeContent['hero'];
type Actor = 'claude-code' | 'codex' | 'visitor';
type Point = { x: number; y: number };

const ACTORS: Array<{ id: Actor; label: keyof Hero['cursors'] }> = [
  { id: 'claude-code', label: 'claude_code' },
  { id: 'codex', label: 'codex' },
  { id: 'visitor', label: 'visitor' },
];
/** The demo's last step: both sessions resolved and the Handoff selected. */
const FINAL_STEP = 3;
/** Share of the demo that must be on screen before it plays. */
const PLAY_THRESHOLD = 0.3;
/** The arrow outline in a 20×22 box, tip at (2, 2). */
const ARROW = 'M2 2 L2 17.5 L6.8 13 L13.8 12.8 Z';
const EASE_OUT = 'cubic-bezier(0.22, 1, 0.36, 1)';
const EASE_IN_OUT = 'cubic-bezier(0.65, 0, 0.35, 1)';
/** How long the finished state holds before the demo starts over. */
const HOLD_MS = 4200;
/** The windows clear and the cursors step away over this long. */
const RESET_MS = 800;

/** The Dashboard window: one Handoff, its four steps and what travels with it. */
function Board({ board, prepared }: { board: Hero['board']; prepared: string }) {
  return (
    <div className="pc-demo-board" data-board="">
      <MockWindow title={board.title} trailing={<span className="pc-chip">{board.scope}</span>}>
        <div className="pc-board">
          <div className="pc-board-head" data-target="handoff">
            <p className="pc-board-kind">{board.kind}</p>
            <p className="pc-board-heading">{board.heading}</p>
            <p className="pc-ref pc-swap">
              <span className="pc-swap-draft">{board.ref}</span>
              <span className="pc-swap-final">{prepared}</span>
            </p>
          </div>
          <ol className="pc-loop">
            {board.steps.map((step, index) => (
              <li data-at={index} key={step.name}>
                <span aria-hidden="true" className="pc-loop-mark"><Check strokeWidth={2.5} /></span>
                <span className="pc-loop-name">{step.name}</span>
                <span className="pc-loop-note pc-swap">
                  <span className="pc-swap-draft">{board.waiting_label}</span>
                  <span className="pc-swap-final">{step.note}</span>
                </span>
              </li>
            ))}
          </ol>
          <div className="pc-board-carried">
            <p>{board.carried_label}</p>
            <dl>
              {board.carried.map((item) => (
                <div key={item.label}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </MockWindow>
    </div>
  );
}

/**
 * The hero illustration: two agent sessions and the Dashboard between them.
 * Named cursors do the work while the demo is on screen: Claude Code hands
 * off, the Dashboard records the prepared revision, Codex acknowledges, and
 * the visitor's cursor selects the Handoff. The cursors rest by their
 * windows, then everything returns to the draft and plays again. The server
 * renders the finished state; a script
 * right after the markup switches to the draft before paint unless motion is
 * reduced. A picture of the product: inert and labelled as an image.
 */
export function HeroDemo({ hero }: { hero: Hero }) {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const demo = root.current;
    if (!demo || demo.dataset.step === String(FINAL_STEP)) return;
    const cursors = new Map<Actor, HTMLElement>();
    for (const { id } of ACTORS) {
      const cursor = demo.querySelector<HTMLElement>(`.pc-cursor[data-agent="${id}"]`);
      if (cursor) cursors.set(id, cursor);
    }
    const positions = new Map<Actor, Point>();
    const running = new Set<Animation>();
    const timers = new Set<number>();
    let stopped = false;
    let rested = false;

    /** A point in the demo's own coordinates, whatever scale the stage applies. */
    const at = (selector: string, fx: number, fy: number, dx = 0, dy = 0): Point => {
      const base = demo.getBoundingClientRect();
      const scale = base.width / demo.offsetWidth || 1;
      const target = demo.querySelector(selector)?.getBoundingClientRect() ?? base;
      return {
        x: (target.left - base.left + target.width * fx) / scale + dx,
        y: (target.top - base.top + target.height * fy) / scale + dy,
      };
    };
    const place = (actor: Actor, point: Point) => {
      positions.set(actor, point);
      const cursor = cursors.get(actor);
      if (cursor) cursor.style.transform = `translate3d(${point.x}px, ${point.y}px, 0)`;
    };
    const wait = (ms: number) => new Promise<void>((resolve) => {
      const timer = window.setTimeout(() => {
        timers.delete(timer);
        resolve();
      }, ms);
      timers.add(timer);
    });
    const play = async (element: HTMLElement, frames: Keyframe[], options: KeyframeAnimationOptions) => {
      const animation = element.animate(frames, options);
      running.add(animation);
      try {
        await animation.finished;
      } catch {
        // Cancelled by `stop`.
      }
      running.delete(animation);
    };
    const move = async (actor: Actor, to: Point, duration: number, easing = EASE_OUT) => {
      const cursor = cursors.get(actor);
      const from = positions.get(actor);
      if (stopped || !cursor || !from) return;
      const frame = (point: Point) => ({ transform: `translate3d(${point.x}px, ${point.y}px, 0)` });
      // Committed before it animates, so a cancelled move never jumps back.
      place(actor, to);
      await play(cursor, [frame(from), frame(to)], { duration, easing });
    };
    const press = async (actor: Actor, selector: string) => {
      const arrow = cursors.get(actor)?.querySelector<HTMLElement>('.pc-cursor-arrow');
      const control = demo.querySelector<HTMLElement>(selector);
      if (stopped || !arrow) return;
      const dip = [{ transform: 'scale(1)' }, { transform: 'scale(0.86)' }, { transform: 'scale(1)' }];
      void (control && play(control, [{ transform: 'scale(1)' }, { transform: 'scale(0.96)' }, { transform: 'scale(1)' }], { duration: 260, easing: EASE_OUT }));
      await play(arrow, dip, { duration: 260, easing: EASE_OUT });
    };
    const step = (value: number) => {
      if (!stopped) demo.dataset.step = String(value);
    };
    const rest = () => {
      // Beside the windows, on the photograph, so no tag covers a line.
      place('claude-code', at('[data-session="ending"]', 0.3, 1, 0, 14));
      place('codex', at('[data-session="next"]', 0.55, 0, 0, -50));
      place('visitor', at('[data-board]', 0.5, 1, 0, 14));
    };

    /** Resolves once the demo is on screen; the loop waits here while it is not. */
    let visible = false;
    let onVisible: (() => void) | null = null;
    const whenVisible = () => (visible ? Promise.resolve() : new Promise<void>((resolve) => {
      onVisible = resolve;
    }));

    const start = () => {
      place('claude-code', at('[data-session="ending"]', 0, 1, -40, 40));
      place('codex', at('[data-session="next"]', 1, 0, 40, -60));
      place('visitor', at('[data-board]', 0.5, 1, 0, 90));
    };

    const cycle = async () => {
      const ending = '[data-session="ending"] [data-action]';
      const next = '[data-session="next"] [data-action]';
      const claude = (async () => {
        await move('claude-code', at(ending, 0.5, 0.6), 760);
        await press('claude-code', ending);
        step(1);
      })();
      const codex = (async () => {
        await wait(900);
        await move('codex', at(next, 0.5, 0.6), 820);
        await press('codex', next);
        step(2);
      })();
      const visitor = (async () => {
        await wait(1700);
        await move('visitor', at('[data-target="handoff"]', 0.42, 0.6), 900);
        await press('visitor', '[data-target="handoff"]');
        step(3);
      })();
      await Promise.all([claude, codex, visitor]);
      await wait(500);
      if (stopped) return;

      // Rest beside the windows while the finished state holds.
      rested = true;
      demo.dataset.rested = '';
      const before = new Map(positions);
      rest();
      const settled = new Map(positions);
      await Promise.all(ACTORS.map(({ id }) => glide(id, before.get(id), settled.get(id), 900)));
      await wait(HOLD_MS);
      if (stopped) return;

      // Back to the draft: the cursors step away while the windows clear.
      rested = false;
      delete demo.dataset.rested;
      const held = new Map(positions);
      start();
      const away = new Map(positions);
      const windows = demo.querySelector<HTMLElement>('.pc-demo-windows');
      const clear = windows
        ? play(windows, [{ opacity: 1 }, { opacity: 0.35, offset: 0.5 }, { opacity: 1 }], { duration: RESET_MS, easing: EASE_IN_OUT })
        : Promise.resolve();
      void wait(RESET_MS / 2).then(() => step(0));
      await Promise.all([clear, ...ACTORS.map(({ id }) => glide(id, held.get(id), away.get(id), RESET_MS))]);
      await wait(400);
    };

    /** Moves a cursor between two known points, whatever was committed in between. */
    const glide = (actor: Actor, from: Point | undefined, to: Point | undefined, duration: number) => {
      if (!from || !to) return Promise.resolve();
      positions.set(actor, from);
      return move(actor, to, duration, EASE_IN_OUT);
    };

    const run = async () => {
      start();
      demo.dataset.live = '';
      while (!stopped) {
        await whenVisible();
        if (stopped) return;
        await cycle();
      }
    };

    const observer = new IntersectionObserver(([entry]) => {
      visible = Boolean(entry?.isIntersecting);
      if (visible && onVisible) {
        onVisible();
        onVisible = null;
      }
    }, { threshold: PLAY_THRESHOLD });
    observer.observe(demo);
    void run();

    // The resting spots follow the windows when the layout changes.
    const resize = new ResizeObserver(() => {
      if (rested) rest();
    });
    resize.observe(demo);
    return () => {
      observer.disconnect();
      resize.disconnect();
      stopped = true;
      for (const timer of timers) window.clearTimeout(timer);
      for (const animation of running) animation.cancel();
    };
  }, []);

  return (
    <>
      <div
        aria-label={hero.demo_label}
        className="pc-demo"
        data-step={FINAL_STEP}
        ref={root}
        role="img"
        suppressHydrationWarning
      >
        <div className="pc-demo-windows" inert>
          <SessionWindow agent={agentBySlug('claude-code')} copy={hero.sessions} side="ending" />
          <Board board={hero.board} prepared={hero.sessions.ending.result} />
          <SessionWindow agent={agentBySlug('codex')} copy={hero.sessions} side="next" />
        </div>
        <div aria-hidden="true" className="pc-cursors">
          {ACTORS.map(({ id, label }) => (
            <div className="pc-cursor" data-agent={id} key={id}>
              <svg className="pc-cursor-arrow" fill="none" height="22" viewBox="0 0 20 22" width="20">
                <path d={ARROW} strokeLinejoin="round" strokeWidth="3.5" />
                <path d={ARROW} strokeLinejoin="round" strokeWidth="1.75" />
              </svg>
              <span className="pc-cursor-tag">{hero.cursors[label]}</span>
            </div>
          ))}
        </div>
      </div>
      <script dangerouslySetInnerHTML={{ __html: draftScript }} />
    </>
  );
}

/** Runs right after the demo's markup: shows the draft before paint unless motion is reduced. */
const draftScript = `(function(){try{var d=document.currentScript.previousElementSibling;
if(d&&!window.matchMedia("(prefers-reduced-motion: reduce)").matches&&"IntersectionObserver" in window&&d.animate)d.setAttribute("data-step","0")}catch(e){}})();`;
