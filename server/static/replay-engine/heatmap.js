// CS-Scout heatmap overlay engine. Renders position density on the radar
// canvas. Two usage patterns:
//   1. Per-player: pass one player's rounds to visualise their tendencies.
//   2. All-players: pass a shared mutable array that accumulates every
//      player's rounds — call markDirty() after appending.
// The heatmap is density-based and static: drawAt(gameTime) ignores the
// game time parameter but accepts it for clock/view-manager compatibility.
"use strict";

const _engine = typeof require === "function"
  ? require("./engine")
  : (typeof window !== "undefined" && window.__replayEngine ? window.__replayEngine : {});
const finiteNumber = _engine.finiteNumber || (v => typeof v === "number" && Number.isFinite(v));
const validSample = _engine.validSample || (s =>
  Array.isArray(s) && s.length >= 3 && finiteNumber(s[0]) && finiteNumber(s[1]) && finiteNumber(s[2]));

// Radius of each density blob in canvas pixels.
const HEATMAP_RADIUS = 25;
// Minimum pixel distance between consecutive samples to avoid over-sampling
// slow or stationary movement.
const MIN_POINT_DISTANCE = 3;

// Colour gradient: [position, r, g, b, a]
const HEATMAP_GRADIENT = [
  [0.00,   0,   0,   0,   0],
  [0.10,   0,   0, 180,  60],
  [0.25,   0,  60, 255, 110],
  [0.45,   0, 200, 180, 150],
  [0.60, 180, 255,   0, 175],
  [0.75, 255, 220,   0, 200],
  [0.90, 255, 120,   0, 220],
  [1.00, 255,  30,   0, 240]
];

/**
 * Map a normalised density value [0, 1] to an [r, g, b, a] tuple.
 * Pure function — exported for unit testing.
 */
function heatColor(t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 1; i < HEATMAP_GRADIENT.length; i++) {
    if (t <= HEATMAP_GRADIENT[i][0]) {
      const prev = HEATMAP_GRADIENT[i - 1];
      const curr = HEATMAP_GRADIENT[i];
      const range = curr[0] - prev[0];
      if (range === 0) return [curr[1], curr[2], curr[3], curr[4]];
      const f = (t - prev[0]) / range;
      return [
        Math.round(prev[1] + (curr[1] - prev[1]) * f),
        Math.round(prev[2] + (curr[2] - prev[2]) * f),
        Math.round(prev[3] + (curr[3] - prev[3]) * f),
        Math.round(prev[4] + (curr[4] - prev[4]) * f)
      ];
    }
  }
  const last = HEATMAP_GRADIENT[HEATMAP_GRADIENT.length - 1];
  return [last[1], last[2], last[3], last[4]];
}

/**
 * Create a heatmap renderer bound to a single canvas.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {object} options - { radar, transform, rounds, side, rtype }
 * @returns {{ drawAt, setFilter, toggleRound, markDirty, destroy,
 *            _filteredRounds, _collectPoints, heatColor,
 *            cv, ctx, transform, allRounds, side, rtype, disabled,
 *            destroyed, imgReady, imgFailed, img }}
 */
function createHeatmap(canvas, options = {}) {
  if (!canvas || typeof canvas.getContext !== "function") {
    throw new TypeError("createHeatmap requires a canvas element");
  }
  const cv = canvas;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas is not available");

  const state = {
    cv,
    ctx,
    transform: options.transform || {},
    allRounds: Array.isArray(options.rounds) ? options.rounds : [],
    side: options.side || "CT",
    rtype: options.rtype || "Buy",
    disabled: new Set(),
    destroyed: false,
    imgReady: false,
    imgFailed: false,
    img: new Image(),
    densityCanvas: null,
    densityPoints: null,
    densityDirty: true
  };

  state.img.onload = () => {
    if (state.destroyed) return;
    const width = state.img.naturalWidth || state.img.width;
    const height = state.img.naturalHeight || state.img.height;
    if (width > 0 && height > 0) {
      cv.width = width;
      cv.height = height;
      state.imgReady = true;
    }
  };
  state.img.onerror = () => {
    if (!state.destroyed) state.imgFailed = true;
  };
  if (typeof options.radar === "string" && options.radar) state.img.src = options.radar;
  else state.imgFailed = true;

  function destroy() {
    state.destroyed = true;
    state.img.onload = null;
    state.img.onerror = null;
  }

  function g2p(x, y) {
    const transform = state.transform || {};
    const scale = transform.scale;
    if (!finiteNumber(x) || !finiteNumber(y) || !finiteNumber(transform.pos_x) ||
        !finiteNumber(transform.pos_y) || !finiteNumber(scale) || scale === 0) return null;
    const px = (x - transform.pos_x) / scale;
    const py = (transform.pos_y - y) / scale;
    return finiteNumber(px) && finiteNumber(py) ? [px, py] : null;
  }

  function _filteredRounds() {
    return state.allRounds.filter(round => round && round.side === state.side &&
      round.rtype === state.rtype && !state.disabled.has(round.round_id));
  }

  function setFilter(side, rtype) {
    state.side = side;
    state.rtype = rtype;
    state.densityDirty = true;
  }

  function toggleRound(roundId, enabled) {
    if (enabled) state.disabled.delete(roundId);
    else state.disabled.add(roundId);
    state.densityDirty = true;
  }

  function markDirty() {
    state.densityDirty = true;
  }

  /**
   * Collect pixel-space position samples from all filtered rounds.
   * Consecutive samples closer than MIN_POINT_DISTANCE are skipped to
   * avoid over-representing stationary periods.
   */
  function _collectPoints() {
    const points = [];
    for (const round of _filteredRounds()) {
      const path = Array.isArray(round.path) ? round.path : [];
      let prevX = null;
      let prevY = null;
      for (const sample of path) {
        if (!validSample(sample)) continue;
        const pixel = g2p(sample[1], sample[2]);
        if (!pixel) continue;
        if (prevX !== null && Math.hypot(pixel[0] - prevX, pixel[1] - prevY) < MIN_POINT_DISTANCE) continue;
        prevX = pixel[0];
        prevY = pixel[1];
        points.push(pixel);
      }
    }
    return points;
  }

  /**
   * Build an off-screen canvas containing the colour-mapped density image.
   * Returns null when document is unavailable (e.g. Node test env) — caller
   * should fall back to _drawFallback.
   */
  function _buildDensityCanvas(points) {
    if (typeof document === "undefined" || typeof document.createElement !== "function") return null;
    const w = cv.width;
    const h = cv.height;
    if (w <= 0 || h <= 0) return null;
    const off = document.createElement("canvas");
    off.width = w;
    off.height = h;
    const offCtx = off.getContext("2d");
    if (!offCtx) return null;

    // Accumulate white radial gradients additively.
    offCtx.globalCompositeOperation = "lighter";
    for (const [x, y] of points) {
      const grad = offCtx.createRadialGradient(x, y, 0, x, y, HEATMAP_RADIUS);
      grad.addColorStop(0, "rgba(255,255,255,0.25)");
      grad.addColorStop(1, "rgba(255,255,255,0)");
      offCtx.fillStyle = grad;
      offCtx.beginPath();
      offCtx.arc(x, y, HEATMAP_RADIUS, 0, Math.PI * 2);
      offCtx.fill();
    }

    // Map accumulated alpha to heatmap colours.
    try {
      const imageData = offCtx.getImageData(0, 0, w, h);
      const data = imageData.data;
      let maxAlpha = 0;
      for (let i = 3; i < data.length; i += 4) {
        if (data[i] > maxAlpha) maxAlpha = data[i];
      }
      if (maxAlpha > 0) {
        for (let i = 0; i < data.length; i += 4) {
          const alpha = data[i + 3];
          if (alpha === 0) continue;
          const [r, g, b, a] = heatColor(alpha / maxAlpha);
          data[i] = r;
          data[i + 1] = g;
          data[i + 2] = b;
          data[i + 3] = a;
        }
        offCtx.putImageData(imageData, 0, 0);
      }
    } catch (_e) {
      // getImageData not available — leave the white gradient as-is.
    }
    return off;
  }

  /**
   * Fallback renderer for environments without off-screen canvas support.
   * Draws simple additive circles directly on the main context.
   */
  function _drawFallback(c, points) {
    c.save();
    c.globalCompositeOperation = "lighter";
    for (const [x, y] of points) {
      const grad = c.createRadialGradient(x, y, 0, x, y, HEATMAP_RADIUS);
      grad.addColorStop(0, "rgba(255,120,0,0.28)");
      grad.addColorStop(1, "rgba(255,0,0,0)");
      c.fillStyle = grad;
      c.beginPath();
      c.arc(x, y, HEATMAP_RADIUS, 0, Math.PI * 2);
      c.fill();
    }
    c.restore();
  }

  function drawAt(gameTime) {
    if (state.destroyed) return;
    const c = state.ctx;
    c.clearRect(0, 0, cv.width, cv.height);

    // Radar background — same behaviour as createReplay.
    if (state.imgReady) {
      c.drawImage(state.img, 0, 0, cv.width, cv.height);
    } else {
      c.fillStyle = "#11141e";
      c.fillRect(0, 0, cv.width, cv.height);
      if (state.imgFailed) {
        c.save();
        c.fillStyle = "#ff7777";
        c.font = "600 16px system-ui, sans-serif";
        c.textAlign = "center";
        c.textBaseline = "middle";
        c.fillText("雷达图加载失败", cv.width / 2, cv.height / 2);
        c.restore();
        return;
      }
    }

    // Rebuild density map when dirty.
    if (state.densityDirty) {
      const points = _collectPoints();
      if (points.length > 0) {
        state.densityCanvas = _buildDensityCanvas(points);
        state.densityPoints = state.densityCanvas ? null : points;
      } else {
        state.densityCanvas = null;
        state.densityPoints = null;
      }
      state.densityDirty = false;
    }

    if (state.densityCanvas) {
      c.drawImage(state.densityCanvas, 0, 0);
    } else if (state.densityPoints && state.densityPoints.length > 0) {
      _drawFallback(c, state.densityPoints);
    }
  }

  return {
    drawAt,
    setFilter,
    toggleRound,
    markDirty,
    destroy,
    _filteredRounds,
    _collectPoints,
    heatColor,
    g2p,
    get cv() { return state.cv; },
    get ctx() { return state.ctx; },
    get transform() { return state.transform; },
    set transform(v) { state.transform = v; },
    get allRounds() { return state.allRounds; },
    get side() { return state.side; },
    set side(v) { state.side = v; },
    get rtype() { return state.rtype; },
    set rtype(v) { state.rtype = v; },
    get disabled() { return state.disabled; },
    get destroyed() { return state.destroyed; },
    get imgReady() { return state.imgReady; },
    set imgReady(v) { state.imgReady = v; },
    get imgFailed() { return state.imgFailed; },
    set imgFailed(v) { state.imgFailed = v; },
    get img() { return state.img; }
  };
}

const _exports = { createHeatmap, heatColor, HEATMAP_RADIUS, HEATMAP_GRADIENT };

if (typeof module !== "undefined") {
  module.exports = _exports;
}
if (typeof window !== "undefined") {
  window.__replayEngine = window.__replayEngine || {};
  Object.assign(window.__replayEngine, _exports);
}
