"use strict";

// Load the replay engine modules. In Node, require() resolves the bundled
// index. In the browser, engine.js + clock.js load first and populate
// window.__replayEngine before this script runs.
const engine = typeof require === "function"
  ? require("./replay-engine/")
  : (typeof window !== "undefined" && window.__replayEngine ? window.__replayEngine : {});
const createReplay = engine.createReplay;
const createClock = engine.createClock;
const createViewManager = engine.createViewManager;
const createHeatmap = engine.createHeatmap;
const PLAYBACK_SPEEDS = engine.PLAYBACK_SPEEDS || [1, 2, 4];

const $ = selector => document.querySelector(selector);
const PLAYER_COLORS = ["#ef6aa8", "#55c8ff", "#ffd166", "#63d297", "#b59cff"];

let players = new Map();
let loadingDomains = new Set();
let playerLoadAttempts = new Map();
let playerFetchControllers = new Set();
let allPlayers = [];
let pistolRounds = [];
let pistolPlayer = null;
let heatmapRounds = [];
let heatmapPlayer = null;
let nextColor = 0;
let currentSide = "CT";
let serverFailures = [];
let uiFailures = new Map();
let pollTimer = null;
let pollEpoch = 0;
let analysisMode = "normal";
let activePlatform = "5e";
let availableMapNames = [];
let pwaCanAnalyze = false;
let fiveEUsernames = ["", "", "", "", ""];
let lastKnownAnalysisRunning = false;
let analysisBusy = false;
let publicMonitoringEnabled = false;
// The app keeps this only in page memory and never writes it to browser
// storage. Browser extensions and password managers still apply their own
// form-handling policies.
let accessKey = "";
let localDemoSessionId = "";
let localDemoPlayers = [];
let localDemoFiles = [];

// --- Engine instances -------------------------------------------------------
// The clock drives a single requestAnimationFrame loop that draws the active
// view. The view manager handles button-style panel switching so only one
// replay canvas is visible at a time.
const replayClock = createClock({
  playbackS: engine.PLAYBACK_S || 10,
  windowS: engine.WINDOW_S || 20,
  onTick: gameTime => drawAll(gameTime),
  onControlsUpdate: updateClockControls
});

// View manager lazily resolves DOM elements via the provider function so it
// can be created at module load time before the DOM is ready.
const viewManager = createViewManager(() => ({
  switcher: $("#view-switcher"),
  toolbar: $("#view-toolbar"),
  emptyState: $("#empty-state")
}));

// --- Clock wrappers (exported for test compatibility) -----------------------

function playbackSeconds() {
  return replayClock.playbackSeconds();
}

function windowSeconds() {
  return replayClock.windowSeconds();
}

function currentGameTime() {
  return replayClock.getGameTime();
}

function playbackElapsedDelta(realSeconds, speed) {
  return replayClock.playbackElapsedDelta(realSeconds, speed);
}

// --- View management wrappers (exported for test compatibility) ------------

function activateReplayView(viewKey) {
  viewManager.activate(viewKey);
  drawAll();
}

function registerReplayView(viewKey, label, panel, player, color = "", accessibleLabel = label) {
  return viewManager.register(viewKey, label, panel, player, color, accessibleLabel);
}

function drawAll(gameTime = currentGameTime()) {
  viewManager.drawActive(gameTime);
}

// --- Drawing / clock control updates ---------------------------------------

function updateClockControls() {
  const scrub = $("#scrub");
  const label = $("#timelbl");
  const button = $("#playpause");
  if (scrub && document.activeElement !== scrub) {
    scrub.value = String(Math.round(replayClock.getElapsed() / playbackSeconds() * 1000));
  }
  if (label) label.textContent = `${currentGameTime().toFixed(1)} / ${windowSeconds().toFixed(1)}s`;
  if (button) {
    const playing = replayClock.isPlaying();
    button.textContent = playing ? "⏸" : "▶";
    button.title = playing ? "暂停" : "播放";
    button.setAttribute("aria-label", playing ? "暂停回放" : "播放回放");
  }
}

function setPlaybackSpeed(speed) {
  const rate = Number(speed);
  if (!PLAYBACK_SPEEDS.includes(rate)) return;
  replayClock.setSpeed(rate);
  for (const button of document.querySelectorAll("[data-playback-speed]")) {
    const active = Number(button.dataset.playbackSpeed) === rate;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function setSide(side) {
  if (side !== "CT" && side !== "T") return;
  currentSide = side;
  const ct = $("#side-ct");
  const t = $("#side-t");
  if (ct) {
    const active = side === "CT";
    ct.classList.toggle("active", active);
    ct.setAttribute("aria-pressed", String(active));
  }
  if (t) {
    const active = side === "T";
    t.classList.toggle("active", active);
    t.setAttribute("aria-pressed", String(active));
  }
  viewManager.setSide(side);
  drawAll();
}

// --- Business logic ---------------------------------------------------------

function localDemoReady() {
  return activePlatform === "localdemos" && Boolean(localDemoSessionId);
}

function getSelectedSteamids() {
  return Array.from(document.querySelectorAll("#local-demo-players input:checked"))
    .map(cb => cb.value);
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return "?";
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KiB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MiB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GiB`;
}

function updateLocalDemoFileList(serverFiles = null) {
  const list = $("#local-demo-file-list");
  if (!list) return;
  list.replaceChildren();
  const files = Array.isArray(serverFiles) ? serverFiles : localDemoFiles;
  for (const file of files) {
    const item = document.createElement("div");
    item.textContent = `${String(file.name || "Demo")} ? ${formatBytes(file.size)}`;
    list.appendChild(item);
  }
  if (!files.length) list.textContent = "No Demo files selected";
}

function updateLocalDemoRunButton() {
  const runButton = $("#run");
  if (!runButton || activePlatform !== "localdemos") return;
  runButton.disabled = analysisBusy || !localDemoReady();
}

function resetLocalDemoState() {
  localDemoSessionId = "";
  localDemoPlayers = [];
  const info = $("#local-demo-info");
  if (info) info.hidden = true;
  const map = $("#local-demo-map");
  if (map) map.textContent = "";
  const list = $("#local-demo-players");
  if (list) list.replaceChildren();
  updateLocalDemoRunButton();
}
function localAnalysisEnabled() {
  return Boolean(document.body && document.body.dataset &&
    document.body.dataset.localAnalysis === "true");
}

function setAnalysisMode(mode) {
  if (mode !== "normal" && mode !== "fast") return;
  analysisMode = mode;
  for (const button of document.querySelectorAll("[data-analysis-mode]")) {
    const active = button.dataset.analysisMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function setAnalysisBusy(busy) {
  const disabled = Boolean(busy);
  analysisBusy = disabled;
  const runButton = $("#run");
  if (runButton) {
    runButton.disabled = disabled ||
      (activePlatform === "perfectworld" && !pwaCanAnalyze) ||
      (activePlatform === "localdemos" && !localDemoReady());
  }
  for (const button of document.querySelectorAll("[data-analysis-mode]")) {
    button.disabled = disabled;
  }
  for (const button of document.querySelectorAll("[data-platform]")) {
    button.disabled = disabled;
  }
  const depth = $("#depth");
  if (depth && activePlatform === "perfectworld") depth.disabled = disabled;
  const fileInput = $("#local-demo-files");
  if (fileInput) fileInput.disabled = disabled;
  const inspectButton = $("#local-demo-inspect");
  if (inspectButton) inspectButton.disabled = disabled || localDemoFiles.length === 0;
  updateLocalDemoRunButton();
}

function updatePlatformControls() {
  const perfectWorld = activePlatform === "perfectworld";
  const localDemos = activePlatform === "localdemos";
  for (const button of document.querySelectorAll("[data-platform]")) {
    const active = button.dataset.platform === activePlatform;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  for (const section of document.querySelectorAll("[data-five-e-only]")) {
    section.hidden = perfectWorld || localDemos;
  }
  const hint = $("#pwa-hint");
  for (const section of document.querySelectorAll("[data-local-demos-only]")) {
    section.hidden = !localDemos;
  }
  updateLocalDemoFileList();
  if (hint) hint.hidden = !perfectWorld;
  const mapSelect = $("#map");
  if (mapSelect) mapSelect.disabled = perfectWorld || localDemos || availableMapNames.length === 0;
  const playerLabel = $("#player-input-label");
  if (playerLabel) playerLabel.textContent = perfectWorld ? "完美平台用户名" : "5E 用户名";
  for (let index = 0; index < 5; index += 1) {
    const input = $(`#u${index}`);
    if (!input) continue;
    input.readOnly = perfectWorld || localDemos;
    input.setAttribute("aria-readonly", String(perfectWorld || localDemos));
    input.setAttribute(
      "aria-label",
      perfectWorld ? `完美平台用户名 ${index + 1}` :
        (index === 0 ? "5E 用户名" : `对手用户名 ${index + 1}`)
    );
    input.placeholder = perfectWorld ? `等待识别对手 ${index + 1}` : `对手用户名 ${index + 1}`;
    if (!perfectWorld) input.value = fiveEUsernames[index] || "";
  }
  const runButton = $("#run");
  if (runButton) runButton.textContent = perfectWorld ? "开始分析" : "开始扫描";
  const emptyTitle = $("#empty-title");
  if (runButton && localDemos) runButton.textContent = "Start parsing";
  updateLocalDemoRunButton();
  if (localDemos) setStatus("Select local Demos, inspect them, then choose a player.");
  if (!localDemos) resetLocalDemoState();
  const emptyDescription = $("#empty-description");
  if (emptyTitle) emptyTitle.textContent = perfectWorld ? "等待进入完美平台对局" : "等待扫描数据";
  if (emptyDescription) {
    emptyDescription.textContent = perfectWorld
      ? "匹配到对局后会自动填入对手；确认名单并点击开始分析后，这里会显示回放。"
      : "完成左侧设置并开始扫描后，这里会显示合并手枪局和每位玩家的 Buy 回放。";
  }
}

function showPerfectWorldTargets(targets) {
  const names = Array.isArray(targets)
    ? targets.slice(0, 5).map(item => String(item && item.username || ""))
    : [];
  for (let index = 0; index < 5; index += 1) {
    const input = $(`#u${index}`);
    if (input) input.value = names[index] || "";
  }
}

async function configurePerfectWorld() {
  const depth = $("#depth");
  const maxDemos = depth ? Number(depth.value) : 6;
  try {
    await requestJSON("/api/pwa/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_demos: maxDemos })
    });
  } catch (error) {
    // A 409 means the current automatic analysis has already captured its
    // depth. The new value will be used for the next match.
    if (error.status !== 409) throw error;
  }
}

async function setPlatform(platform) {
  if (analysisBusy) return;
  if (platform !== "5e" && platform !== "perfectworld" && platform !== "localdemos") return;
  if (activePlatform === "5e" && platform !== "5e") {
    fiveEUsernames = Array.from({ length: 5 }, (_item, index) => {
      const input = $(`#u${index}`);
      return input ? input.value : "";
    });
  }
  activePlatform = platform;
  pwaCanAnalyze = false;
  if (platform === "localdemos") resetLocalDemoState();
  if (platform === "perfectworld") showPerfectWorldTargets([]);
  pollEpoch += 1;
  const epoch = pollEpoch;
  clearPollTimer();
  lastKnownAnalysisRunning = false;
  resetResults();
  updatePlatformControls();
  setAnalysisBusy(false);
  if (platform === "localdemos") {
    setStatus("Select local Demos, inspect them, then choose a player.");
  } else if (platform === "perfectworld") {
    setStatus("正在连接完美平台自动侦察…");
    try {
      await configurePerfectWorld();
    } catch (error) {
      if (epoch !== pollEpoch) return;
      setStatus(`完美平台连接失败：${error.message}`);
    }
  } else {
    setStatus("已切换到 5E，可输入用户名开始扫描。");
  }
  if (epoch === pollEpoch) await poll(epoch);
}

async function runPerfectWorldAnalysis() {
  pwaCanAnalyze = false;
  setAnalysisBusy(true);
  try {
    await configurePerfectWorld();
    await requestJSON("/api/pwa/analyze", { method: "POST" });
    lastKnownAnalysisRunning = true;
    pollEpoch += 1;
    clearPollTimer();
    resetResults();
    setStatus("已确认对手，正在开始分析…");
    await poll(pollEpoch);
  } catch (error) {
    lastKnownAnalysisRunning = false;
    setStatus(error.status === 409 ? "当前名单尚未就绪或分析已经开始。" : `错误：${error.message}`);
    await poll(pollEpoch);
  }
}

function wireControls() {
  const playPause = $("#playpause");
  const scrub = $("#scrub");
  const ct = $("#side-ct");
  const t = $("#side-t");
  const speedButtons = document.querySelectorAll("[data-playback-speed]");
  const modeButtons = document.querySelectorAll("[data-analysis-mode]");
  const platformButtons = document.querySelectorAll("[data-platform]");
  if (playPause) {
    playPause.addEventListener("click", () => {
      replayClock.setPlaying(!replayClock.isPlaying());
      updateClockControls();
    });
  }
  if (scrub) {
    scrub.addEventListener("input", event => {
      const value = Number(event.target.value);
      replayClock.seek(value);
      drawAll();
      updateClockControls();
    });
  }
  if (ct) ct.addEventListener("click", () => setSide("CT"));
  if (t) t.addEventListener("click", () => setSide("T"));
  for (const button of speedButtons) {
    button.addEventListener("click", () => setPlaybackSpeed(button.dataset.playbackSpeed));
  }
  for (const button of modeButtons) {
    button.addEventListener("click", () => setAnalysisMode(button.dataset.analysisMode));
  }
  for (const button of platformButtons) {
    button.addEventListener("click", () => { void setPlatform(button.dataset.platform); });
  }
  const localFileInput = $("#local-demo-files");
  if (localFileInput) {
    localFileInput.addEventListener("change", () => {
      localDemoFiles = localFileInput.files ? Array.from(localFileInput.files) : [];
      resetLocalDemoState();
      updateLocalDemoFileList();
      setAnalysisBusy(false);
    });
  }
  const localInspectButton = $("#local-demo-inspect");
  if (localInspectButton) localInspectButton.addEventListener("click", () => { void inspectLocalDemos(); });
  setPlaybackSpeed(replayClock.getSpeed());
  setAnalysisMode(analysisMode);
  updatePlatformControls();
  document.addEventListener("visibilitychange", () => { replayClock._raw.last = null; });
}

async function requestJSON(url, options) {
  const response = await fetch(url, options);
  let body;
  try {
    body = await response.json();
  } catch (_error) {
    throw new Error(`${response.status || "网络"} 响应不是有效 JSON`);
  }
  if (!response.ok) {
    const error = new Error(body.error || `请求失败 (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function protectedRequestOptions(options = {}) {
  if (!accessKey) {
    const error = new Error("请输入访问密钥");
    error.status = 401;
    throw error;
  }
  return {
    ...options,
    headers: {
      ...(options.headers || {}),
      "Authorization": `Bearer ${accessKey}`
    }
  };
}

async function requestProtectedJSON(url, options = {}) {
  return requestJSON(url, protectedRequestOptions(options));
}

async function connectWithEnteredKey() {
  const keyInput = $("#key");
  accessKey = keyInput && typeof keyInput.value === "string" ? keyInput.value.trim() : "";
  pollEpoch += 1;
  clearPollTimer();
  setStatus(accessKey ? "分析密钥已输入，正在刷新公开结果…" : "分析密钥已清除，仍可查看公开结果…");
  await poll(pollEpoch);
}

function setStatus(message) {
  const target = $("#status");
  if (target) target.textContent = message || "";
}

function renderFailures() {
  const target = $("#failed");
  if (!target) return;
  target.replaceChildren();
  const failures = [
    ...serverFailures.map(item => ({
      username: String(item && item.username || "未知玩家"),
      reason: String(item && item.reason || "分析失败")
    })),
    ...[...uiFailures.entries()].map(([username, reason]) => ({ username, reason }))
  ];
  for (const failure of failures) {
    const line = document.createElement("div");
    line.textContent = `✗ ${failure.username}: ${failure.reason}`;
    target.appendChild(line);
  }
}

async function loadMaps() {
  const select = $("#map");
  if (!select) return;
  try {
    const data = await requestJSON("/api/maps");
    const mapNames = Array.isArray(data.maps) ? data.maps : [];
    availableMapNames = mapNames.map(String);
    select.replaceChildren();
    for (const mapName of mapNames) {
      const option = document.createElement("option");
      option.value = String(mapName);
      option.textContent = String(mapName);
      select.appendChild(option);
    }
    select.disabled = activePlatform === "perfectworld" || activePlatform === "localdemos" || mapNames.length === 0;
    if (mapNames.length === 0) setStatus("没有可用地图，请先生成地图资源。");
  } catch (error) {
    availableMapNames = [];
    select.replaceChildren();
    select.disabled = true;
    setStatus(`地图加载失败：${error.message}`);
  }
}

function enteredNames() {
  const result = [];
  for (let index = 0; index < 5; index += 1) {
    const input = $(`#u${index}`);
    const value = input ? input.value.trim() : "";
    if (value) result.push(value);
  }
  return result;
}

function clearPollTimer() {
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
}

function schedulePoll(epoch, delay = 2000) {
  if (epoch !== pollEpoch) return;
  clearPollTimer();
  pollTimer = setTimeout(() => poll(epoch), delay);
}

function resetResults() {
  for (const controller of playerFetchControllers) controller.abort();
  for (const player of allPlayers) {
    if (typeof player.destroy === "function") player.destroy();
  }
  players = new Map();
  loadingDomains = new Set();
  playerLoadAttempts = new Map();
  playerFetchControllers = new Set();
  allPlayers = [];
  pistolRounds = [];
  pistolPlayer = null;
  heatmapRounds = [];
  heatmapPlayer = null;
  nextColor = 0;
  serverFailures = [];
  uiFailures = new Map();
  viewManager.reset();
  const cards = $("#cards");
  const switcher = $("#view-switcher");
  const toolbar = $("#view-toolbar");
  const legend = $("#pistol-legend");
  const pistol = $("#pistol");
  const heatmap = $("#heatmap");
  const empty = $("#empty-state");
  if (cards) cards.replaceChildren();
  if (switcher) {
    switcher.replaceChildren();
    switcher.hidden = true;
  }
  if (toolbar) toolbar.hidden = true;
  if (legend) legend.replaceChildren();
  if (pistol) pistol.hidden = true;
  if (heatmap) heatmap.hidden = true;
  if (empty) empty.hidden = false;
  replayClock.setElapsed(0);
  setSide("CT");
  renderFailures();
}

async function inspectLocalDemos() {
  const input = $("#local-demo-files");
  if (!input || !input.files || input.files.length === 0) {
    setStatus("Select one or more .dem files first.");
    return;
  }
  localDemoFiles = Array.from(input.files);
  localDemoSessionId = "";
  localDemoPlayers = [];
  setAnalysisBusy(true);
  try {
    const formData = new FormData();
    for (const file of localDemoFiles) formData.append("demos", file, file.name);
    const data = await requestJSON("/api/local-demos/inspect", {
      method: "POST",
      body: formData
    });
    localDemoSessionId = String(data.session_id || "");
    localDemoPlayers = Array.isArray(data.players) ? data.players : [];
    updateLocalDemoFileList(Array.isArray(data.files) ? data.files : null);
    const map = $("#local-demo-map");
    if (map) map.textContent = `Map: ${String(data.map || "unknown")} - ${localDemoFiles.length} files, ${localDemoPlayers.length} players`;
    const list = $("#local-demo-players");
    if (list) {
      list.replaceChildren();
      for (const player of localDemoPlayers) {
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = String(player.steamid || "");
        cb.checked = true;
        cb.addEventListener("change", updateLocalDemoRunButton);
        const name = `${String(player.username || player.steamid)} (${player.appearances}/${localDemoFiles.length})`;
        label.append(cb, document.createTextNode(name));
        list.appendChild(label);
      }
    }
    const info = $("#local-demo-info");
    if (info) info.hidden = false;
    updateLocalDemoRunButton();
    setStatus(`Demo inspection complete: ${localDemoPlayers.length} players found.`);
  } catch (error) {
    resetLocalDemoState();
    setStatus(`Demo inspection failed: ${error.message}`);
  } finally {
    setAnalysisBusy(false);
  }
}

async function runLocalDemoAnalysis() {
  if (!localDemoReady()) {
    setStatus("Inspect the Demos first.");
    return;
  }
  const steamids = getSelectedSteamids();
  if (steamids.length === 0) {
    setStatus("Select at least one player to analyze.");
    return;
  }
  setAnalysisBusy(true);
  try {
    await requestJSON("/api/local-demos/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: localDemoSessionId, steamids })
    });
    lastKnownAnalysisRunning = true;
    pollEpoch += 1;
    clearPollTimer();
    resetResults();
    setStatus("Local Demo analysis started...");
    await poll(pollEpoch);
  } catch (error) {
    if (error.status === 409) {
      lastKnownAnalysisRunning = true;
      pollEpoch += 1;
      clearPollTimer();
      resetResults();
      setStatus("Another analysis is already running; restoring progress...");
      await poll(pollEpoch);
      return;
    }
    lastKnownAnalysisRunning = false;
    setStatus(`Local Demo analysis failed: ${error.message}`);
    setAnalysisBusy(false);
  }
}
async function runAnalysis() {
  if (activePlatform === "localdemos") {
    await runLocalDemoAnalysis();
    return;
  }
  if (activePlatform === "perfectworld") {
    await runPerfectWorldAnalysis();
    return;
  }
  const mapSelect = $("#map");
  const depth = $("#depth");
  const key = $("#key");
  setAnalysisBusy(true);
  try {
    accessKey = key && typeof key.value === "string" ? key.value.trim() : "";
    const body = {
      usernames: enteredNames(),
      map: mapSelect ? mapSelect.value : "",
      max_demos: depth ? Number(depth.value) : 6,
      mode: analysisMode
    };
    const options = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    };
    if (localAnalysisEnabled()) await requestJSON("/api/analyze", options);
    else await requestProtectedJSON("/api/analyze", options);
    lastKnownAnalysisRunning = true;
    pollEpoch += 1;
    clearPollTimer();
    resetResults();
    setStatus(`${analysisMode === "fast" ? "快速" : "普通"}分析已启动…`);
    await poll(pollEpoch);
  } catch (error) {
    if (error.status === 409) {
      lastKnownAnalysisRunning = true;
      pollEpoch += 1;
      clearPollTimer();
      // This tab may still contain the previous run. Clear it before adopting
      // the task started in another tab, otherwise matching domains are
      // mistaken for already-loaded current results.
      resetResults();
      setStatus("已有分析正在运行，正在恢复进度…");
      await poll(pollEpoch);
      return;
    }
    lastKnownAnalysisRunning = false;
    setStatus(`错误：${error.message}`);
    setAnalysisBusy(false);
  }
}

function ensurePistolPlayer(data) {
  if (pistolPlayer) return;
  const canvas = $("#pistol-canvas");
  const pistol = $("#pistol");
  if (!canvas) throw new Error("页面缺少合并手枪局画布");
  if (!pistol) throw new Error("页面缺少合并手枪局面板");
  const player = createReplay(canvas, {
    radar: data.radar,
    transform: data.transform,
    rounds: pistolRounds,
    side: currentSide,
    rtype: "Pistol"
  });
  try {
    registerReplayView("pistol", "手枪局（全员）", pistol, player, "#5d86ff");
  } catch (error) {
    player.destroy();
    throw error;
  }
  pistolPlayer = player;
  allPlayers.push(pistolPlayer);
  viewManager.addSideTarget(pistolPlayer, "Pistol");
}

function ensureHeatmapPlayer(data) {
  if (heatmapPlayer) return;
  const canvas = $("#heatmap-canvas");
  const panel = $("#heatmap");
  if (!canvas) throw new Error("页面缺少热力图画布");
  if (!panel) throw new Error("页面缺少热力图面板");
  const player = createHeatmap(canvas, {
    radar: data.radar,
    transform: data.transform,
    rounds: heatmapRounds,
    side: currentSide,
    rtype: "Buy"
  });
  try {
    registerReplayView("heatmap", "热力图（全员）", panel, player, "#ff6b6b");
  } catch (error) {
    player.destroy();
    throw error;
  }
  heatmapPlayer = player;
  allPlayers.push(heatmapPlayer);
  viewManager.addSideTarget(heatmapPlayer, "Buy");
}

function addLegendItem(username, color) {
  const legend = $("#pistol-legend");
  if (!legend) return;
  const item = document.createElement("span");
  item.className = "legend-item";
  const swatch = document.createElement("i");
  swatch.className = "legend-swatch";
  swatch.style.backgroundColor = color;
  const label = document.createElement("span");
  label.textContent = username;
  item.append(swatch, label);
  legend.appendChild(item);
}

function stat(label, value, suffix = "") {
  const item = document.createElement("span");
  item.className = "stat";
  item.append(`${label} `);
  const strong = document.createElement("strong");
  strong.textContent = value === null || value === undefined ? "-" : `${value}${suffix}`;
  item.appendChild(strong);
  return item;
}

function buildPlayerCard(data, username, color, rtype = "Buy") {
  const card = document.createElement("article");
  card.className = "card player-card";
  card.style.borderLeftColor = color;
  card.style.borderLeftWidth = "3px";

  const heading = document.createElement("div");
  heading.className = "card-heading";
  const title = document.createElement("h2");
  title.textContent = username;
  title.style.color = color;
  const stats = document.createElement("div");
  stats.className = "stats";
  const combat = data.combat_stats || {};
  stats.append(
    stat("K/D", combat.kd),
    stat("AWP 持有率", combat.awp_rate, "%"),
    stat("有效回合", data.round_count ?? (Array.isArray(data.rounds) ? data.rounds.length : 0))
  );
  const rtypeLabel = document.createElement("span");
  rtypeLabel.className = "buy-label";
  rtypeLabel.textContent = rtype === "Pistol" ? "Pistol" : "Buy";
  heading.append(title, stats, rtypeLabel);

  const canvas = document.createElement("canvas");
  canvas.className = "replay-canvas";
  canvas.dataset.rtype = rtype;
  canvas.setAttribute("aria-label", `${username} ${rtype} 回放`);
  card.append(heading, canvas);
  return { card, canvas };
}

async function addPlayer(result, epoch = pollEpoch) {
  if (epoch !== pollEpoch) return;
  const domain = String(result && result.domain || "");
  if (!domain || players.has(domain) || loadingDomains.has(domain)) return;
  // Capture the per-run loading set. resetResults() replaces the global set;
  // an older in-flight request must not unlock or mutate the next run.
  const runLoadingDomains = loadingDomains;
  const runFetchControllers = playerFetchControllers;
  const fetchController = new AbortController();
  runLoadingDomains.add(domain);
  runFetchControllers.add(fetchController);
  try {
    const data = activePlatform === "perfectworld"
      ? await requestJSON(`/api/pwa/player/${encodeURIComponent(domain)}`, {
        signal: fetchController.signal
      })
      : await requestJSON(`/api/player/${encodeURIComponent(domain)}`, {
        signal: fetchController.signal
      });
    if (epoch !== pollEpoch || runLoadingDomains !== loadingDomains) return;
    if (players.has(domain)) return;
    if (!data || !Array.isArray(data.rounds) || !data.transform || !data.radar) {
      throw new Error("玩家回放数据不完整");
    }
    const username = String(data.username || result.username || domain);
    const color = PLAYER_COLORS[nextColor % PLAYER_COLORS.length];
    nextColor += 1;

    const { card: buyCard, canvas: buyCanvas } = buildPlayerCard(data, username, color, "Buy");
    buyCard.id = `buy-${domain}`;
    const buyPlayer = createReplay(buyCanvas, {
      radar: data.radar,
      transform: data.transform,
      rounds: data.rounds,
      side: currentSide,
      rtype: "Buy"
    });
    try {
      ensurePistolPlayer(data);
      ensureHeatmapPlayer(data);
      const cards = $("#cards");
      if (!cards) throw new Error("页面缺少玩家卡片容器");
      cards.appendChild(buyCard);
      allPlayers.push(buyPlayer);
      viewManager.addSideTarget(buyPlayer, "Buy");
      registerReplayView(`buy:${domain}`, username, buyCard, buyPlayer, color, `${username} 购买局`);

      const pistolRoundsForPlayer = data.rounds.filter(r => r && r.rtype === "Pistol");
      if (pistolRoundsForPlayer.length > 0) {
        const { card: pistolCard, canvas: pistolCanvas } = buildPlayerCard(data, username, color, "Pistol");
        pistolCard.id = `pistol-${domain}`;
        const perPlayerPistol = createReplay(pistolCanvas, {
          radar: data.radar,
          transform: data.transform,
          rounds: data.rounds,
          side: currentSide,
          rtype: "Pistol"
        });
        cards.appendChild(pistolCard);
        allPlayers.push(perPlayerPistol);
        viewManager.addSideTarget(perPlayerPistol, "Pistol");
        registerReplayView(`pistol:${domain}`, `${username} 手枪局`, pistolCard, perPlayerPistol, color, `${username} 手枪局`);
      }

      // Per-player density heatmap
      const { card: heatCard, canvas: heatCanvas } = buildPlayerCard(data, username, color, "热力图");
      heatCard.id = `heat-${domain}`;
      const heatPlayer = createHeatmap(heatCanvas, {
        radar: data.radar,
        transform: data.transform,
        rounds: data.rounds,
        side: currentSide,
        rtype: "Buy"
      });
      cards.appendChild(heatCard);
      allPlayers.push(heatPlayer);
      viewManager.addSideTarget(heatPlayer, "Buy");
      registerReplayView(`heat:${domain}`, `${username} 热力图`, heatCard, heatPlayer, color, `${username} 热力图`);

      players.set(domain, { data, buyPlayer, color });

      for (const round of data.rounds) {
        if (round && round.rtype === "Pistol") pistolRounds.push({ ...round, color });
        if (round && round.rtype === "Buy") heatmapRounds.push({ ...round });
      }
      if (heatmapPlayer) heatmapPlayer.markDirty();
      addLegendItem(username, color);
      uiFailures.delete(username);
      drawAll();
    } catch (error) {
      buyPlayer.destroy();
      card.remove();
      throw error;
    }
  } finally {
    runLoadingDomains.delete(domain);
    runFetchControllers.delete(fetchController);
  }
}

async function poll(epoch = pollEpoch) {
  try {
    const perfectWorld = activePlatform === "perfectworld";
    const status = perfectWorld
      ? await requestJSON("/api/pwa/status")
      : await requestJSON("/api/status");
    if (epoch !== pollEpoch) return;
    setStatus(status.message || status.status || "");
    const running = perfectWorld
      ? ["detected", "queued", "analyzing"].includes(status.phase)
      : status.status === "running";
    if (perfectWorld && status.map) {
      const mapSelect = $("#map");
      if (mapSelect && availableMapNames.includes(String(status.map))) {
        mapSelect.value = String(status.map);
      }
    }
    if (perfectWorld) {
      showPerfectWorldTargets(status.targets);
      pwaCanAnalyze = status.phase === "awaiting_confirmation";
      const emptyTitle = $("#empty-title");
      if (emptyTitle && status.phase === "awaiting_confirmation") {
        emptyTitle.textContent = "请确认完美平台对手";
      }
    }
    if (running && !lastKnownAnalysisRunning && players.size > 0) {
      resetResults();
    }
    lastKnownAnalysisRunning = running;
    if (!perfectWorld && running && (status.mode === "normal" || status.mode === "fast")) {
      setAnalysisMode(status.mode);
    }
    setAnalysisBusy(running);
    serverFailures = Array.isArray(status.failed) ? status.failed : [];
    let retryNeeded = false;
    const results = Array.isArray(status.results) ? status.results : [];
    for (const result of results) {
      if (epoch !== pollEpoch) return;
      const domain = String(result && result.domain || "");
      if (!domain || players.has(domain)) continue;
      try {
        await addPlayer(result, epoch);
        if (epoch !== pollEpoch) return;
        playerLoadAttempts.delete(domain);
      } catch (error) {
        if (epoch !== pollEpoch) return;
        const attempts = (playerLoadAttempts.get(domain) || 0) + 1;
        playerLoadAttempts.set(domain, attempts);
        uiFailures.set(String(result.username || domain), `回放加载失败：${error.message}`);
        retryNeeded = retryNeeded || attempts < 3;
      }
    }
    renderFailures();

    if (running) schedulePoll(epoch, perfectWorld ? 1000 : 2000);
    else if (retryNeeded) schedulePoll(epoch, 2500);
    else if (perfectWorld) schedulePoll(epoch, status.phase === "error" ? 3000 : 1500);
    else if (publicMonitoringEnabled) schedulePoll(epoch, 5000);
  } catch (error) {
    if (epoch !== pollEpoch) return;
    setStatus(`状态读取失败：${error.message}`);
    setAnalysisBusy(lastKnownAnalysisRunning);
    schedulePoll(epoch, 3000);
  }
}

function boot() {
  // Optional controls are isolated so a stale template cannot block core startup.
  try { wireControls(); } catch (error) { console.error("Control setup failed", error); }
  const runButton = $("#run");
  const keyInput = $("#key");
  const depthInput = $("#depth");
  if (runButton) runButton.addEventListener("click", runAnalysis);
  if (keyInput) {
    keyInput.addEventListener("change", connectWithEnteredKey);
    keyInput.addEventListener("keydown", event => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      connectWithEnteredKey();
    });
  }
  if (depthInput) {
    depthInput.addEventListener("change", () => {
      if (activePlatform === "perfectworld") void configurePerfectWorld();
    });
  }
  loadMaps();
  // Viewing current progress and the latest completed replay is public. The
  // access key is only read when the visitor starts a new analysis.
  publicMonitoringEnabled = true;
  void poll(pollEpoch);
  updateClockControls();
  replayClock.start();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
}

if (typeof module !== "undefined") {
  module.exports = {
    activateReplayView, registerReplayView, drawAll, playbackElapsedDelta,
    wireControls, setAnalysisMode, setAnalysisBusy, runAnalysis,
    connectWithEnteredKey, setPlatform, updatePlatformControls,
    showPerfectWorldTargets, runPerfectWorldAnalysis,
    inspectLocalDemos, runLocalDemoAnalysis,
  };
}
