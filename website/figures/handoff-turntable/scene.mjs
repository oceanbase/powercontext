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

// The handoff turntable: a bench instrument whose rotary table is one Scope.
// The table carries a card-file box (Memory), a spindle of slips (Source
// journal), an in-tray (Review Inbox) and a handoff cartridge in a clip. Two
// fixed docking stations stand on opposite sides of the base; the table turns
// 180° from the `from` station to the `to` station, and an index pawl reads
// four detent marks on the table, one per Handoff step.
//
// World units are about one viewBox px; x and y lie on the floor, z is up.
// The build's rest pose (table at 0°) is the `to` station.

import * as k from "../anatomy-kit/iso-kit.mjs";
import * as G from "../anatomy-kit/lathe.mjs";
import * as A from "../anatomy-kit/audit.mjs";
import { turning } from "../anatomy-kit/turn-build.mjs";
import { tubePieces, tubeSvg } from "../anatomy-kit/tube.mjs";
import { DETENTS } from "./motion.mjs";

export const WIDTH = 640;
export const HEIGHT = 420;
const AZIMUTH = 52;
const ELEVATION = 30;

export const BASE = { x: -98, y: -60, w: 196, d: 126, r: 9 };
const BASE_Z = -12;
const FOOT_Z = -17;
const BEARING_R = 40;
const BEARING_TOP = 4;
const TABLE_R = 56;
const RIM_TOP = 11;
const TOP_R = 54;
export const TOP = 12.2;
const PAWL_AT = (AZIMUTH * Math.PI) / 180;

const ring = [];
for (let i = 0; i < 16; i++) ring.push([44 * Math.cos((i / 16) * Math.PI * 2), 44 * Math.sin((i / 16) * Math.PI * 2), 46]);
export const P = k.fitProjection([...k.boxCorners(BASE, FOOT_Z, 0), ...ring], WIDTH, HEIGHT, { pad: 22, azimuth: AZIMUTH, elevation: ELEVATION });

const at = (p) => k.iso(p, P);
const S = (paths, style) => k.solidSvg(paths, style);
const L = (d, style) => k.lineSvg(d, style);
const D = (points, style) => k.dotsSvg(points, style);
const VERT = (x, y) => G.frameOf([x, y, 0], [0, 0, 1], [1, 0, 0], [0, 1, 0]);
const FLOOR = { o: [0, 0, 0], a: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0] };
const ALONG_X = { o: [0, 0, 0], a: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1] };
const byDepth = (points) => [...points].sort((a, b) => k.depthOf([a[0], a[1], 0], P) - k.depthOf([b[0], b[1], 0], P));
const f = (n) => n.toFixed(3);
const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const yawed = (cx, cy, w, d, deg) => {
  const c = Math.cos((deg * Math.PI) / 180);
  const s = Math.sin((deg * Math.PI) / 180);
  return rect(-w / 2, -d / 2, w / 2, d / 2).map(([x, y]) => [cx + c * x - s * y, cy + s * x + c * y]);
};
// A rotated rectangle around the floor direction `angle`, from radius r0 to r1, half-width t.
const radial = (angle, r0, r1, t) => {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [[r0, -t], [r1, -t], [r1, t], [r0, t]].map(([r, w]) => [c * r - s * w, s * r + c * w]);
};

export function buildScene() {
  const R = A.recorder(P);
  const put = (item) => R.put({ ...item, shapes: item.shapes.map((shape, index) => R.solid(Object.assign(shape, { name: shape.name ?? (index ? `${item.name}#${index + 1}` : item.name) }))) });

  // ---- Base, feet and bearing (static, under the table) -------------------
  for (const [x, y] of k.corners(BASE, 20)) {
    const svg = S(k.cylinder(x, y, 7.4, FOOT_Z, 1.6, P, 32), { tone: "lo" }) + S(k.cylinder(x, y, 5.6, FOOT_Z + 1.6, BASE_Z - FOOT_Z - 1.6, P, 32), { tone: "mid" });
    put({ name: `stand.foot.${x}.${y}`, layer: "back", at: [x, y, FOOT_Z], svg, shapes: [A.cylinder(x, y, 7.4, FOOT_Z, 1.6, { ground: true }), A.cylinder(x, y, 5.6, FOOT_Z + 1.6, BASE_Z - FOOT_Z - 1.6)] });
  }
  {
    const screws = k.corners(BASE, 9);
    const groove = L(k.planOutline(k.insetPlan(BASE, 5), 0, P, 8), { tone: "faint" });
    const svg = S(k.slabOf(BASE, BASE_Z, -BASE_Z, P, 8, 1.6), { tone: "mid", inner: L(k.sideSeam(BASE, BASE_Z + 4, P, 8), { tone: "faint" }) }) + groove + L(screws.map(([x, y]) => k.ring(x, y, 2.3, 0, P, 18)).join(""), { tone: "lo" }) + D(screws.map(([x, y]) => at([x, y, 0])), { size: 0.5 });
    put({ name: "stand.base", layer: "back", at: [0, 0, BASE_Z], bias: 4, svg, shapes: [A.slab(BASE, BASE_Z, -BASE_Z, { ground: true })] });
  }
  {
    const F = VERT(0, 0);
    const bolts = G.dotsOf(F, BEARING_TOP, BEARING_R - 2.6, 24, P, { all: true });
    const svg = S(k.cylinder(0, 0, BEARING_R, 0, BEARING_TOP, P, 96), { tone: "lo", inner: L(G.arcOf(F, 1.6, BEARING_R, P), { tone: "faint" }) }) + D(bolts, { size: 0.45, tone: "lo" });
    put({ name: "stand.bearing", layer: "back", at: [0, 0, 0], svg, shapes: [A.cylinder(0, 0, BEARING_R, 0, BEARING_TOP)] });
  }

  // ---- Drive: a worm gearbox under the rim, turned by a crank handwheel ----
  {
    const DRIVE_AT = (124 * Math.PI) / 180;
    const c = [Math.cos(DRIVE_AT) * 70, Math.sin(DRIVE_AT) * 70];
    const plan = { x: c[0] - 7, y: c[1] - 7, w: 14, d: 14, r: 2 };
    const BOX_TOP = 11;
    const screws = k.corners(plan, 2.2);
    const boxSvg = S(k.slabOf(plan, 0, BOX_TOP, P, 4, 0.6), { tone: "mid", inner: L(k.sideSeam(plan, 3.2, P, 4), { tone: "faint" }) }) + L(k.planOutline(k.insetPlan(plan, 1.2), BOX_TOP, P, 4), { tone: "faint" }) + L(screws.map(([x, y]) => k.ring(x, y, 1, BOX_TOP, P, 14)).join(""), { tone: "lo" }) + D(screws.map(([x, y]) => at([x, y, BOX_TOP])), { size: 0.42 });
    put({ name: "stand.drive.box", layer: "front", at: [c[0], c[1], 0], svg: boxSvg, shapes: [A.slab(plan, 0, BOX_TOP)] });
    const collar = S(k.cylinder(c[0], c[1], 3.2, BOX_TOP, 2.2, P, 32), { tone: "lo" });
    put({ name: "stand.drive.collar", layer: "front", at: [c[0], c[1], BOX_TOP], svg: collar, shapes: [A.cylinder(c[0], c[1], 3.2, BOX_TOP, 2.2)] });
    const WZ = BOX_TOP + 2.2;
    const WR = 8.4;
    const spokes = [0, 120, 240].map((deg) => {
      const a = (deg * Math.PI) / 180 + 0.4;
      return k.lineOnTop([c[0] + 2.4 * Math.cos(a), c[1] + 2.4 * Math.sin(a)], [c[0] + (WR - 1.6) * Math.cos(a), c[1] + (WR - 1.6) * Math.sin(a)], WZ + 1.8, P);
    }).join("");
    const wheel = S(k.cylinder(c[0], c[1], WR, WZ, 1.8, P, 64, 0.4), { tone: "hi", crease: "lo", inner: L(k.knurl(c[0], c[1], WR, WZ + 0.3, WZ + 1.5, 48, P), { tone: "lo" }) }) + L(k.ring(c[0], c[1], WR - 1.6, WZ + 1.8, P, 48) + k.ring(c[0], c[1], 2.4, WZ + 1.8, P, 20), { tone: "lo" }) + L(spokes, { tone: "mid" });
    put({ name: "stand.drive.wheel", layer: "front", at: [c[0], c[1], WZ], svg: wheel, shapes: [A.cylinder(c[0], c[1], WR, WZ, 1.8)] });
    const hub = S(k.cylinder(c[0], c[1], 2.2, WZ + 1.8, 1.2, P, 24), { tone: "mid" }) + D([at([c[0], c[1], WZ + 3])], { size: 0.45 });
    put({ name: "stand.drive.hub", layer: "front", at: [c[0], c[1], WZ + 1.8], svg: hub, shapes: [A.cylinder(c[0], c[1], 2.2, WZ + 1.8, 1.2)] });
    const ka = DRIVE_AT + 2.2;
    const kx = c[0] + (WR - 1.8) * Math.cos(ka);
    const ky = c[1] + (WR - 1.8) * Math.sin(ka);
    const knob = S(k.cylinder(kx, ky, 1.5, WZ + 1.8, 5.4, P, 24, 0.3), { tone: "mid", inner: L(k.sideArc(kx, ky, 1.5, WZ + 3, P, 16), { tone: "faint" }) });
    put({ name: "stand.drive.knob", layer: "front", at: [kx, ky, WZ + 1.8], svg: knob, shapes: [A.cylinder(kx, ky, 1.5, WZ + 1.8, 5.4)] });
  }

  // ---- Nameplate on the bench ---------------------------------------------
  {
    const plate = { x: 54, y: 52, w: 26, d: 8, r: 1 };
    const z = 0.9;
    const rules = [[plate.x + 6.4, 15], [plate.x + 6.4, 10], [plate.x + 6.4, 13]].map(([x0, len], i) => k.lineOnTop([x0, plate.y + 2.2 + i * 1.8], [x0 + len, plate.y + 2.2 + i * 1.8], z, P)).join("");
    const screws = [[plate.x + 2.6, plate.y + plate.d / 2], [plate.x + plate.w - 2.6, plate.y + plate.d / 2]];
    const svg = S(k.slabOf(plate, 0, z, P, 4), { tone: "mid" }) + L(k.planOutline(k.insetPlan(plate, 1.4), z, P, 4), { tone: "faint" }) + L(rules, { tone: "lo", free: true }) + D(screws.map(([x, y]) => at([x, y, z])), { size: 0.5 });
    put({ name: "stand.label", layer: "back", at: [plate.x + 14, plate.y + 4, 0], svg, shapes: [A.slab(plate, 0, z)] });
  }

  // ---- Index: a spring detent plunger in its housing, with a reading arm ---
  {
    const HOUSE_TOP = 12.4;
    const house = radial(PAWL_AT, 60, 68, 3.6);
    const ca = Math.cos(PAWL_AT);
    const sa = Math.sin(PAWL_AT);
    const arm = [[58.6, -0.5], [66, -2.4], [66, 2.4], [58.6, 0.5]].map(([r, w]) => [ca * r - sa * w, sa * r + ca * w]);
    const head = [ca * 63.6, sa * 63.6];
    const houseSvg = S(k.extrude(house, 0, HOUSE_TOP, P, { bevel: 0.4 }), { tone: "mid", inner: L(k.ringSeam(house, 3, P), { tone: "faint" }) });
    put({ name: "stand.index.housing", layer: "front", at: [ca * 64, sa * 64, 0], svg: houseSvg, shapes: [A.extruded(house, 0, HOUSE_TOP)] });
    const F = G.frameAlong([ca * 68, sa * 68, 7.4], [ca, sa, 0]);
    const cap = G.disc(0, 1.4, 2.6, F, P);
    const nut = G.disc(1.4, 2.4, 1.8, F, P);
    const capSvg = S(cap, { tone: "lo" }) + S(nut, { tone: "hi", crease: "lo", inner: L(G.ribsOf(F, 1.4, 2.4, 1.8, 18, P), { tone: "lo" }) });
    put({ name: "stand.index.cap", layer: "front", at: [ca * 69.4, sa * 69.4, 7.4], svg: capSvg, shapes: [A.disc(F, 0, 1.4, 2.6), A.disc(F, 1.4, 2.4, 1.8)] });
    const armSvg = S(k.extrude(arm, HOUSE_TOP, 1.6, P, { bevel: 0.3 }), { tone: "hi", crease: "lo" }) + D([at([head[0], head[1], HOUSE_TOP + 1.6])], { size: 0.5 });
    put({ name: "stand.index.arm", layer: "front", at: [ca * 61, sa * 61, HOUSE_TOP], svg: armSvg, shapes: [A.extruded(arm, HOUSE_TOP, 1.6)] });
  }

  // ---- Docking stations (static) -----------------------------------------
  const STATIONS = [];
  for (const sigma of [-1, 1]) {
    const layer = sigma < 0 ? "back" : "front";
    const under = "back";
    const mx = (x0, x1) => (sigma > 0 ? [x0, x1] : [-x1, -x0]);
    const plan = (x0, x1, y, d, r) => {
      const [a, b] = mx(x0, x1);
      return { x: a, y, w: b - a, d, r };
    };
    const tag = sigma < 0 ? "from" : "to";
    const pedestal = plan(60, 92, -20, 40, 3);
    const riser = plan(66, 90, -10, 20, 2.5);
    const head = plan(59.5, 81.5, -8.5, 17, 2);
    const HEAD_TOP = 26;
    {
      const screws = byDepth(k.corners(pedestal, 3.2));
      const svg = S(k.slabOf(pedestal, 0, 3, P, 4, 0.6), { tone: "lo" }) + L(screws.map(([x, y]) => k.ring(x, y, 1.3, 3, P, 14)).join(""), { tone: "lo" }) + D(screws.map(([x, y]) => at([x, y, 3])), { size: 0.42 });
      put({ name: `stand.${tag}.pedestal`, layer: under, at: [sigma * 76, 0, 0], svg, shapes: [A.slab(pedestal, 0, 3)] });
    }
    {
      const screws = [[sigma * 86.4, -6.4], [sigma * 86.4, 6.4]];
      const svg = S(k.slabOf(riser, 3, 8, P, 4, 0.6), { tone: "mid", inner: L(k.sideSeam(riser, 6.5, P, 4), { tone: "faint" }) }) + L(screws.map(([x, y]) => k.ring(x, y, 1.2, 11, P, 14)).join(""), { tone: "lo" }) + D(screws.map(([x, y]) => at([x, y, 11])), { size: 0.42 });
      put({ name: `stand.${tag}.riser`, layer, at: [sigma * 78, 0, 3], svg, shapes: [A.slab(riser, 3, 8)] });
    }
    {
      const plate = plan(68, 84, 12.5, 5.6, 0.8);
      const z = 3.8;
      const rules = [11, 7, 9].map((len, i) => k.lineOnTop([plate.x + 2.6, plate.y + 1.5 + i * 1.3], [plate.x + 2.6 + len, plate.y + 1.5 + i * 1.3], z, P)).join("");
      const svg = S(k.slabOf(plate, 3, 0.8, P, 3), { tone: "mid" }) + L(rules, { tone: "lo", free: true }) + D([at([plate.x + plate.w - 1.6, plate.y + 2.8, z])], { size: 0.42 });
      put({ name: `stand.${tag}.plate`, layer: under, at: [plate.x + 8, plate.y + 2.8, 3], svg, shapes: [A.slab(plate, 3, 0.8)] });
    }
    {
      const inner = sigma * 59.5;
      const mouth = [];
      const slot = { y0: -6.7, y1: 6.7, z0: 12.8, z1: 22.5, r: 1.4 };
      const corner = (cy, cz, a0) => {
        for (let i = 0; i <= 6; i++) {
          const a = a0 + (i / 6) * (Math.PI / 2);
          mouth.push([inner, cy + slot.r * Math.cos(a), cz + slot.r * Math.sin(a)]);
        }
      };
      corner(slot.y1 - slot.r, slot.z1 - slot.r, 0);
      corner(slot.y0 + slot.r, slot.z1 - slot.r, Math.PI / 2);
      corner(slot.y0 + slot.r, slot.z0 + slot.r, Math.PI);
      corner(slot.y1 - slot.r, slot.z0 + slot.r, (3 * Math.PI) / 2);
      const back = mouth.map(([x, y, z]) => [x + sigma * 3, y * 0.82, slot.z0 + 1.2 + (z - slot.z0) * 0.78]);
      const visible = sigma < 0;
      const mouthSvg = visible
        ? `<path class="iso-shade" data-shade="0" d="${k.pathOf(mouth.map(at), true)}"/>` + L(k.pathOf(back.map(at), true), { tone: "faint" }) + L(k.pathOf(mouth.map(at), true), { tone: "lo" }) + D([-3.6, -1.2, 1.2, 3.6].map((y) => at([inner + sigma * 3, y * 0.82, 17.4])), { size: 0.45, tone: "hi" })
        : "";
      const win = [];
      for (const [wx, wz] of [[64, 15.2], [76, 15.2], [76, 19.8], [64, 19.8]]) win.push([sigma * wx, 8.5, wz]);
      const winSvg = `<path class="iso-shade" data-shade="0" d="${k.pathOf(win.map(at), true)}"/>` + L(k.pathOf(win.map(at), true), { tone: "lo" }) + L(k.pathOf([[sigma * 66, 8.5, 17.5], [sigma * 74, 8.5, 17.5]].map(at)), { tone: "faint", free: true });
      const screws = [[sigma * 78.6, -6], [sigma * 78.6, 6]];
      const top = L(k.planOutline(k.insetPlan(head, 1.8), HEAD_TOP, P, 4), { tone: "faint" }) + L(screws.map(([x, y]) => k.ring(x, y, 1.2, HEAD_TOP, P, 14)).join(""), { tone: "lo" });
      const svg = S(k.slabOf(head, 11, HEAD_TOP - 11, P, 4, 0.8), { tone: "mid", inner: L(k.sideSeam(head, 13.6, P, 4), { tone: "faint" }) }) + mouthSvg + winSvg + top + D(screws.map(([x, y]) => at([x, y, HEAD_TOP])), { size: 0.42 });
      put({ name: `stand.${tag}.head`, layer, at: [sigma * 70, 0, 11], svg, shapes: [A.slab(head, 11, HEAD_TOP - 11)] });
    }
    // Guide rails either side of the mouth: the cartridge's contact face lines up between them.
    for (const side of [-1, 1]) {
      const rail = plan(58.7, 59.5, side < 0 ? -8.4 : 6.8, 1.6, 0);
      const svg = S(k.slabOf(rail, 11.6, 12.4, P, 2, 0.2), { tone: "hi", crease: "lo" });
      put({ name: `stand.${tag}.guide.${side < 0 ? "a" : "b"}`, layer, at: [rail.x + rail.w / 2, rail.y + 0.8, 11.6], svg, shapes: [A.slab(rail, 11.6, 12.4)] });
    }
    // Signal cable: from a port on the riser, through a strap, into a terminal block.
    {
      const x0 = sigma * 78;
      const CR = 0.9;
      const block = plan(70, 86, 33, 10, 1.2);
      const BLOCK_TOP = 7;
      const terms = [[x0 - 3.6, 38], [x0 + 3.6, 38]];
      const blockSvg = S(k.slabOf(block, 0, BLOCK_TOP, P, 4, 0.5), { tone: "mid", inner: L(k.sideSeam(block, 2.2, P, 4), { tone: "faint" }) }) + L(k.planOutline(k.insetPlan(block, 1.4), BLOCK_TOP, P, 4), { tone: "faint" }) + L(terms.map(([x, y]) => k.ring(x, y, 1.6, BLOCK_TOP, P, 18)).join(""), { tone: "lo" }) + D(terms.map(([x, y]) => at([x, y, BLOCK_TOP])), { size: 0.5, tone: "hi" });
      put({ name: `stand.${tag}.terminal`, layer, at: [x0, 38, 0], svg: blockSvg, shapes: [A.slab(block, 0, BLOCK_TOP)] });
      const name = `stand.${tag}.cable`;
      const route = G.fillet([[x0, 10, 7], [x0, 22, 7], [x0, 26, 2.6], [x0, 33, 2.6]], 3, 10);
      const total = G.pathLength(route);
      const strap = { x: x0 - 2.6, y: 28.6, w: 5.2, d: 2, r: 0.6 };
      const at3 = (y) => {
        let walked = 0;
        for (let i = 1; i < route.length; i++) {
          const a = route[i - 1];
          const b = route[i];
          const len = G.len3(G.sub3(b, a));
          if ((a[1] - y) * (b[1] - y) <= 0 && Math.abs(b[1] - a[1]) > 1e-9) return walked + (len * (y - a[1])) / (b[1] - a[1]);
          walked += len;
        }
        return null;
      };
      const PORT = 1.2;
      const gaps = [[0, PORT], [at3(strap.y), at3(strap.y + strap.d)], [total - PORT, total]];
      R.route(name, route, CR, { owner: name, limp: true, gaps });
      tubePieces(route, CR, P, { maxLength: 8, gaps }).forEach((piece, index) => put({ name: `${name}:${index}`, layer: Math.max(...piece.points.map((q) => q[2])) + CR <= BEARING_TOP ? "back" : layer, route: name, chunk: index, at: piece.mid, svg: tubeSvg(piece, { tone: "mid" }), shapes: [A.tube(piece.points, piece.radii, { name: `${name}:${index}`, owner: name })] }));
      const portF = G.frameAlong([x0, 10, 7], [0, 1, 0]);
      put({ name: `${name}.port`, layer, at: [x0, 10.6, 7], svg: S(G.disc(0, PORT, 1.7, portF, P), { tone: "lo" }), shapes: [A.disc(portF, 0, PORT, 1.7, { owner: name })] });
      const glandF = G.frameAlong([x0, 33, 2.6], [0, -1, 0]);
      put({ name: `${name}.gland`, layer, at: [x0, 32.4, 2.6], svg: S(G.disc(0, PORT, 1.6, glandF, P), { tone: "lo" }), shapes: [A.disc(glandF, 0, PORT, 1.6, { owner: name })] });
      const strapSvg = S(k.slabOf(strap, 0, 4.6, P, 3, 0.3), { tone: "mid" }) + D([at([strap.x + 1, strap.y + 1, 4.6]), at([strap.x + strap.w - 1, strap.y + 1, 4.6])], { size: 0.4 });
      put({ name: `${name}.strap`, layer, at: [x0, strap.y + 1, 0], svg: strapSvg, shapes: [A.slab(strap, 0, 4.6, { owner: name })] });
    }
    {
      const [lx, ly] = [sigma * 70, -3.2];
      const svg = S(k.cylinder(lx, ly, 2.8, HEAD_TOP, 1.4, P, 32), { tone: "mid" }) + S(k.cylinder(lx, ly, 2, HEAD_TOP + 1.4, 1.2, P, 28, 0.3), { tone: "hi", crease: "lo" });
      put({ name: `stand.${tag}.lamp`, layer, at: [lx, ly, HEAD_TOP], svg, shapes: [A.cylinder(lx, ly, 2.8, HEAD_TOP, 1.4), A.cylinder(lx, ly, 2, HEAD_TOP + 1.4, 1.2)] });
    }
    {
      const [nx, ny] = [sigma * 70, 4.2];
      const kz = HEAD_TOP + 4.2;
      const svg = S(k.cylinder(nx, ny, 3.4, HEAD_TOP, 4.2, P, 40), { tone: "mid", inner: L(k.knurl(nx, ny, 3.4, HEAD_TOP + 0.5, kz - 0.5, 30, P), { tone: "lo" }) + L(k.sideArc(nx, ny, 3.4, HEAD_TOP + 0.5, P, 24) + k.sideArc(nx, ny, 3.4, kz - 0.5, P, 24), { tone: "faint" }) }) + L(k.lineOnTop([nx - 2.4 * sigma, ny], [nx + 2.6 * sigma, ny], kz, P), { tone: "hi", free: "end" });
      put({ name: `stand.${tag}.knob`, layer, at: [nx, ny, HEAD_TOP], svg, shapes: [A.cylinder(nx, ny, 3.4, HEAD_TOP, 4.2)] });
    }
    STATIONS.push({ tag, sigma });
  }

  // ---- The table and everything on it (one turn group) --------------------
  const T = turning(P, { recorder: R });
  const table = T.group("table", { turn: { pivot: [0, 0] } });
  const live = T.layer("table");
  T.stack(["back", "table", "front"]);
  const H = { group: table, layer: live };
  const slab = (name, rectPlan, z, h, extra = {}) => T.prism({ name, ...H, plan: rectPlan, z, h, steps: extra.steps ?? 2, tone: "mid", ...extra });
  const prism = (name, F, polygon, s0, s1, extra = {}) => T.prism({ name, ...H, F, polygon, s0, s1, tone: "mid", ...extra });
  const round = (name, x, y, s0, s1, r, extra = {}) => T.round({ name, ...H, F: VERT(x, y), s0, s1, r, tone: "mid", ...extra });

  // Table: knurled rim, bevel, top face with the degree ring and four detents.
  const rim = round("table.rim", 0, 0, BEARING_TOP, RIM_TOP, TABLE_R, { tone: "hi", crease: "lo", details: { seams: [BEARING_TOP + 0.8, RIM_TOP - 0.8], ribs: { s0: BEARING_TOP + 0.8, s1: RIM_TOP - 0.8, count: 168, fade: [0.08, 0.5], tone: "lo" } } });
  void rim;
  const bevel = round("table.bevel", 0, 0, RIM_TOP, TOP, [TABLE_R, TOP_R], { tone: "hi", crease: "lo" });
  {
    const tick = (a, r0, r1) => `M${f(r0 * Math.cos(a))} ${f(r0 * Math.sin(a))}L${f(r1 * Math.cos(a))} ${f(r1 * Math.sin(a))}`;
    const circle = (r, n) => Array.from({ length: n }, (_, j) => `${j ? "L" : "M"}${f(r * Math.cos((j / n) * Math.PI * 2))} ${f(r * Math.sin((j / n) * Math.PI * 2))}`).join("") + "Z";
    const outer = TOP_R - 1.2;
    const minor = [];
    const major = [];
    for (let i = 0; i < 72; i++) (i % 6 ? minor : major).push(tick((i / 72) * Math.PI * 2, outer - (i % 6 ? 1.4 : 2.4), outer));
    const marks = DETENTS.map((angle) => tick(PAWL_AT - (angle * Math.PI) / 180, outer - 5.6, outer)).join("");
    const pips = DETENTS.map((angle) => {
      const a = PAWL_AT - (angle * Math.PI) / 180;
      return `<circle class="hf-pip" cx="${f((outer - 6.8) * Math.cos(a))}" cy="${f((outer - 6.8) * Math.sin(a))}" r="0.75"/>`;
    }).join("");
    const svg = `<path class="iso-line" data-tone="lo" d="${circle(outer, 220)}"/><path class="iso-line" data-tone="lo" d="${minor.join("")}"/><path class="iso-line" data-tone="mid" d="${major.join("")}"/><path class="iso-line hf-detent" data-tone="hi" d="${marks}"/>${pips}<path class="iso-line" data-tone="faint" d="${circle(outer - 9.4, 200)}"/>`;
    T.plane({ part: bevel, o: [0, 0, TOP], u: [1, 0, 0], v: [0, 1, 0], svg, fade: [0.04, 0.3] });
  }
  round("table.hub", 0, 0, TOP, TOP + 2.2, 7, { tone: "mid", details: { bolts: [{ s: TOP + 2.2, r: 5, count: 6, size: 0.45, phase: 0.2 }] } });
  round("table.hub.cap", 0, 0, TOP + 2.2, TOP + 3.2, 3, { tone: "hi", crease: "lo", details: { bolts: [{ s: TOP + 3.2, r: 0, count: 1, size: 0.42 }] } });

  // Memory: a card-file box of index cards with staggered revision tabs.
  {
    const B = { x0: -44, y0: -38, x1: -8, y1: -10 };
    const FLOOR_TOP = TOP + 1.2;
    const WALL = 1.4;
    const WALL_TOP = FLOOR_TOP + 10.2;
    slab("memory.floor", { x: B.x0, y: B.y0, w: B.x1 - B.x0, d: B.y1 - B.y0, r: 0.5 }, TOP, 1.2, { tone: "lo" });
    slab("memory.wall.back", { x: B.x0, y: B.y0, w: B.x1 - B.x0, d: WALL, r: 0 }, FLOOR_TOP, WALL_TOP - FLOOR_TOP, { bevel: 0.3 });
    slab("memory.wall.front", { x: B.x0, y: B.y1 - WALL, w: B.x1 - B.x0, d: WALL, r: 0 }, FLOOR_TOP, WALL_TOP - FLOOR_TOP, { bevel: 0.3 });
    slab("memory.wall.left", { x: B.x0, y: B.y0 + WALL, w: WALL, d: B.y1 - B.y0 - 2 * WALL, r: 0 }, FLOOR_TOP, WALL_TOP - FLOOR_TOP, { bevel: 0.3 });
    slab("memory.wall.right", { x: B.x1 - WALL, y: B.y0 + WALL, w: WALL, d: B.y1 - B.y0 - 2 * WALL, r: 0 }, FLOOR_TOP, WALL_TOP - FLOOR_TOP, { bevel: 0.3 });
    const holder = [[-27.6, FLOOR_TOP + 4], [-20.4, FLOOR_TOP + 4], [-20.4, FLOOR_TOP + 8.2], [-27.6, FLOOR_TOP + 8.2]];
    prism("memory.holder", { o: [0, 0, 0], a: [0, 1, 0], u: [0, 0, 1], v: [1, 0, 0] }, holder.map(([x, z]) => [z, x]), B.y1, B.y1 + 0.5, { tone: "hi", crease: "lo", details: { dots: [[-27, FLOOR_TOP + 6.1], [-21, FLOOR_TOP + 6.1]].map(([x, z]) => ({ at: [B.y1 + 0.5, z, x], normal: [0, 1, 0], size: 0.4 })) } });
    slab("memory.follower", { x: -14.6, y: B.y0 + WALL + 0.6, w: 3.6, d: B.y1 - B.y0 - 2 * WALL - 1.2, r: 0.4 }, FLOOR_TOP, 7.4, { bevel: 0.3, details: { dots: [{ at: [FLOOR_TOP + 7.4, -12.8, (B.y0 + B.y1) / 2], normal: [0, 0, 1], size: 0.5 }] } });
    const CARD_T = 0.6;
    const yIn0 = B.y0 + WALL + 0.3;
    const yIn1 = B.y1 - WALL - 0.3;
    const CARD_TOP = FLOOR_TOP + 14.4;
    for (let i = 0; i < 8; i++) {
      const x = B.x0 + WALL + 2.2 + i * 3.6;
      const guide = i === 0;
      const top = guide ? CARD_TOP + 0.6 : CARD_TOP;
      prism(`memory.card.${i}`, ALONG_X, rect(yIn0, FLOOR_TOP, yIn1, top), x, x + CARD_T, { tone: guide ? "mid" : "hi", crease: "lo" });
      const t0 = yIn0 + 1.2 + (i % 4) * 5.4;
      const tab = [[t0, top], [t0 + 5.2, top], [t0 + 4.6, top + 2.4], [t0 + 0.6, top + 2.4]];
      prism(`memory.tab.${i}`, ALONG_X, tab, x, x + CARD_T, { tone: guide ? "mid" : "hi", crease: "lo" });
    }
  }

  // Source journal: a weighted spindle with a stack of paper slips.
  {
    const [cx, cy] = [-28, 24];
    const BASE_TOP = TOP + 2.4;
    round("journal.base", cx, cy, TOP, BASE_TOP, 6.6, { tone: "mid", details: { seams: [TOP + 0.9] } });
    const yaws = [-6, 5, -2, 8, -4, 3];
    const SLIP = 0.9;
    let z = BASE_TOP;
    let last = null;
    yaws.forEach((yaw, i) => {
      const dx = ((i * 37) % 7) * 0.12 - 0.36;
      const dy = ((i * 53) % 5) * 0.14 - 0.28;
      last = prism(`journal.slip.${i}`, FLOOR, yawed(cx + dx, cy + dy, 14, 10, yaw), z, z + SLIP, { tone: "hi", crease: "faint" });
      if (i === yaws.length - 1) last.yaw = { yaw, cx: cx + dx, cy: cy + dy, z: z + SLIP };
      z += SLIP;
    });
    {
      const { yaw, cx: sx, cy: sy, z: sz } = last.yaw;
      const c = Math.cos((yaw * Math.PI) / 180);
      const s = Math.sin((yaw * Math.PI) / 180);
      const rules = [[-5.6, -3, 1.6], [-5.6, -1.6, 2.6], [-5.6, 2.2, 3], [-5.6, 3.4, 0.4]].map(([x0, y, x1]) => `M${f(x0)} ${f(y)}L${f(x1)} ${f(y)}`).join("");
      T.plane({ part: last, o: [sx, sy, sz], u: [c, s, 0], v: [-s, c, 0], svg: `<path class="iso-line" data-tone="lo" data-free="" d="${rules}"/>` });
    }
    round("journal.rod", cx, cy, z, z + 12, 0.75, { tone: "hi", crease: "lo" });
    round("journal.tip", cx, cy, z + 12, z + 14.4, [0.75, 0.12], { tone: "hi", crease: "lo" });
  }

  // Review Inbox: a shallow in-tray with two pending candidate slips.
  {
    const B = { x0: 0, y0: 16, x1: 28, y1: 38 };
    const FLOOR_TOP = TOP + 1;
    const WALL = 1.2;
    const WALL_TOP = FLOOR_TOP + 5;
    slab("inbox.floor", { x: B.x0, y: B.y0, w: B.x1 - B.x0, d: B.y1 - B.y0, r: 0.5 }, TOP, 1, { tone: "lo" });
    slab("inbox.wall.lip", { x: B.x0, y: B.y0, w: B.x1 - B.x0, d: WALL, r: 0.5 }, FLOOR_TOP, 2.6, { bevel: 0.25 });
    slab("inbox.wall.back", { x: B.x0, y: B.y1 - WALL, w: B.x1 - B.x0, d: WALL, r: 0.5 }, FLOOR_TOP, WALL_TOP - FLOOR_TOP, { bevel: 0.25 });
    slab("inbox.wall.left", { x: B.x0, y: B.y0 + WALL, w: WALL, d: B.y1 - B.y0 - 2 * WALL, r: 0 }, FLOOR_TOP, WALL_TOP - FLOOR_TOP, { bevel: 0.25 });
    slab("inbox.wall.right", { x: B.x1 - WALL, y: B.y0 + WALL, w: WALL, d: B.y1 - B.y0 - 2 * WALL, r: 0 }, FLOOR_TOP, WALL_TOP - FLOOR_TOP, { bevel: 0.25 });
    const slips = [[14.2, 27.4, 20, 14, 4], [13.6, 26.8, 19, 13.4, -5]];
    slips.forEach(([sx, sy, w, d, yaw], i) => {
      const z0 = FLOOR_TOP + i * 0.5;
      const part = prism(`inbox.candidate.${i}`, FLOOR, yawed(sx, sy, w, d, yaw), z0, z0 + 0.5, { tone: "hi", crease: "lo" });
      const c = Math.cos((yaw * Math.PI) / 180);
      const s = Math.sin((yaw * Math.PI) / 180);
      const lines = i === 1 ? [[-7.6, -4, 3], [-7.6, -2.2, 6], [-7.6, -0.4, 1.4], [-7.6, 1.4, 4.8]] : [];
      if (lines.length) {
        const rules = lines.map(([x0, y, x1]) => `M${f(x0)} ${f(y)}L${f(x1)} ${f(y)}`).join("");
        const box = `M${f(4.6)} ${f(2.6)}L${f(7.4)} ${f(2.6)}L${f(7.4)} ${f(5.4)}L${f(4.6)} ${f(5.4)}Z`;
        T.plane({ part, o: [sx, sy, z0 + 0.5], u: [c, s, 0], v: [-s, c, 0], svg: `<path class="iso-line" data-tone="lo" data-free="" d="${rules}"/><path class="iso-line" data-tone="mid" d="${box}"/>` });
      }
    });
  }

  // Handoff: a cartridge in a clip at the rim, pointing at the station it serves.
  {
    const X0 = 34;
    const X1 = 52.6;
    slab("handoff.clip.plate", { x: X0, y: -8.5, w: X1 - X0, d: 17, r: 0.8 }, TOP, 1.2, { tone: "lo", details: { dots: [[X0 + 1.6, -7.1], [X0 + 1.6, 7.1], [X1 - 1.6, -7.1], [X1 - 1.6, 7.1]].map(([x, y]) => ({ at: [TOP + 1.2, x, y], normal: [0, 0, 1], size: 0.4 })) } });
    const BED = TOP + 1.2;
    slab("handoff.clip.jaw.left", { x: X0 + 3.2, y: -8.5, w: X1 - X0 - 6.4, d: 2, r: 0.4 }, BED, 5, { bevel: 0.25 });
    slab("handoff.clip.jaw.right", { x: X0 + 3.2, y: 6.5, w: X1 - X0 - 6.4, d: 2, r: 0.4 }, BED, 5, { bevel: 0.25 });
    slab("handoff.clip.stop", { x: X0, y: -6.3, w: 2, d: 12.6, r: 0.4 }, BED, 6.2, { bevel: 0.25 });
    const C0 = X0 + 2;
    const C1 = 51.6;
    const CTOP = BED + 8;
    slab("handoff.cartridge", { x: C0, y: -6.3, w: C1 - C0, d: 12.6, r: 1 }, BED, CTOP - BED, { steps: 3, tone: "hi", crease: "lo", lit: true, bevel: 0.5, details: { seams: [BED + 2.2] } });
    slab("handoff.cartridge.grip", { x: C0 + 2.6, y: -4.4, w: 8.4, d: 8.8, r: 1.2 }, CTOP, 1, { steps: 3, tone: "hi", crease: "lo", lit: true, bevel: 0.3 });
    const pins = [-3.6, -1.2, 1.2, 3.6].map((y) => ({ at: [BED + 4, C1 + 0.6, y], normal: [1, 0, 0], size: 0.42, tone: "hi" }));
    slab("handoff.cartridge.contact", { x: C1, y: -4.8, w: 0.6, d: 9.6, r: 0 }, BED + 1.6, 4.8, { tone: "mid", details: { dots: pins } });
  }

  return { R, T, STATIONS };
}

/** Screen point of the hub, in viewBox units: the transform origin of the opening. */
export const HUB = k.iso([0, 0, TOP + 2.2], P);
