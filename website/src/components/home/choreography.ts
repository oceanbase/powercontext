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

/** The cursors of the opening, by who they stand for. */
export type Actor = 'visitor' | 'claude-code' | 'codex' | 'opencode';

export type OpeningCast = {
  cursors: Record<Actor, HTMLElement>;
  /** The selection frames: the card the visitor resizes, the headline Codex drags, OpenCode's marquee. */
  boxes: Record<'card' | 'title' | 'marquee', HTMLElement>;
  /** The photograph OpenCode carries in. */
  photo: HTMLElement;
};

export type OpeningParts = {
  card: HTMLElement;
  ground: HTMLElement;
  title: HTMLElement;
  lead: HTMLElement;
  ending: HTMLElement;
  next: HTMLElement;
};

export type OpeningRun = {
  /** Jumps to the final hero at once. */
  finish: () => void;
  /** Stops without touching the hero; the caller restores the draft. */
  stop: () => void;
};

type Point = { x: number; y: number };
type Rect = Point & { width: number; height: number };
type Timing = { duration: number; easing: string };

const EASE_OUT = 'cubic-bezier(0.22, 1, 0.36, 1)';
const EASE_IN_OUT = 'cubic-bezier(0.65, 0, 0.35, 1)';
const ENTER: Timing = { duration: 640, easing: EASE_OUT };
const DRAG: Timing = { duration: 560, easing: EASE_IN_OUT };
const PRESS: Timing = { duration: 110, easing: EASE_OUT };
const FADE: Timing = { duration: 180, easing: EASE_OUT };
const DROP: Timing = { duration: 640, easing: EASE_OUT };
const EXIT: Timing = { duration: 520, easing: EASE_OUT };
const HANDOFF: Timing = { duration: 420, easing: EASE_IN_OUT };
const EXIT_STAGGER = 60;
/** How far past the viewport edge cursors start and end. */
const OFFSTAGE = 60;
/** Room a name tag needs to the right of its arrow before it flips to the left. */
const TAG_ROOM = 140;

/** The draft each part starts from; mirrors home.css. */
const DRAFT = {
  card: { right: 0.16, bottom: 0.12 },
  ground: 'scale(1.12)',
  groundBlur: 'blur(18px)',
  title: { x: 56, y: -40 },
  lead: 12,
} as const;

const rectOf = (element: Element): Rect => {
  const { x, y, width, height } = element.getBoundingClientRect();
  return { x, y, width, height };
};
/** The box around an element's rendered text, not its full-width block. */
const textRectOf = (element: Element): Rect => {
  const range = document.createRange();
  range.selectNodeContents(element);
  const { x, y, width, height } = range.getBoundingClientRect();
  return { x, y, width, height };
};
const center = (rect: Rect): Point => ({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
const translate = ({ x, y }: Point) => `translate3d(${x}px, ${y}px, 0)`;

/**
 * Plays the opening over the hero, which must be in its draft state, in
 * viewport coordinates measured at the start. The figure's turn begins at
 * once (Claude Code presses its Handoff control in the first few hundred
 * milliseconds); the other cursors finish the card around it, Codex
 * acknowledges when the turn has landed, and everyone leaves. With a known
 * mouse pointer the visitor's cursor flies to it and becomes it.
 */
export function playOpening({ cast, parts, playFigure, pointer, onDone }: {
  cast: OpeningCast;
  parts: OpeningParts;
  /** Starts the figure's turn; resolves when it rests. */
  playFigure: () => Promise<void>;
  pointer: () => Point | null;
  onDone: () => void;
}): OpeningRun {
  const running = new Set<Animation>();
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let stopped = false;
  let figureStarted = false;
  const halted = new Promise<never>(() => {});

  const animate = (element: Element, keyframes: Keyframe[], timing: Timing, delay = 0): Promise<void> => {
    if (stopped) return halted;
    const animation = element.animate(keyframes, { ...timing, delay, fill: 'both' });
    running.add(animation);
    return animation.finished.then(() => undefined, () => halted);
  };
  const wait = (ms: number) => (stopped ? halted : new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      timers.delete(timer);
      resolve();
    }, ms);
    timers.add(timer);
  }));

  const view = { width: window.innerWidth, height: window.innerHeight };
  const positions = new Map<HTMLElement, Point>();
  const place = (cursor: HTMLElement, point: Point) => {
    positions.set(cursor, point);
    cursor.style.transform = translate(point);
    cursor.toggleAttribute('data-flip', point.x > view.width - TAG_ROOM);
  };
  const move = (cursor: HTMLElement, to: Point, timing = ENTER, delay = 0) => {
    const from = positions.get(cursor) ?? to;
    positions.set(cursor, to);
    cursor.toggleAttribute('data-flip', to.x > view.width - TAG_ROOM);
    return animate(cursor, [{ transform: translate(from) }, { transform: translate(to) }], timing, delay);
  };
  const arrow = (cursor: HTMLElement) => cursor.querySelector('svg') ?? cursor;
  const press = (cursor: HTMLElement) => animate(arrow(cursor), [{ scale: '1' }, { scale: '0.86' }], PRESS);
  const release = (cursor: HTMLElement) => animate(arrow(cursor), [{ scale: '0.86' }, { scale: '1' }], PRESS);
  const show = (element: HTMLElement, visible: boolean, delay = 0) =>
    animate(element, [{ opacity: visible ? 0 : 1 }, { opacity: visible ? 1 : 0 }], FADE, delay);
  const setBox = (box: HTMLElement, rect: Rect) => Object.assign(box.style, {
    left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px`,
  });

  const { visitor, codex, opencode } = cast.cursors;
  const claude = cast.cursors['claude-code'];
  const card = rectOf(parts.card);
  const radius = getComputedStyle(parts.card).borderTopLeftRadius;
  const clip = (right: number, bottom: number) => `inset(0% ${right * 100}% ${bottom * 100}% 0% round ${radius})`;
  const draftCard: Rect = {
    ...card,
    width: card.width * (1 - DRAFT.card.right),
    height: card.height * (1 - DRAFT.card.bottom),
  };
  const draftTitle = textRectOf(parts.title);
  const leadRect = rectOf(parts.lead);
  const lead: Rect = { ...leadRect, y: leadRect.y - DRAFT.lead };
  const endingAction = parts.ending.querySelector('[data-action]') ?? parts.ending;
  const nextAction = parts.next.querySelector('[data-action]') ?? parts.next;

  const starts: Record<string, Point> = {
    visitor: { x: view.width + OFFSTAGE, y: Math.min(view.height + OFFSTAGE, draftCard.y + draftCard.height + 120) },
    codex: { x: view.width + OFFSTAGE, y: draftTitle.y - 80 },
    opencode: { x: -OFFSTAGE - TAG_ROOM, y: view.height * 0.86 },
  };
  place(visitor, starts.visitor);
  place(codex, starts.codex);
  place(opencode, starts.opencode);
  for (const cursor of [visitor, codex, opencode]) cursor.style.opacity = '1';

  /** Claude Code is already at its window: it presses Hand off and the table turns. */
  const handOff = async () => {
    const target = center(rectOf(endingAction));
    place(claude, { x: target.x + 28, y: target.y + 22 });
    void show(claude, true);
    await move(claude, target, { duration: 200, easing: EASE_OUT });
    await press(claude);
    parts.ending.setAttribute('data-done', '');
    figureStarted = true;
    const played = playFigure();
    const result = parts.ending.querySelector('.pc-swap-final');
    if (result) void animate(result, [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }], FADE, 260);
    await release(claude);
    return played;
  };

  const resizeCard = async () => {
    const corner = { x: draftCard.x + draftCard.width, y: draftCard.y + draftCard.height };
    await move(visitor, corner);
    setBox(cast.boxes.card, draftCard);
    await show(cast.boxes.card, true);
    await press(visitor);
    await Promise.all([
      move(visitor, { x: card.x + card.width, y: card.y + card.height }, DRAG),
      animate(parts.card, [{ clipPath: clip(DRAFT.card.right, DRAFT.card.bottom) }, { clipPath: clip(0, 0) }], DRAG),
      animate(cast.boxes.card, [
        { width: `${draftCard.width}px`, height: `${draftCard.height}px` },
        { width: `${card.width}px`, height: `${card.height}px` },
      ], DRAG),
    ]);
    await release(visitor);
    await show(cast.boxes.card, false, 120);
  };

  const dropPhoto = async () => {
    await wait(200);
    await move(opencode, { x: card.x + card.width * 0.3, y: card.y + card.height * 0.3 });
    await press(opencode);
    void animate(cast.photo, [{ opacity: 1, scale: '1' }, { opacity: 0, scale: '0.9' }], FADE);
    await animate(parts.ground, [
      { transform: DRAFT.ground, filter: DRAFT.groundBlur },
      { transform: 'none', filter: 'blur(0px)' },
    ], DROP);
    await release(opencode);
  };

  const revealLead = async () => {
    const from = { x: lead.x - 12, y: lead.y - 10 };
    const to = { x: lead.x + Math.min(lead.width, 560) + 12, y: lead.y + lead.height + 10 };
    await move(opencode, from, { duration: 420, easing: EASE_OUT });
    setBox(cast.boxes.marquee, { ...from, width: 0, height: 0 });
    cast.boxes.marquee.style.opacity = '1';
    await press(opencode);
    await Promise.all([
      move(opencode, to, DRAG),
      animate(cast.boxes.marquee, [
        { width: '0px', height: '0px' },
        { width: `${to.x - from.x}px`, height: `${to.y - from.y}px` },
      ], DRAG),
    ]);
    await release(opencode);
    void show(cast.boxes.marquee, false);
    await animate(parts.lead, [
      { opacity: 0, transform: `translateY(${DRAFT.lead}px)` },
      { opacity: 1, transform: 'none' },
    ], { duration: 420, easing: EASE_OUT });
  };

  const placeTitle = async () => {
    await wait(260);
    const grab = { x: draftTitle.x + draftTitle.width * 0.38, y: draftTitle.y + draftTitle.height * 0.45 };
    await move(codex, grab);
    setBox(cast.boxes.title, draftTitle);
    await show(cast.boxes.title, true);
    await press(codex);
    const offset = { x: -DRAFT.title.x, y: -DRAFT.title.y };
    await Promise.all([
      move(codex, { x: grab.x + offset.x, y: grab.y + offset.y }, DRAG),
      animate(parts.title, [{ transform: `translate(${DRAFT.title.x}px, ${DRAFT.title.y}px)` }, { transform: 'none' }], DRAG),
      animate(cast.boxes.title, [{ transform: 'none' }, { transform: translate(offset) }], DRAG),
    ]);
    await release(codex);
    void show(cast.boxes.title, false, 120);
  };

  const acknowledge = async (played: Promise<void>) => {
    await move(codex, center(rectOf(nextAction)), { duration: 520, easing: EASE_IN_OUT });
    // The receiver acknowledges once the table has turned to its station.
    await Promise.race([played, wait(700)]);
    await press(codex);
    parts.next.setAttribute('data-done', '');
    const rows = parts.next.querySelectorAll('.pc-swap-final > p, .pc-received > div > *');
    rows.forEach((row, index) => {
      void animate(row, [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }], FADE, 80 + index * 50);
    });
    await release(codex);
    await wait(240);
  };

  const leave = async () => {
    const exits: Array<[HTMLElement, Point]> = [
      [claude, { x: -OFFSTAGE - TAG_ROOM, y: (positions.get(claude)?.y ?? 0) - 60 }],
      [codex, { x: view.width + OFFSTAGE, y: (positions.get(codex)?.y ?? 0) + 40 }],
      [opencode, { x: (positions.get(opencode)?.x ?? 0) - 40, y: view.height + OFFSTAGE }],
    ];
    const leaving = exits.map(([cursor, point], index) => move(cursor, point, EXIT, index * EXIT_STAGGER));
    const visitorPointer = pointer();
    if (visitorPointer) {
      // The visitor's cursor flies to the real pointer and becomes it.
      const tag = visitor.querySelector('.pc-cursor-tag');
      if (tag) void animate(tag, [{ opacity: 1 }, { opacity: 0 }], FADE, HANDOFF.duration - FADE.duration);
      await move(visitor, visitorPointer, HANDOFF);
      await show(visitor, false);
    } else {
      leaving.push(move(visitor, { x: view.width + OFFSTAGE, y: (positions.get(visitor)?.y ?? 0) + 40 }, EXIT));
    }
    await Promise.all(leaving);
  };

  void (async () => {
    // Resolves once the figure has turned (handOff returns the figure's play).
    const turned = handOff();
    await Promise.all([
      resizeCard(),
      dropPhoto().then(revealLead),
      placeTitle().then(() => acknowledge(turned)),
    ]);
    await leave();
    if (!stopped) finishNow();
  })();

  function stop() {
    stopped = true;
    for (const timer of timers) clearTimeout(timer);
    timers.clear();
  }

  /** Clears every animation once <html> no longer holds the draft, so nothing jumps. */
  function finishNow() {
    stop();
    if (!figureStarted) void playFigure();
    onDone();
    for (const animation of running) animation.cancel();
    running.clear();
  }

  return {
    finish: finishNow,
    stop: () => {
      stop();
      for (const animation of running) animation.cancel();
      running.clear();
    },
  };
}
