// CS-Scout canvas replay engine. This file is now a backward-compatible shim
// that re-exports from static/replay-engine/. The original class-based API
// (ReplayPlayer) is preserved so existing code and tests continue to work
// while new code should prefer createReplay() from the engine module.
"use strict";

const engine = typeof require === "function"
  ? require("./replay-engine/engine")
  : (typeof window !== "undefined" && window.__replayEngine ? window.__replayEngine : null);

const createReplay = engine.createReplay;
const PLAYBACK_S = engine.PLAYBACK_S;
const WINDOW_S = engine.WINDOW_S;
const SIDE_COLOR = engine.SIDE_COLOR;
const NADE_COLOR = engine.NADE_COLOR;
const NADE_R = engine.NADE_R;
const NADE_ICON_SRC = engine.NADE_ICON_SRC;
const NADE_EFFECT_SRC = engine.NADE_EFFECT_SRC;
const NADE_ICON_HEIGHT = engine.NADE_ICON_HEIGHT;
const grenadeIcon = engine.grenadeIcon;
const tintedGrenadeAsset = engine.tintedGrenadeAsset;
const finiteNumber = engine.finiteNumber;
const validSample = engine.validSample;

/**
 * Backward-compatible class wrapper around createReplay(). New code should
 * use createReplay() directly.
 */
class ReplayPlayer {
  constructor(canvas, options = {}) {
    const instance = createReplay(canvas, options);
    // Copy all methods
    this.drawAt = instance.drawAt;
    this.setFilter = instance.setFilter;
    this.toggleRound = instance.toggleRound;
    this.destroy = instance.destroy;
    this._rounds = instance._rounds;
    this._interp = instance._interp;
    this._velocityAt = instance._velocityAt;
    this._drawGrenade = instance._drawGrenade;
    this._drawArrow = instance._drawArrow;
    this._drawX = instance._drawX;
    this._drawNadeIcon = instance._drawNadeIcon;
    this._drawNadeEffect = instance._drawNadeEffect;
    this._nadeIconSource = instance._nadeIconSource;
    this.g2p = instance.g2p;
    // Property accessors
    this._instance = instance;
  }

  get cv() { return this._instance.cv; }
  get ctx() { return this._instance.ctx; }
  get transform() { return this._instance.transform; }
  set transform(v) { this._instance.transform = v; }
  get allRounds() { return this._instance.allRounds; }
  get side() { return this._instance.side; }
  set side(v) { this._instance.side = v; }
  get rtype() { return this._instance.rtype; }
  set rtype(v) { this._instance.rtype = v; }
  get disabled() { return this._instance.disabled; }
  get destroyed() { return this._instance.destroyed; }
  get imgReady() { return this._instance.imgReady; }
  set imgReady(v) { this._instance.imgReady = v; }
  get imgFailed() { return this._instance.imgFailed; }
  set imgFailed(v) { this._instance.imgFailed = v; }
  get img() { return this._instance.img; }
}

// Expose to global scope when loaded as a <script> tag in the browser.
if (typeof window !== "undefined") {
  window.__replayEngine = engine;
  window.ReplayPlayer = ReplayPlayer;
  window.PLAYBACK_S = PLAYBACK_S;
  window.WINDOW_S = WINDOW_S;
  window.NADE_ICON_SRC = NADE_ICON_SRC;
  window.NADE_EFFECT_SRC = NADE_EFFECT_SRC;
}

if (typeof module !== "undefined") {
  module.exports = {
    ReplayPlayer, createReplay, PLAYBACK_S, WINDOW_S, SIDE_COLOR,
    NADE_COLOR, NADE_R, NADE_ICON_SRC, NADE_EFFECT_SRC, NADE_ICON_HEIGHT,
    grenadeIcon, tintedGrenadeAsset, finiteNumber, validSample
  };
}
