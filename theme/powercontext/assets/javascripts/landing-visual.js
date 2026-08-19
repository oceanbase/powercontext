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
  const field = document.querySelector("[data-pc-context-field]");
  const canvas = field?.querySelector("[data-pc-context-canvas]");
  const context = canvas?.getContext("2d", { alpha: true });
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const precisePointer = window.matchMedia("(hover: hover) and (pointer: fine)");

  if (!field || !canvas || !context) {
    return;
  }

  const pointer = { active: false, x: -1000, y: -1000 };
  const pairFocus = [];
  let colors = {};
  let deviceScale = 1;
  let frame = 0;
  let height = 0;
  let lastTime = 0;
  let pairCount = 17;
  let radius = 126;
  let rotation = -0.55;
  let sampleCount = 144;
  let visualHeight = 380;
  let width = 0;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const readColors = () => {
    const styles = getComputedStyle(field);
    colors = {
      accent: styles.getPropertyValue("--pc-accent").trim(),
      artifactFill: styles.getPropertyValue("--pc-accent-border").trim(),
      accentHover: styles.getPropertyValue("--pc-accent-hover").trim(),
      ink: styles.getPropertyValue("--pc-ink").trim(),
      muted: styles.getPropertyValue("--pc-muted").trim(),
      rule: styles.getPropertyValue("--pc-rule-strong").trim(),
      sourceFill: styles.getPropertyValue("--pc-surface-secondary").trim(),
      surface: styles.getPropertyValue("--pc-surface").trim(),
    };
  };

  const project = (progress, strand, phase) => {
    const angle = progress * Math.PI * 4.8 + strand * Math.PI + phase;
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
        artifact: project(progress, 1, phase),
        focus: pairFocus[index] || 0,
        index,
        source: project(progress, 0, phase),
      });
    }

    return { pairs, strands };
  };

  const distanceToSegment = (point, from, to) => {
    const lineX = to.x - from.x;
    const lineY = to.y - from.y;
    const lengthSquared = lineX * lineX + lineY * lineY;
    if (!lengthSquared) {
      return Math.hypot(point.x - from.x, point.y - from.y);
    }

    const progress = clamp(((point.x - from.x) * lineX + (point.y - from.y) * lineY) / lengthSquared, 0, 1);
    return Math.hypot(point.x - (from.x + lineX * progress), point.y - (from.y + lineY * progress));
  };

  const findActivePair = (pairs) => {
    if (!pointer.active) {
      return Math.floor(pairCount / 2);
    }

    return pairs.reduce(
      (active, pair) => {
        const distance = distanceToSegment(pointer, pair.source, pair.artifact);
        return distance < active.distance ? { distance, index: pair.index } : active;
      },
      { distance: Number.POSITIVE_INFINITY, index: 0 },
    ).index;
  };

  const drawPairs = (pairs) => {
    pairs.forEach((pair) => {
      const depth = (pair.source.depth + pair.artifact.depth) / 2;
      const midpoint = {
        x: (pair.source.x + pair.artifact.x) / 2,
        y: (pair.source.y + pair.artifact.y) / 2,
      };
      const lineWidth = 5.2 + depth * 2.1 + pair.focus * 1.2;
      const alpha = 0.44 + depth * 0.3 + pair.focus * 0.22;

      context.beginPath();
      context.moveTo(pair.source.x, pair.source.y);
      context.lineTo(pair.artifact.x, pair.artifact.y);
      context.globalAlpha = 0.46 + depth * 0.24;
      context.lineCap = "round";
      context.lineWidth = lineWidth + 1.6;
      context.strokeStyle = colors.surface;
      context.stroke();

      context.beginPath();
      context.moveTo(pair.source.x, pair.source.y);
      context.lineTo(midpoint.x, midpoint.y);
      context.globalAlpha = alpha;
      context.lineCap = "butt";
      context.lineWidth = lineWidth;
      context.strokeStyle = colors.muted;
      context.stroke();

      context.beginPath();
      context.moveTo(midpoint.x, midpoint.y);
      context.lineTo(pair.artifact.x, pair.artifact.y);
      context.globalAlpha = alpha + 0.08;
      context.lineWidth = lineWidth;
      context.strokeStyle = colors.accent;
      context.stroke();

      context.globalAlpha = 0.52 + pair.focus * 0.38;
      context.fillStyle = pair.focus > 0.08 ? colors.accentHover : colors.rule;
      context.beginPath();
      context.arc(midpoint.x, midpoint.y, 1.2 + pair.focus * 0.8, 0, Math.PI * 2);
      context.fill();
    });
  };

  const buildRibbonRuns = (strands) => {
    const runs = [];

    strands.forEach((points, strand) => {
      let current = {
        front: points[0].depth >= 0.5,
        points: [points[0]],
        role: strand === 0 ? "source" : "artifact",
      };

      for (let index = 1; index < points.length; index += 1) {
        const front = points[index].depth >= 0.5;
        if (front !== current.front) {
          current.points.push(points[index]);
          runs.push(current);
          current = {
            front,
            points: [points[index - 1], points[index]],
            role: current.role,
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
      const visiblePoints = run.points;
      const averageDepth = visiblePoints.reduce((sum, point) => sum + point.depth, 0) / visiblePoints.length;
      const bandWidth = 13.2 + averageDepth * 4.2;
      const drawCenterline = () => {
        context.beginPath();
        context.moveTo(visiblePoints[0].x, visiblePoints[0].y);
        visiblePoints.slice(1).forEach((point) => context.lineTo(point.x, point.y));
      };

      drawCenterline();
      context.globalAlpha = 0.98;
      context.lineCap = "butt";
      context.lineJoin = "round";
      context.lineWidth = bandWidth + 3;
      context.strokeStyle = run.role === "artifact" ? colors.accentHover : colors.muted;
      context.stroke();

      drawCenterline();
      context.lineWidth = bandWidth;
      context.strokeStyle = run.role === "artifact" ? colors.artifactFill : colors.sourceFill;
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
      const halfWidth = (5.2 + point.depth * 4.3) * taper;

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
    context.globalAlpha = run.front ? 0.98 : 0.72;
    context.fillStyle = run.role === "artifact" ? colors.artifactFill : colors.sourceFill;
    context.fill();

    context.globalAlpha = run.front ? 0.96 : 0.62;
    context.strokeStyle = run.role === "artifact" ? colors.accentHover : colors.muted;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.lineWidth = run.front ? 1.8 : 1.25;
    ["left", "right"].forEach((side) => {
      context.beginPath();
      context.moveTo(edges[0][side].x, edges[0][side].y);
      edges.slice(1).forEach((edge) => context.lineTo(edge[side].x, edge[side].y));
      context.stroke();
    });
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

  const drawSquare = (point, size, fill, stroke, lineWidth = 1) => {
    if (fill) {
      context.fillStyle = fill;
      context.fillRect(point.x - size / 2, point.y - size / 2, size, size);
    }
    if (stroke) {
      context.strokeStyle = stroke;
      context.lineWidth = lineWidth;
      context.strokeRect(point.x - size / 2, point.y - size / 2, size, size);
    }
  };

  const drawActiveMapping = (pair, time, staticFrame) => {
    if (!pair || pair.focus < 0.04) {
      return;
    }

    const lineX = pair.artifact.x - pair.source.x;
    const lineY = pair.artifact.y - pair.source.y;
    const length = Math.hypot(lineX, lineY) || 1;
    const directionX = lineX / length;
    const directionY = lineY / length;
    const normalX = -directionY;
    const normalY = directionX;
    const progress = staticFrame ? 0.58 : (time % 2100) / 2100;
    const signal = {
      x: pair.source.x + lineX * progress,
      y: pair.source.y + lineY * progress,
    };

    context.globalAlpha = pair.focus;
    context.save();
    context.translate(signal.x, signal.y);
    context.rotate(Math.atan2(lineY, lineX));
    context.fillStyle = colors.accentHover;
    context.beginPath();
    context.moveTo(4, 0);
    context.lineTo(-3, -2.8);
    context.lineTo(-3, 2.8);
    context.closePath();
    context.fill();
    context.restore();

    context.globalAlpha = pair.focus * 0.96;
    drawSquare(pair.source, 8.5, colors.surface, colors.muted, 2);
    drawSquare(pair.artifact, 8.5, colors.accent, null);

    const artifactOffset = 19 + pair.focus * 3;
    const satelliteSize = 5 + pair.focus;
    const branchStart = {
      x: pair.artifact.x + directionX * 4,
      y: pair.artifact.y + directionY * 4,
    };
    const upperArtifact = {
      x: pair.artifact.x + directionX * artifactOffset + normalX * 9,
      y: pair.artifact.y + directionY * artifactOffset + normalY * 9,
    };
    const lowerArtifact = {
      x: pair.artifact.x + directionX * artifactOffset - normalX * 9,
      y: pair.artifact.y + directionY * artifactOffset - normalY * 9,
    };
    const artifactLabelAnchor = {
      x: pair.artifact.x + directionX * (artifactOffset + satelliteSize / 2),
      y: pair.artifact.y + directionY * (artifactOffset + satelliteSize / 2),
    };

    context.beginPath();
    context.moveTo(branchStart.x, branchStart.y);
    context.lineTo(upperArtifact.x, upperArtifact.y);
    context.moveTo(branchStart.x, branchStart.y);
    context.lineTo(lowerArtifact.x, lowerArtifact.y);
    context.globalAlpha = pair.focus * 0.68;
    context.lineCap = "round";
    context.lineWidth = 2;
    context.strokeStyle = colors.accent;
    context.stroke();

    context.globalAlpha = pair.focus * 0.8;
    drawSquare(upperArtifact, satelliteSize, colors.accent, null);
    context.globalAlpha = pair.focus * 0.56;
    drawSquare(lowerArtifact, satelliteSize - 0.5, colors.accent, null);

    const drawLabel = (label, point, color, yOffset) => {
      const onLeft = point.x < width / 2;
      const labelX = onLeft ? 14 : width - 14;
      const leaderX = onLeft ? 64 : width - 64;
      const labelY = point.y + yOffset;

      context.beginPath();
      context.moveTo(point.x, point.y);
      context.lineTo(leaderX, labelY);
      context.lineTo(onLeft ? 56 : width - 56, labelY);
      context.globalAlpha = pair.focus * 0.42;
      context.lineWidth = 1;
      context.strokeStyle = color;
      context.stroke();

      context.globalAlpha = pair.focus * 0.86;
      context.fillStyle = color;
      context.textAlign = onLeft ? "left" : "right";
      context.fillText(label, labelX, labelY);
    };

    context.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    context.textBaseline = "middle";
    drawLabel("Source", pair.source, colors.muted, -18);
    drawLabel("Artifacts", artifactLabelAnchor, colors.accentHover, 18);
  };

  const render = (time, staticFrame = false) => {
    if (!width || !height) {
      return;
    }

    const elapsed = lastTime ? Math.min(32, time - lastTime) : 16;
    lastTime = time;
    if (!staticFrame) {
      rotation += elapsed * 0.00022;
    }

    const geometry = buildGeometry(rotation);
    const ribbons = buildRibbonRuns(geometry.strands);
    const activeIndex = staticFrame ? Math.floor(pairCount / 2) : findActivePair(geometry.pairs);
    geometry.pairs.forEach((pair, index) => {
      const target = index === activeIndex ? 1 : 0;
      pairFocus[index] = staticFrame ? target : (pairFocus[index] || 0) + (target - (pairFocus[index] || 0)) * 0.12;
      pair.focus = pairFocus[index];
    });

    context.clearRect(0, 0, width, height);
    drawRibbons(ribbons, false);
    drawPairs(geometry.pairs);
    drawRibbons(ribbons, true);
    drawActiveMapping(geometry.pairs[activeIndex], time, staticFrame);
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
    deviceScale = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * deviceScale);
    canvas.height = Math.round(height * deviceScale);
    context.setTransform(deviceScale, 0, 0, deviceScale, 0, 0);
    radius = Math.min(width * 0.29, 126);
    visualHeight = Math.min(height * 0.76, 380);
    pairCount = width < 360 ? 13 : 17;
    sampleCount = width < 360 ? 112 : 144;
    pairFocus.length = pairCount;
    pairFocus.fill(0);

    if (reducedMotion.matches) {
      render(0, true);
    }
  };

  const updatePointer = (event) => {
    const bounds = canvas.getBoundingClientRect();
    pointer.x = event.clientX - bounds.left;
    pointer.y = event.clientY - bounds.top;
    pointer.active = pointer.x >= 0 && pointer.x <= bounds.width && pointer.y >= 0 && pointer.y <= bounds.height;

    const normalizedX = pointer.x / bounds.width - 0.5;
    const normalizedY = pointer.y / bounds.height - 0.5;
    field.style.setProperty("--pc-field-rotate-x", `${(normalizedY * -2.2).toFixed(2)}deg`);
    field.style.setProperty("--pc-field-rotate-y", `${(normalizedX * 3).toFixed(2)}deg`);
  };

  const resetPointer = () => {
    pointer.active = false;
    pointer.x = -1000;
    pointer.y = -1000;
    field.style.removeProperty("--pc-field-rotate-x");
    field.style.removeProperty("--pc-field-rotate-y");
  };

  readColors();
  resize();

  if (!reducedMotion.matches) {
    frame = requestAnimationFrame(render);
  }

  if (!reducedMotion.matches && precisePointer.matches) {
    field.addEventListener("pointermove", updatePointer, { passive: true });
    field.addEventListener("pointerleave", resetPointer);
  }

  new ResizeObserver(resize).observe(canvas);
  new MutationObserver(() => {
    readColors();
    if (reducedMotion.matches) {
      render(0, true);
    }
  }).observe(document.body, { attributeFilter: ["data-md-color-scheme"], attributes: true });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelAnimationFrame(frame);
      frame = 0;
      return;
    }
    if (!reducedMotion.matches && !frame) {
      lastTime = 0;
      frame = requestAnimationFrame(render);
    }
  });
})();
