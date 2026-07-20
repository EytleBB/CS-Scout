// CS-Scout canvas replay engine. One external clock drives every instance.
// Twenty seconds of game time are shown on a ten-second loop. Player trails are
// intentionally not persisted between frames.
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

class ReplayPlayer {
  constructor(canvas, options = {}) {
    if (!canvas || typeof canvas.getContext !== "function") {
      throw new TypeError("ReplayPlayer requires a canvas element");
    }
    this.cv = canvas;
    this.ctx = canvas.getContext("2d");
    if (!this.ctx) throw new Error("2D canvas is not available");
    this.transform = options.transform || {};
    this.allRounds = Array.isArray(options.rounds) ? options.rounds : [];
    this.side = options.side || "CT";
    this.rtype = options.rtype || "Buy";
    this.disabled = new Set();
    this.destroyed = false;
    this.imgReady = false;
    this.imgFailed = false;
    this.img = new Image();
    this.img.onload = () => {
      if (this.destroyed) return;
      const width = this.img.naturalWidth || this.img.width;
      const height = this.img.naturalHeight || this.img.height;
      if (width > 0 && height > 0) {
        this.cv.width = width;
        this.cv.height = height;
        this.imgReady = true;
      }
    };
    this.img.onerror = () => {
      if (!this.destroyed) this.imgFailed = true;
    };
    if (typeof options.radar === "string" && options.radar) this.img.src = options.radar;
    else this.imgFailed = true;
  }

  destroy() {
    this.destroyed = true;
    this.img.onload = null;
    this.img.onerror = null;
  }

  g2p(x, y) {
    const transform = this.transform || {};
    const scale = transform.scale;
    if (!finiteNumber(x) || !finiteNumber(y) || !finiteNumber(transform.pos_x) ||
        !finiteNumber(transform.pos_y) || !finiteNumber(scale) || scale === 0) return null;
    const px = (x - transform.pos_x) / scale;
    const py = (transform.pos_y - y) / scale;
    return finiteNumber(px) && finiteNumber(py) ? [px, py] : null;
  }

  _rounds() {
    return this.allRounds.filter(round => round && round.side === this.side &&
      round.rtype === this.rtype && !this.disabled.has(round.round_id));
  }

  setFilter(side, rtype) {
    this.side = side;
    this.rtype = rtype;
  }

  toggleRound(roundId, enabled) {
    if (enabled) this.disabled.delete(roundId);
    else this.disabled.add(roundId);
  }

  // Interpolate a [[t,x,y], ...] series. `holdLast` is useful for death
  // markers and grenade heads, while live players disappear after their final
  // position sample.
  _interp(series, gameTime, holdLast = false) {
    if (!Array.isArray(series) || !finiteNumber(gameTime)) return null;
    let previous = null;
    for (const sample of series) {
      if (!validSample(sample)) continue;
      if (previous === null) {
        previous = sample;
        if (gameTime < sample[0]) return null;
        if (gameTime === sample[0]) return [sample[1], sample[2]];
        continue;
      }
      if (sample[0] <= previous[0]) {
        previous = sample;
        continue;
      }
      if (gameTime <= sample[0]) {
        const fraction = Math.max(0, Math.min(1,
          (gameTime - previous[0]) / (sample[0] - previous[0])));
        return [
          previous[1] + (sample[1] - previous[1]) * fraction,
          previous[2] + (sample[2] - previous[2]) * fraction
        ];
      }
      previous = sample;
    }
    if (previous && (holdLast || gameTime === previous[0])) return [previous[1], previous[2]];
    return null;
  }

  _velocityAt(series, gameTime) {
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

  drawAt(gameTime) {
    if (this.destroyed || !finiteNumber(gameTime)) return;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.cv.width, this.cv.height);
    if (this.imgReady) {
      ctx.drawImage(this.img, 0, 0, this.cv.width, this.cv.height);
    } else {
      ctx.fillStyle = "#11141e";
      ctx.fillRect(0, 0, this.cv.width, this.cv.height);
      if (this.imgFailed) {
        ctx.save();
        ctx.fillStyle = "#ff7777";
        ctx.font = "600 16px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("雷达图加载失败", this.cv.width / 2, this.cv.height / 2);
        ctx.restore();
        return;
      }
    }

    for (const round of this._rounds()) {
      const color = typeof round.color === "string" && round.color ?
        round.color : (SIDE_COLOR[this.side] || SIDE_COLOR.CT);
      for (const grenade of (Array.isArray(round.grenades) ? round.grenades : [])) {
        this._drawGrenade(grenade, gameTime);
      }

      const path = Array.isArray(round.path) ? round.path : [];
      if (finiteNumber(round.death_t) && gameTime >= round.death_t) {
        const death = this._interp(path, round.death_t, true);
        const deathPixel = death && this.g2p(death[0], death[1]);
        if (deathPixel) this._drawX(deathPixel, color);
        continue;
      }

      const position = this._interp(path, gameTime);
      const pixel = position && this.g2p(position[0], position[1]);
      if (!pixel) continue;
      const velocity = this._velocityAt(path, gameTime);
      if (velocity) {
        const scale = this.transform.scale;
        const vx = velocity[0] / scale;
        const vy = -velocity[1] / scale;
        if (finiteNumber(vx) && finiteNumber(vy) && Math.hypot(vx, vy) > 0.5) {
          this._drawArrow(pixel[0], pixel[1], Math.atan2(vy, vx), color);
        }
      }
      ctx.save();
      ctx.globalAlpha = 0.86;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(pixel[0], pixel[1], DOT_R, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  _drawGrenade(grenade, gameTime) {
    if (!grenade || !finiteNumber(grenade.throw_t) || !finiteNumber(grenade.land_t)) return;
    const type = grenade.type;
    const color = NADE_COLOR[type] || "#ffffff";
    if (gameTime >= grenade.throw_t && gameTime < grenade.land_t) {
      const arc = Array.isArray(grenade.arc) ? grenade.arc : [];
      const ctx = this.ctx;
      let started = false;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (const sample of arc) {
        if (!validSample(sample) || sample[0] > gameTime) continue;
        const pixel = this.g2p(sample[1], sample[2]);
        if (!pixel) continue;
        if (started) ctx.lineTo(pixel[0], pixel[1]);
        else {
          ctx.moveTo(pixel[0], pixel[1]);
          started = true;
        }
      }
      const head = this._interp(arc, gameTime, true);
      const headPixel = head && this.g2p(head[0], head[1]);
      if (headPixel) {
        if (started) ctx.lineTo(headPixel[0], headPixel[1]);
        else {
          ctx.moveTo(headPixel[0], headPixel[1]);
          started = true;
        }
      }
      if (started) ctx.stroke();
      ctx.restore();

      if (headPixel) this._drawNadeIcon(type, headPixel, color);
      return;
    }

    if (!finiteNumber(grenade.expire_t) || gameTime < grenade.land_t || gameTime >= grenade.expire_t ||
        !Array.isArray(grenade.land) || grenade.land.length < 2) return;
    const landing = this.g2p(grenade.land[0], grenade.land[1]);
    if (!landing) return;
    const ctx = this.ctx;
    ctx.save();
    let radius = null;
    if (NADE_R[type] && finiteNumber(this.transform.scale) && this.transform.scale !== 0) {
      radius = NADE_R[type] / Math.abs(this.transform.scale);
      ctx.globalAlpha = 0.28;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(landing[0], landing[1], radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    if (radius) this._drawNadeEffect(type, landing, radius, color);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(landing[0], landing[1], 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  _nadeIconSource(type) {
    const source = NADE_ICON_SRC[type];
    if (type !== "molotov") return source;
    return source[this.side] || source.T;
  }

  _drawNadeIcon(type, point, color) {
    // There is deliberately no decoy entry in NADE_ICON_SRC.
    const asset = tintedGrenadeAsset(this._nadeIconSource(type), color);
    if (!asset || !asset.width || !asset.height) return;
    const width = NADE_ICON_HEIGHT * asset.width / asset.height;
    const ctx = this.ctx;
    ctx.save();
    ctx.shadowColor = "rgba(0,0,0,.9)";
    ctx.shadowBlur = 4;
    ctx.shadowOffsetX = 1;
    ctx.shadowOffsetY = 1;
    ctx.drawImage(asset, point[0] - width / 2, point[1] - NADE_ICON_HEIGHT / 2,
      width, NADE_ICON_HEIGHT);
    ctx.restore();
  }

  _drawNadeEffect(type, point, radius, color) {
    const asset = tintedGrenadeAsset(NADE_EFFECT_SRC[type], color);
    if (!asset || !asset.width || !asset.height || !finiteNumber(radius) || radius <= 0) return;
    const maxSize = radius * 2;
    const scale = maxSize / Math.max(asset.width, asset.height);
    const width = asset.width * scale;
    const height = asset.height * scale;
    const ctx = this.ctx;
    ctx.save();
    ctx.globalAlpha = type === "smoke" ? 0.72 : 0.82;
    ctx.shadowColor = "rgba(0,0,0,.65)";
    ctx.shadowBlur = 3;
    ctx.drawImage(asset, point[0] - width / 2, point[1] - height / 2, width, height);
    ctx.restore();
  }

  _drawArrow(x, y, angle, color) {
    const ctx = this.ctx;
    const radius = DOT_R + 8;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.globalAlpha = 0.95;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(radius, 0);
    ctx.lineTo(radius - 9, -6);
    ctx.lineTo(radius - 9, 6);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  _drawX(point, color) {
    const ctx = this.ctx;
    const size = DOT_R * 0.85;
    ctx.save();
    ctx.globalAlpha = 0.92;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(point[0] - size, point[1] - size);
    ctx.lineTo(point[0] + size, point[1] + size);
    ctx.moveTo(point[0] + size, point[1] - size);
    ctx.lineTo(point[0] - size, point[1] + size);
    ctx.stroke();
    ctx.restore();
  }
}

if (typeof module !== "undefined") {
  module.exports = { ReplayPlayer, PLAYBACK_S, WINDOW_S, NADE_ICON_SRC, NADE_EFFECT_SRC };
}
