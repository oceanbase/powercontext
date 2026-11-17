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

type Point = {
  depth: number;
  progress: number;
  x: number;
  y: number;
};

type RibbonRun = {
  front: boolean;
  points: Point[];
  strand: number;
};

/** One helix turn spans this many radii along the axis. */
const TURN_LENGTH = 3.4;
const PAIRS_PER_TURN = 5.5;
const SAMPLES_PER_TURN = 72;
/** Milliseconds for the helix to close most of the gap to the cursor. */
const FOLLOW_EASE = 90;

/**
 * The double helix from the previous landing page, laid along the footer.
 * It idles in a slow spin; under a mouse it screws along with the cursor, so
 * the strands keep pace with the pointer as it moves sideways.
 */
export function FooterHelix() {
  const fieldRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const fieldElement = fieldRef.current;
    const canvasElement = canvasRef.current;
    if (!fieldElement || !canvasElement) return;

    const contextValue = canvasElement.getContext('2d', { alpha: true });
    if (!contextValue) return;

    const field = fieldElement;
    const canvas = canvasElement;
    const context: CanvasRenderingContext2D = contextValue;
    const sheet = field.closest('.pc-home')?.querySelector('.pc-home-sheet') ?? null;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let colors = readColors();
    let exposed = false;
    let follow: { anchorRotation: number; anchorX: number; target: number } | null = null;
    let frame = 0;
    let height = 0;
    let lastTime = 0;
    let pairCount = 0;
    let radius = 48;
    let rotation = -0.48;
    let sampleCount = 0;
    let scale = 1;
    let turns = 1;
    let width = 0;

    function readColors() {
      const styles = getComputedStyle(field);
      return {
        accent: styles.getPropertyValue('--helix-accent').trim(),
        accentDeep: styles.getPropertyValue('--helix-accent-deep').trim(),
        fill: styles.getPropertyValue('--helix-fill').trim(),
        fillMuted: styles.getPropertyValue('--helix-fill-muted').trim(),
        muted: styles.getPropertyValue('--helix-muted').trim(),
        surface: styles.getPropertyValue('--helix-surface').trim(),
      };
    }

    function clamp(value: number, min: number, max: number) {
      return Math.max(min, Math.min(max, value));
    }

    // The axis overshoots both edges by one radius so the strand ends stay off-canvas.
    function axisLength() {
      return width + radius * 2;
    }

    function project(progress: number, strand: number, phase: number): Point {
      const angle = progress * Math.PI * 2 * turns + strand * Math.PI + phase;
      const localY = Math.cos(angle) * radius;
      const localZ = Math.sin(angle) * radius;
      const focal = radius * 3.33;
      const perspective = focal / (focal + localZ);

      return {
        depth: clamp((1 - localZ / (radius * 1.12)) / 2, 0, 1),
        progress,
        x: -radius + progress * axisLength() + localZ * 0.08,
        y: height / 2 + localY * perspective,
      };
    }

    function buildGeometry(phase: number) {
      const strands: Point[][] = [[], []];
      const pairs: Array<{ first: Point; second: Point }> = [];

      for (let strand = 0; strand < 2; strand += 1) {
        for (let index = 0; index < sampleCount; index += 1) {
          strands[strand].push(project(index / (sampleCount - 1), strand, phase));
        }
      }

      for (let index = 0; index < pairCount; index += 1) {
        const progress = (index + 0.5) / pairCount;
        pairs.push({
          first: project(progress, 0, phase),
          second: project(progress, 1, phase),
        });
      }

      return { pairs, strands };
    }

    function drawPairs(pairs: Array<{ first: Point; second: Point }>) {
      pairs.forEach((pair) => {
        const depth = (pair.first.depth + pair.second.depth) / 2;
        const midpoint = {
          x: (pair.first.x + pair.second.x) / 2,
          y: (pair.first.y + pair.second.y) / 2,
        };
        const lineWidth = (6.2 + depth * 2.4) * scale;

        context.beginPath();
        context.moveTo(pair.first.x, pair.first.y);
        context.lineTo(pair.second.x, pair.second.y);
        context.globalAlpha = 0.52 + depth * 0.22;
        context.lineCap = 'round';
        context.lineWidth = lineWidth + 1.8 * scale;
        context.strokeStyle = colors.surface;
        context.stroke();

        context.beginPath();
        context.moveTo(pair.first.x, pair.first.y);
        context.lineTo(midpoint.x, midpoint.y);
        context.globalAlpha = 0.58 + depth * 0.2;
        context.lineCap = 'butt';
        context.lineWidth = lineWidth;
        context.strokeStyle = colors.muted;
        context.stroke();

        context.beginPath();
        context.moveTo(midpoint.x, midpoint.y);
        context.lineTo(pair.second.x, pair.second.y);
        context.globalAlpha = 0.7 + depth * 0.18;
        context.lineWidth = lineWidth;
        context.strokeStyle = colors.accent;
        context.stroke();
      });
    }

    function buildRibbonRuns(strands: Point[][]) {
      const runs: RibbonRun[] = [];

      strands.forEach((points, strand) => {
        let current: RibbonRun = {
          front: points[0].depth >= 0.5,
          points: [points[0]],
          strand,
        };

        for (let index = 1; index < points.length; index += 1) {
          const front = points[index].depth >= 0.5;
          if (front !== current.front) {
            current.points.push(points[index]);
            runs.push(current);
            current = {
              front,
              points: [points[index - 1], points[index]],
              strand,
            };
          } else {
            current.points.push(points[index]);
          }
        }

        runs.push(current);
      });

      return runs;
    }

    function drawRibbonRun(run: RibbonRun) {
      if (run.front) {
        const averageDepth = run.points.reduce((sum, point) => sum + point.depth, 0) / run.points.length;
        const bandWidth = (15.5 + averageDepth * 5) * scale;
        const drawCenterline = () => {
          context.beginPath();
          context.moveTo(run.points[0].x, run.points[0].y);
          run.points.slice(1).forEach((point) => context.lineTo(point.x, point.y));
        };

        drawCenterline();
        context.globalAlpha = 0.96;
        context.lineCap = 'butt';
        context.lineJoin = 'round';
        context.lineWidth = bandWidth + 2.6 * scale;
        context.strokeStyle = run.strand === 1 ? colors.accentDeep : colors.muted;
        context.stroke();

        drawCenterline();
        context.lineWidth = bandWidth;
        context.strokeStyle = run.strand === 1 ? colors.fill : colors.fillMuted;
        context.stroke();
        return;
      }

      const edges = run.points.map((point, index, points) => {
        const before = points[Math.max(0, index - 1)];
        const after = points[Math.min(points.length - 1, index + 1)];
        const tangentX = after.x - before.x;
        const tangentY = after.y - before.y;
        const tangentLength = Math.hypot(tangentX, tangentY) || 1;
        const normalX = -tangentY / tangentLength;
        const normalY = tangentX / tangentLength;
        const halfWidth = (6.5 + point.depth * 5) * scale;

        return {
          left: { x: point.x + normalX * halfWidth, y: point.y + normalY * halfWidth },
          right: { x: point.x - normalX * halfWidth, y: point.y - normalY * halfWidth },
        };
      });

      context.beginPath();
      context.moveTo(edges[0].left.x, edges[0].left.y);
      edges.slice(1).forEach((edge) => context.lineTo(edge.left.x, edge.left.y));
      edges.slice().reverse().forEach((edge) => context.lineTo(edge.right.x, edge.right.y));
      context.closePath();
      context.globalAlpha = 0.78;
      context.fillStyle = run.strand === 1 ? colors.fill : colors.fillMuted;
      context.fill();
    }

    function drawRibbons(runs: RibbonRun[], front: boolean) {
      runs
        .filter((run) => run.front === front)
        .sort((left, right) => {
          const leftDepth = left.points.reduce((sum, point) => sum + point.depth, 0) / left.points.length;
          const rightDepth = right.points.reduce((sum, point) => sum + point.depth, 0) / right.points.length;
          return leftDepth - rightDepth;
        })
        .forEach(drawRibbonRun);
    }

    function draw() {
      if (!width || !height) return;
      const geometry = buildGeometry(rotation);
      const ribbons = buildRibbonRuns(geometry.strands);
      context.clearRect(0, 0, width, height);
      drawRibbons(ribbons, false);
      drawPairs(geometry.pairs);
      drawRibbons(ribbons, true);
      context.globalAlpha = 1;
    }

    function tick(time: number) {
      const elapsed = lastTime ? Math.min(32, time - lastTime) : 16;
      lastTime = time;
      if (follow) {
        rotation += (follow.target - rotation) * (1 - Math.exp(-elapsed / FOLLOW_EASE));
      } else {
        rotation += elapsed * 0.00015;
      }
      draw();
      frame = requestAnimationFrame(tick);
    }

    function stop() {
      cancelAnimationFrame(frame);
      frame = 0;
      lastTime = 0;
    }

    function start() {
      stop();
      draw();
      if (reducedMotion.matches || document.hidden || !exposed) return;
      frame = requestAnimationFrame(tick);
    }

    function resize() {
      const bounds = canvas.getBoundingClientRect();
      const nextWidth = Math.round(bounds.width);
      const nextHeight = Math.round(bounds.height);
      if (!nextWidth || !nextHeight || (nextWidth === width && nextHeight === height)) return;

      width = nextWidth;
      height = nextHeight;
      const deviceScale = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * deviceScale);
      canvas.height = Math.round(height * deviceScale);
      context.setTransform(deviceScale, 0, 0, deviceScale, 0, 0);
      radius = Math.min(height * 0.3, 56);
      scale = clamp((radius / 150) * 1.4, 0.35, 1);
      turns = axisLength() / (radius * TURN_LENGTH);
      pairCount = Math.max(1, Math.round(turns * PAIRS_PER_TURN));
      sampleCount = Math.max(2, Math.ceil(turns * SAMPLES_PER_TURN));
      draw();
    }

    // The footer is pinned under the page sheet, so it is in the viewport long before it is uncovered.
    function updateExposure() {
      const bounds = field.getBoundingClientRect();
      const coveredTo = sheet ? sheet.getBoundingClientRect().bottom : 0;
      const next = bounds.bottom > Math.max(0, coveredTo) && bounds.top < window.innerHeight;
      if (next === exposed) return;
      exposed = next;
      start();
    }

    function handlePointerMove(event: PointerEvent) {
      if (event.pointerType === 'touch') return;
      if (!follow) follow = { anchorRotation: rotation, anchorX: event.clientX, target: rotation };
      const travel = event.clientX - follow.anchorX;
      follow.target = follow.anchorRotation - (travel / (radius * TURN_LENGTH)) * Math.PI * 2;
      if (!frame) {
        rotation = follow.target;
        draw();
      }
    }

    function handlePointerLeave() {
      follow = null;
    }

    const resizeObserver = new ResizeObserver(() => {
      resize();
      updateExposure();
    });
    const themeObserver = new MutationObserver(() => {
      colors = readColors();
      draw();
    });

    resize();
    updateExposure();
    start();
    resizeObserver.observe(canvas);
    themeObserver.observe(document.documentElement, { attributeFilter: ['class'], attributes: true });
    reducedMotion.addEventListener('change', start);
    document.addEventListener('visibilitychange', start);
    window.addEventListener('scroll', updateExposure, { passive: true });
    field.addEventListener('pointermove', handlePointerMove);
    field.addEventListener('pointerleave', handlePointerLeave);

    return () => {
      stop();
      resizeObserver.disconnect();
      themeObserver.disconnect();
      reducedMotion.removeEventListener('change', start);
      document.removeEventListener('visibilitychange', start);
      window.removeEventListener('scroll', updateExposure);
      field.removeEventListener('pointermove', handlePointerMove);
      field.removeEventListener('pointerleave', handlePointerLeave);
    };
  }, []);

  return (
    <div aria-hidden="true" className="pc-footer-helix" ref={fieldRef}>
      <canvas ref={canvasRef} />
    </div>
  );
}
