// CS-Scout replay engine unified entry point.
"use strict";

const engine = require("./engine");
const clock = require("./clock");
const heatmap = require("./heatmap");

module.exports = {
  createReplay: engine.createReplay,
  PLAYBACK_S: engine.PLAYBACK_S,
  WINDOW_S: engine.WINDOW_S,
  SIDE_COLOR: engine.SIDE_COLOR,
  NADE_COLOR: engine.NADE_COLOR,
  NADE_R: engine.NADE_R,
  NADE_ICON_SRC: engine.NADE_ICON_SRC,
  NADE_EFFECT_SRC: engine.NADE_EFFECT_SRC,
  NADE_ICON_HEIGHT: engine.NADE_ICON_HEIGHT,
  grenadeIcon: engine.grenadeIcon,
  tintedGrenadeAsset: engine.tintedGrenadeAsset,
  finiteNumber: engine.finiteNumber,
  validSample: engine.validSample,
  createClock: clock.createClock,
  createViewManager: clock.createViewManager,
  PLAYBACK_SPEEDS: clock.PLAYBACK_SPEEDS,
  createHeatmap: heatmap.createHeatmap,
  heatColor: heatmap.heatColor,
  HEATMAP_RADIUS: heatmap.HEATMAP_RADIUS
};
