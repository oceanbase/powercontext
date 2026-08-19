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

(() => {
  const field = document.querySelector("[data-pc-spiral]");
  const canvas = field?.querySelector("[data-pc-spiral-canvas]");
  const context = canvas?.getContext("2d", { alpha: true });
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  if (!field || !canvas || !context) {
    return;
  }

  let colors = {};
  let frame = 0;
  let height = 0;
  let lastTime = 0;
  let pairCount = 11;
  let radius = 150;
  let rotation = -0.48;
  let sampleCount = 144;
  let visualHeight = 360;
  let width = 0;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const readColors = () => {
    const styles = getComputedStyle(field);
    colors = {
      accent: styles.getPropertyValue("--spiral-accent").trim(),
      accentDeep: styles.getPropertyValue("--spiral-accent-deep").trim(),
      fill: styles.getPropertyValue("--spiral-fill").trim(),
      fillMuted: styles.getPropertyValue("--spiral-fill-muted").trim(),
      muted: styles.getPropertyValue("--spiral-muted").trim(),
      surface: styles.getPropertyValue("--spiral-surface").trim(),
    };
  };

  const project = (progress, strand, phase) => {
    const angle = progress * Math.PI * 4 + strand * Math.PI + phase;
    const localX = Math.cos(angle) * radius;
    const localZ = Math.sin(angle) * radius;
    const perspective = 500 / (500 + localZ);

    return {
      depth: clamp((1 - localZ / (radius * 1.12)) / 2, 0, 1),
      progress,
      x: width / 2 + (progress - 0.5) * 16 + localX * perspective,
      y: height / 2 + (progress - 0.5) * visualHeight + localZ * 0.08,
    };
  };

  const buildGeometry = (phase) => {
    const strands = [[], []];
    const pairs = [];

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
  };

  const drawPairs = (pairs) => {
    pairs.forEach((pair) => {
      const depth = (pair.first.depth + pair.second.depth) / 2;
      const midpoint = {
        x: (pair.first.x + pair.second.x) / 2,
        y: (pair.first.y + pair.second.y) / 2,
      };
      const lineWidth = 6.2 + depth * 2.4;

      context.beginPath();
      context.moveTo(pair.first.x, pair.first.y);
      context.lineTo(pair.second.x, pair.second.y);
      context.globalAlpha = 0.52 + depth * 0.22;
      context.lineCap = "round";
      context.lineWidth = lineWidth + 1.8;
      context.strokeStyle = colors.surface;
      context.stroke();

      context.beginPath();
      context.moveTo(pair.first.x, pair.first.y);
      context.lineTo(midpoint.x, midpoint.y);
      context.globalAlpha = 0.58 + depth * 0.2;
      context.lineCap = "butt";
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
  };

  const buildRibbonRuns = (strands) => {
    const runs = [];

    strands.forEach((points, strand) => {
      let current = {
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
  };

  const drawRibbonRun = (run) => {
    if (run.front) {
      const averageDepth = run.points.reduce((sum, point) => sum + point.depth, 0) / run.points.length;
      const bandWidth = 15.5 + averageDepth * 5;

      const drawCenterline = () => {
        context.beginPath();
        context.moveTo(run.points[0].x, run.points[0].y);
        run.points.slice(1).forEach((point) => context.lineTo(point.x, point.y));
      };

      drawCenterline();
      context.globalAlpha = 0.96;
      context.lineCap = "butt";
      context.lineJoin = "round";
      context.lineWidth = bandWidth + 2.6;
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
      const taper = clamp(Math.min(point.progress, 1 - point.progress) / 0.045, 0, 1);
      const halfWidth = (6.5 + point.depth * 5) * taper;

      return {
        left: { x: point.x + normalX * halfWidth, y: point.y + normalY * halfWidth },
        right: { x: point.x - normalX * halfWidth, y: point.y - normalY * halfWidth },
      };
    });

    context.beginPath();
    context.moveTo(edges[0].left.x, edges[0].left.y);
    edges.slice(1).forEach((edge) => context.lineTo(edge.left.x, edge.left.y));
    edges
      .slice()
      .reverse()
      .forEach((edge) => context.lineTo(edge.right.x, edge.right.y));
    context.closePath();
    context.globalAlpha = 0.78;
    context.fillStyle = run.strand === 1 ? colors.fill : colors.fillMuted;
    context.fill();
  };

  const drawRibbons = (runs, front) => {
    runs
      .filter((run) => run.front === front)
      .sort((left, right) => {
        const leftDepth = left.points.reduce((sum, point) => sum + point.depth, 0) / left.points.length;
        const rightDepth = right.points.reduce((sum, point) => sum + point.depth, 0) / right.points.length;
        return leftDepth - rightDepth;
      })
      .forEach(drawRibbonRun);
  };

  const render = (time, staticFrame = false) => {
    if (!width || !height) {
      return;
    }

    const elapsed = lastTime ? Math.min(32, time - lastTime) : 16;
    lastTime = time;
    if (!staticFrame) {
      rotation += elapsed * 0.00015;
    }

    const geometry = buildGeometry(rotation);
    const ribbons = buildRibbonRuns(geometry.strands);

    context.clearRect(0, 0, width, height);
    drawRibbons(ribbons, false);
    drawPairs(geometry.pairs);
    drawRibbons(ribbons, true);
    context.globalAlpha = 1;

    if (!staticFrame) {
      frame = requestAnimationFrame(render);
    }
  };

  const resize = () => {
    const bounds = canvas.getBoundingClientRect();
    const nextWidth = Math.round(bounds.width);
    const nextHeight = Math.round(bounds.height);
    if (!nextWidth || !nextHeight || (nextWidth === width && nextHeight === height)) {
      return;
    }

    width = nextWidth;
    height = nextHeight;
    const deviceScale = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * deviceScale);
    canvas.height = Math.round(height * deviceScale);
    context.setTransform(deviceScale, 0, 0, deviceScale, 0, 0);
    radius = Math.min(width * 0.31, 150);
    visualHeight = Math.min(height * 0.76, 360);
    pairCount = width < 360 ? 9 : 11;
    sampleCount = width < 360 ? 112 : 144;
    render(0, true);
  };

  const start = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    lastTime = 0;
    if (reducedMotion.matches) {
      render(0, true);
      return;
    }
    frame = requestAnimationFrame(render);
  };

  readColors();
  resize();
  start();

  new ResizeObserver(() => {
    resize();
    start();
  }).observe(canvas);

  reducedMotion.addEventListener("change", start);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelAnimationFrame(frame);
      frame = 0;
      return;
    }
    start();
  });
})();
