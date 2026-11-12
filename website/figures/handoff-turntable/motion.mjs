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

// Motion model of the handoff turntable, shared by the site component and the
// standalone verification page. Pure functions of time: no DOM, no state.

/** Table angle at each station, in degrees. The rest pose of the build is `to`. */
export const SIDES = { from: 180, to: 0 };

/** The opening: one eased 180° turn while the drawing grows from `scale` to 1. */
export const OPENING = { duration: 1800, fade: 200, scale: 0.55 };

/** A turn between stations after the opening. */
export const TURN_MS = 1400;

/** Table angles at which the four Handoff steps sit under the index pawl. */
export const DETENTS = [180, 120, 60, 0];

const bezier = (x1, y1, x2, y2) => {
  const ax = 3 * x1 - 3 * x2 + 1;
  const bx = 3 * x2 - 6 * x1;
  const cx = 3 * x1;
  const ay = 3 * y1 - 3 * y2 + 1;
  const by = 3 * y2 - 6 * y1;
  const cy = 3 * y1;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 8; i++) {
      const err = ((ax * t + bx) * t + cx) * t - x;
      const slope = (3 * ax * t + 2 * bx) * t + cx;
      if (Math.abs(err) < 1e-6 || slope === 0) break;
      t -= err / slope;
    }
    return ((ay * t + by) * t + cy) * t;
  };
};

/** The opening curve: a long, calm arrival with no overshoot. */
export const arrive = bezier(0.33, 0.12, 0.2, 1);
/** A turn that starts from rest and comes to rest. */
export const travel = bezier(0.65, 0, 0.35, 1);

const smooth = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

/** The opening pose at `ms` after it starts. */
export function openingAt(ms) {
  const p = arrive(ms / OPENING.duration);
  return {
    angle: SIDES.from + (SIDES.to - SIDES.from) * p,
    scale: OPENING.scale + (1 - OPENING.scale) * p,
    opacity: smooth(ms / OPENING.fade),
    done: ms >= OPENING.duration,
  };
}

/** A turn from `start` to `end` degrees at `ms` after it starts. */
export function turnAt(ms, start, end) {
  const span = Math.abs(end - start) / 180;
  const duration = Math.max(1, TURN_MS * Math.sqrt(span));
  return { angle: start + (end - start) * travel(ms / duration), done: ms >= duration };
}

/** The Handoff step whose detent is nearest the index pawl at this table angle. */
export function stepAt(angle) {
  let best = 0;
  for (let i = 1; i < DETENTS.length; i++) if (Math.abs(DETENTS[i] - angle) < Math.abs(DETENTS[best] - angle)) best = i;
  return best;
}

/** The station the table rests at, or null while it is between them. */
export function sideAt(angle) {
  if (Math.abs(angle - SIDES.from) < 1e-3) return "from";
  if (Math.abs(angle - SIDES.to) < 1e-3) return "to";
  return null;
}
