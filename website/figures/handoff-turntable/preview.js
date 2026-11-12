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

// Standalone verification page glue, inlined by build.mjs after the 3D runtime
// and motion.mjs. It behaves like the site component: plays the opening on
// load, then a click or Enter/Space turns the table to the other station.
// `window.__hfSpeed` (set by a preload) slows time for contact sheets.
const root = document.querySelector(".hf");
const plate = root.querySelector(".hf-plate");
const scaler = root.querySelector(".hf-scale");
const stage = root.querySelector(".hf-stage");
const readout = root.querySelector("[data-readout]");
const status = root.querySelector(".hf-status");
const still = matchMedia("(prefers-reduced-motion: reduce)");
const now = () => performance.now() * (window.__hfSpeed ?? 1);
let angle = SIDES.to;
let side = "to";
let busy = null;
const keep = scaler.style.transform;
scaler.style.transform = "none";
let held = false;
const controller = TURN.mount(scaler, DATA, {
  onHold: () => {
    held = true;
    busy = null;
    reveal();
  },
});
scaler.style.transform = keep;
const say = (text) => {
  if (readout.textContent !== text) readout.textContent = text;
};
const draw = (value) => {
  angle = value;
  controller.set({ table: value });
  const rest = sideAt(value);
  say(rest ? LABELS.readout[rest] : LABELS.steps[stepAt(value)]);
};
const animate = (frameAt) =>
  new Promise((resolve) => {
    const t0 = now();
    const tick = () => {
      if (held || frameAt(now() - t0)) return resolve();
      requestAnimationFrame(tick);
    };
    tick();
  });
function reveal() {
  plate.style.opacity = "";
  scaler.style.transform = "";
  root.setAttribute("data-shown", "");
}
async function play() {
  if (still.matches) {
    reveal();
    draw(SIDES.to);
    return;
  }
  draw(SIDES.from);
  await animate((ms) => {
    const pose = openingAt(ms);
    plate.style.opacity = String(pose.opacity);
    scaler.style.transform = `scale(${pose.scale})`;
    draw(pose.done ? SIDES.to : pose.angle);
    return pose.done;
  });
  reveal();
  side = "to";
  status.textContent = LABELS.readout.to;
}
async function turnTo(next) {
  const run = Symbol();
  busy = run;
  const start = angle;
  const end = SIDES[next];
  side = next;
  if (still.matches) draw(end);
  else await animate((ms) => (busy !== run ? true : (draw(turnAt(ms, start, end).angle), turnAt(ms, start, end).done)));
  if (busy === run) {
    draw(end);
    busy = null;
    status.textContent = LABELS.readout[next];
  }
}
if (!still.matches) draw(SIDES.from);
const toggle = () => {
  if (root.hasAttribute("data-shown")) turnTo(side === "to" ? "from" : "to");
};
stage.addEventListener("click", toggle);
stage.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    toggle();
  }
});
window.__hf = { play, turnTo, get angle() { return angle; } };
if (!window.__hfHold) play();
