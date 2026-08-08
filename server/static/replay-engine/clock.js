// CS-Scout replay clock and view manager. The clock drives a single
// requestAnimationFrame loop that calls draw on the active view. The view
// manager handles button-style panel switching so only one replay is visible
// at a time.
(function() {
"use strict";

const PLAYBACK_SPEEDS = [1, 2, 4];

/**
 * Create a shared animation clock.
 *
 * @param {object} options
 * @param {function} options.getGameTime - returns current game time for drawing
 * @param {function} options.onTick - called each frame with the game time
 * @param {function} options.onControlsUpdate - called each frame to sync scrubber/label
 * @param {number} options.playbackS - playback loop duration (default 10)
 * @param {number} options.windowS - game time window (default 20)
 * @returns {{ start, stop, setPlaying, setSpeed, setElapsed, seek,
 *            getElapsed, getGameTime, isPlaying, getSpeed,
 *            playbackElapsedDelta, playbackSeconds, windowSeconds }}
 */
function createClock(options = {}) {
  const playbackS = options.playbackS || 10;
  const windowS = options.windowS || 20;
  const clock = { elapsed: 0, playing: true, speed: 2, last: null, raf: null };

  function playbackSeconds() {
    return typeof playbackS === "number" && playbackS > 0 ? playbackS : 10;
  }

  function windowSeconds() {
    return typeof windowS === "number" && windowS > 0 ? windowS : 20;
  }

  function getGameTime() {
    return clock.elapsed / playbackSeconds() * windowSeconds();
  }

  function playbackElapsedDelta(realSeconds, speed = clock.speed) {
    const seconds = Number(realSeconds);
    const rate = Number(speed);
    if (!Number.isFinite(seconds) || seconds < 0 || !PLAYBACK_SPEEDS.includes(rate)) return 0;
    return seconds * rate * playbackSeconds() / windowSeconds();
  }

  function tick(timestamp) {
    if (clock.last === null) clock.last = timestamp;
    const delta = Math.max(0, Math.min((timestamp - clock.last) / 1000, 1));
    clock.last = timestamp;
    if (clock.playing) {
      clock.elapsed = (clock.elapsed + playbackElapsedDelta(delta)) % playbackSeconds();
    }
    if (typeof options.onTick === "function") options.onTick(getGameTime());
    if (typeof options.onControlsUpdate === "function") options.onControlsUpdate();
    clock.raf = requestAnimationFrame(tick);
  }

  function start() {
    if (typeof requestAnimationFrame === "function") {
      clock.raf = requestAnimationFrame(tick);
    }
  }

  function stop() {
    if (clock.raf !== null && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(clock.raf);
    }
    clock.raf = null;
  }

  function setPlaying(playing) {
    clock.playing = playing;
    clock.last = null;
  }

  function setSpeed(speed) {
    const rate = Number(speed);
    if (!PLAYBACK_SPEEDS.includes(rate)) return;
    clock.speed = rate;
    clock.last = null;
  }

  function setElapsed(value) {
    clock.elapsed = value;
    clock.last = null;
  }

  function seek(scrubValue) {
    const value = Number(scrubValue);
    if (!Number.isFinite(value)) return;
    clock.playing = false;
    clock.elapsed = Math.max(0, Math.min(value, 1000)) / 1000 * playbackSeconds();
    clock.last = null;
  }

  return {
    start,
    stop,
    setPlaying,
    setSpeed,
    setElapsed,
    seek,
    getElapsed: () => clock.elapsed,
    getGameTime,
    isPlaying: () => clock.playing,
    getSpeed: () => clock.speed,
    playbackElapsedDelta,
    playbackSeconds,
    windowSeconds,
    // Expose raw clock for backward compatibility with code that reads clock.elapsed etc.
    _raw: clock
  };
}

/**
 * Create a view manager that handles button-style panel switching.
 *
 * @param {function|null} getElements - function returning { switcher, toolbar, emptyState }
 * @returns {{ register, activate, reset, has, getActive, drawActive, setSide,
 *            addSideTarget, getViews, getSideTargets, getActiveKey }}
 */
function createViewManager(getElements) {
  const views = new Map();
  let activeKey = null;
  const sideTargets = [];

  function _elems() {
    if (typeof getElements === "function") return getElements();
    if (getElements && typeof getElements === "object") return getElements;
    return {};
  }

  function register(viewKey, label, panel, player, color = "", accessibleLabel = label) {
    if (views.has(viewKey)) return views.get(viewKey);
    const { switcher, toolbar, emptyState } = _elems();
    if (!switcher || !panel || !player) throw new Error("页面缺少回放视图容器");

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.title = label;
    button.dataset.viewKey = viewKey;
    button.setAttribute("aria-label", accessibleLabel);
    button.setAttribute("aria-pressed", "false");
    if (panel.id) button.setAttribute("aria-controls", panel.id);
    if (color) button.style.setProperty("--view-color", color);
    button.addEventListener("click", () => activate(viewKey));

    panel.hidden = true;
    switcher.appendChild(button);
    const view = { panel, player, button, viewKey };
    views.set(viewKey, view);
    switcher.hidden = false;
    if (toolbar) toolbar.hidden = false;
    if (emptyState) emptyState.hidden = true;
    if (activeKey === null) activate(viewKey);
    return view;
  }

  function activate(viewKey) {
    if (!views.has(viewKey)) return;
    activeKey = viewKey;
    for (const [key, view] of views) {
      const active = key === viewKey;
      view.panel.hidden = !active;
      view.button.classList.toggle("active", active);
      view.button.setAttribute("aria-pressed", String(active));
    }
  }

  function has(viewKey) {
    return views.has(viewKey);
  }

  function getActive() {
    return views.get(activeKey) || null;
  }

  function drawActive(gameTime) {
    const activeView = views.get(activeKey);
    if (!activeView || !activeView.player) return;
    try {
      activeView.player.drawAt(gameTime);
    } catch (error) {
      // A malformed player payload must not stop the shared animation clock.
      console.error("Replay draw failed", error);
    }
  }

  function setSide(side) {
    for (const { player, rtype } of sideTargets) player.setFilter(side, rtype);
  }

  function addSideTarget(player, rtype) {
    sideTargets.push({ player, rtype });
  }

  function reset() {
    activeKey = null;
    views.clear();
    sideTargets.length = 0;
  }

  function getViews() { return views; }
  function getSideTargets() { return sideTargets; }
  function getActiveKey() { return activeKey; }

  return {
    register,
    activate,
    has,
    getActive,
    drawActive,
    setSide,
    addSideTarget,
    reset,
    getViews,
    getSideTargets,
    getActiveKey
  };
}

const _exports = { createClock, createViewManager, PLAYBACK_SPEEDS };

if (typeof module !== "undefined") {
  module.exports = _exports;
}
if (typeof window !== "undefined") {
  window.__replayEngine = window.__replayEngine || {};
  Object.assign(window.__replayEngine, _exports);
}
})();
