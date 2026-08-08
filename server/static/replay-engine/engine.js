// CS-Scout canvas replay engine core. One external clock drives every instance.
// Twenty seconds of game time are shown on a ten-second loop. Player trails are
// intentionally not persisted between frames.
(function() {
"use strict";

const PLAYBACK_S = 10;
const WINDOW_S = 20;
const DOT_R = 10;
const SIDE_COLOR = { CT: "#55b8ff", T: "#ffd166" };
const NADE_COLOR = {
  smoke: "#dddddd",
  flash: "#fff27a",
  he: "#ff6b6b",
  molotov: "#ff8c42",
  decoy: "#9aa0a6"
};
const NADE_R = { smoke: 90, molotov: 70 };
const NADE_ICON_SRC = {
  smoke: "smokegrenade.svg",
  flash: "flashbang.svg",
  he: "hegrenade.svg",
  molotov: { CT: "incgrenade.svg", T: "molotov_bottle.svg" }
};
const NADE_EFFECT_SRC = { smoke: "map_smoke.svg", molotov: "inferno.svg" };
const NADE_ICON_HEIGHT = 22;
const iconCache = new Map();
const tintedAssetCache = new Map();

function grenadeIcon(filename) {
  if (!filename || typeof Image === "undefined") return null;
  if (!iconCache.has(filename)) {
    const image = new Image();
    image.src = `/icons/${filename}`;
    iconCache.set(filename, image);
  }
  return iconCache.get(filename);
}

function tintedGrenadeAsset(filename, color) {
  const image = grenadeIcon(filename);
  if (!image || !image.complete || !image.naturalWidth || !image.naturalHeight) return null;
  if (typeof document === "undefined" || typeof document.createElement !== "function") return image;

  const cacheKey = `${filename}:${color}`;
  if (tintedAssetCache.has(cacheKey)) return tintedAssetCache.get(cacheKey);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) return image;
  ctx.drawImage(image, 0, 0);
  ctx.globalCompositeOperation = "source-in";
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.globalCompositeOperation = "source-over";
  tintedAssetCache.set(cacheKey, canvas);
  return canvas;
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function validSample(sample) {
  return Array.isArray(sample) && sample.length >= 3 &&
    finiteNumber(sample[0]) && finiteNumber(sample[1]) && finiteNumber(sample[2]);
}

/**
 * Create a replay renderer bound to a single canvas.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {object} options - { radar, transform, rounds, side, rtype }
 * @returns {{ drawAt, setFilter, toggleRound, destroy, _rounds, _interp, _velocityAt,
 *            _drawGrenade, _drawArrow, _drawX, _drawNadeIcon, _drawNadeEffect,
 *            _nadeIconSource, g2p, cv, ctx, transform, allRounds, side, rtype,
 *            disabled, destroyed, imgReady, imgFailed, img }}
 */
function createReplay(canvas, options = {}) {
  if (!canvas || typeof canvas.getContext !== "function") {
    throw new TypeError("createReplay requires a canvas element");
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
    img: new Image()
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

  function _rounds() {
    return state.allRounds.filter(round => round && round.side === state.side &&
      round.rtype === state.rtype && !state.disabled.has(round.round_id));
  }

  function setFilter(side, rtype) {
    state.side = side;
    state.rtype = rtype;
  }

  function toggleRound(roundId, enabled) {
    if (enabled) state.disabled.delete(roundId);
    else state.disabled.add(roundId);
  }

  // Interpolate a [[t,x,y,...], ...] series. Returns all elements from index 1
  // onwards (e.g. [x, y] or [x, y, yaw]). `holdLast` is useful for death
  // markers and grenade heads, while live players disappear after their final
  // position sample.
  function _interp(series, gameTime, holdLast = false) {
    if (!Array.isArray(series) || !finiteNumber(gameTime)) return null;
    let previous = null;
    for (const sample of series) {
      if (!validSample(sample)) continue;
      if (previous === null) {
        previous = sample;
        if (gameTime < sample[0]) return null;
        if (gameTime === sample[0]) return sample.slice(1);
        continue;
      }
      if (sample[0] <= previous[0]) {
        previous = sample;
        continue;
      }
      if (gameTime <= sample[0]) {
        const fraction = Math.max(0, Math.min(1,
          (gameTime - previous[0]) / (sample[0] - previous[0])));
        const result = [];
        for (let i = 1; i < sample.length; i++) {
          const prevVal = previous[i] || 0;
          const currVal = sample[i] || 0;
          result.push(prevVal + (currVal - prevVal) * fraction);
        }
        return result;
      }
      previous = sample;
    }
    if (previous && (holdLast || gameTime === previous[0])) return previous.slice(1);
    return null;
  }

  function _velocityAt(series, gameTime) {
    if (!Array.isArray(series) || !finiteNumber(gameTime)) return null;
    let previous = null;
    for (const sample of series) {
      if (!validSample(sample)) continue;
      if (previous === null) {
        previous = sample;
        continue;
      }
      if (sample[0] <= previous[0]) {
        previous = sample;
        continue;
      }
      if (gameTime <= sample[0]) {
        return [sample[1] - previous[1], sample[2] - previous[2]];
      }
      previous = sample;
    }
    return null;
  }

  function drawAt(gameTime) {
    if (state.destroyed || !finiteNumber(gameTime)) return;
    const c = state.ctx;
    c.clearRect(0, 0, state.cv.width, state.cv.height);
    if (state.imgReady) {
      c.drawImage(state.img, 0, 0, state.cv.width, state.cv.height);
    } else {
      c.fillStyle = "#11141e";
      c.fillRect(0, 0, state.cv.width, state.cv.height);
      if (state.imgFailed) {
        c.save();
        c.fillStyle = "#ff7777";
        c.font = "600 16px system-ui, sans-serif";
        c.textAlign = "center";
        c.textBaseline = "middle";
        c.fillText("雷达图加载失败", state.cv.width / 2, state.cv.height / 2);
        c.restore();
        return;
      }
    }

    for (const round of _rounds()) {
      const color = typeof round.color === "string" && round.color ?
        round.color : (SIDE_COLOR[state.side] || SIDE_COLOR.CT);
      for (const grenade of (Array.isArray(round.grenades) ? round.grenades : [])) {
        _drawGrenade(grenade, gameTime);
      }

      const path = Array.isArray(round.path) ? round.path : [];
      if (finiteNumber(round.death_t) && gameTime >= round.death_t) {
        const death = _interp(path, round.death_t, true);
        const deathPixel = death && g2p(death[0], death[1]);
        if (deathPixel) _drawX(deathPixel, color);
        continue;
      }

      const position = _interp(path, gameTime);
      const pixel = position && g2p(position[0], position[1]);
      if (!pixel) continue;
      // Use real view yaw when available (sample[3] in [t, x, y, yaw]).
      // Empirically: yaw ≈ atan2(dy, dx) in game coords. Radar flips Y,
      // so canvas_angle = atan2(-dy, dx) = -atan2(dy, dx) = -yaw.
      let arrowAngle = null;
      if (position.length >= 3 && finiteNumber(position[2])) {
        arrowAngle = -position[2] * Math.PI / 180;
      } else {
        const velocity = _velocityAt(path, gameTime);
        if (velocity) {
          const scale = state.transform.scale;
          const vx = velocity[0] / scale;
          const vy = -velocity[1] / scale;
          if (finiteNumber(vx) && finiteNumber(vy) && Math.hypot(vx, vy) > 0.5) {
            arrowAngle = Math.atan2(vy, vx);
          }
        }
      }
      if (arrowAngle !== null) _drawArrow(pixel[0], pixel[1], arrowAngle, color);
      c.save();
      c.globalAlpha = 0.86;
      c.fillStyle = color;
      c.beginPath();
      c.arc(pixel[0], pixel[1], DOT_R, 0, Math.PI * 2);
      c.fill();
      c.restore();
    }
  }

  function _drawGrenade(grenade, gameTime) {
    if (!grenade || !finiteNumber(grenade.throw_t) || !finiteNumber(grenade.land_t)) return;
    const type = grenade.type;
    const color = NADE_COLOR[type] || "#ffffff";
    if (gameTime >= grenade.throw_t && gameTime < grenade.land_t) {
      const arc = Array.isArray(grenade.arc) ? grenade.arc : [];
      const c = state.ctx;
      let started = false;
      c.save();
      c.strokeStyle = color;
      c.lineWidth = 1.5;
      c.beginPath();
      for (const sample of arc) {
        if (!validSample(sample) || sample[0] > gameTime) continue;
        const pixel = g2p(sample[1], sample[2]);
        if (!pixel) continue;
        if (started) c.lineTo(pixel[0], pixel[1]);
        else {
          c.moveTo(pixel[0], pixel[1]);
          started = true;
        }
      }
      const head = _interp(arc, gameTime, true);
      const headPixel = head && g2p(head[0], head[1]);
      if (headPixel) {
        if (started) c.lineTo(headPixel[0], headPixel[1]);
        else {
          c.moveTo(headPixel[0], headPixel[1]);
          started = true;
        }
      }
      if (started) c.stroke();
      c.restore();

      if (headPixel) _drawNadeIcon(type, headPixel, color);
      return;
    }

    if (!finiteNumber(grenade.expire_t) || gameTime < grenade.land_t || gameTime >= grenade.expire_t ||
        !Array.isArray(grenade.land) || grenade.land.length < 2) return;
    const landing = g2p(grenade.land[0], grenade.land[1]);
    if (!landing) return;
    const c = state.ctx;
    c.save();
    let radius = null;
    if (NADE_R[type] && finiteNumber(state.transform.scale) && state.transform.scale !== 0) {
      radius = NADE_R[type] / Math.abs(state.transform.scale);
      c.globalAlpha = 0.28;
      c.fillStyle = color;
      c.beginPath();
      c.arc(landing[0], landing[1], radius, 0, Math.PI * 2);
      c.fill();
      c.globalAlpha = 1;
    }
    if (radius) _drawNadeEffect(type, landing, radius, color);
    c.fillStyle = color;
    c.beginPath();
    c.arc(landing[0], landing[1], 4, 0, Math.PI * 2);
    c.fill();
    c.restore();
  }

  function _nadeIconSource(type) {
    const source = NADE_ICON_SRC[type];
    if (type !== "molotov") return source;
    return source[state.side] || source.T;
  }

  function _drawNadeIcon(type, point, color) {
    // There is deliberately no decoy entry in NADE_ICON_SRC.
    const asset = tintedGrenadeAsset(_nadeIconSource(type), color);
    if (!asset || !asset.width || !asset.height) return;
    const width = NADE_ICON_HEIGHT * asset.width / asset.height;
    const c = state.ctx;
    c.save();
    c.shadowColor = "rgba(0,0,0,.9)";
    c.shadowBlur = 4;
    c.shadowOffsetX = 1;
    c.shadowOffsetY = 1;
    c.drawImage(asset, point[0] - width / 2, point[1] - NADE_ICON_HEIGHT / 2,
      width, NADE_ICON_HEIGHT);
    c.restore();
  }

  function _drawNadeEffect(type, point, radius, color) {
    const asset = tintedGrenadeAsset(NADE_EFFECT_SRC[type], color);
    if (!asset || !asset.width || !asset.height || !finiteNumber(radius) || radius <= 0) return;
    const maxSize = radius * 2;
    const scale = maxSize / Math.max(asset.width, asset.height);
    const width = asset.width * scale;
    const height = asset.height * scale;
    const c = state.ctx;
    c.save();
    c.globalAlpha = type === "smoke" ? 0.72 : 0.82;
    c.shadowColor = "rgba(0,0,0,.65)";
    c.shadowBlur = 3;
    c.drawImage(asset, point[0] - width / 2, point[1] - height / 2, width, height);
    c.restore();
  }

  function _drawArrow(x, y, angle, color) {
    const c = state.ctx;
    const radius = DOT_R + 8;
    c.save();
    c.translate(x, y);
    c.rotate(angle);
    c.globalAlpha = 0.95;
    c.fillStyle = color;
    c.beginPath();
    c.moveTo(radius, 0);
    c.lineTo(radius - 9, -6);
    c.lineTo(radius - 9, 6);
    c.closePath();
    c.fill();
    c.restore();
  }

  function _drawX(point, color) {
    const c = state.ctx;
    const size = DOT_R * 0.85;
    c.save();
    c.globalAlpha = 0.92;
    c.strokeStyle = color;
    c.lineWidth = 2;
    c.beginPath();
    c.moveTo(point[0] - size, point[1] - size);
    c.lineTo(point[0] + size, point[1] + size);
    c.moveTo(point[0] + size, point[1] - size);
    c.lineTo(point[0] - size, point[1] + size);
    c.stroke();
    c.restore();
  }

  return {
    drawAt,
    setFilter,
    toggleRound,
    destroy,
    // Exposed for testing parity with the old class-based API
    _rounds,
    _interp,
    _velocityAt,
    _drawGrenade,
    _drawArrow,
    _drawX,
    _drawNadeIcon,
    _drawNadeEffect,
    _nadeIconSource,
    g2p,
    // State accessors for backward compatibility with class property access
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

const _exports = {
  createReplay,
  PLAYBACK_S,
  WINDOW_S,
  SIDE_COLOR,
  NADE_COLOR,
  NADE_R,
  NADE_ICON_SRC,
  NADE_EFFECT_SRC,
  NADE_ICON_HEIGHT,
  grenadeIcon,
  tintedGrenadeAsset,
  finiteNumber,
  validSample
};

if (typeof module !== "undefined") {
  module.exports = _exports;
}
if (typeof window !== "undefined") {
  window.__replayEngine = window.__replayEngine || {};
  Object.assign(window.__replayEngine, _exports);
}
})();
