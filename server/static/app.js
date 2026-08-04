"use strict";

const $ = selector => document.querySelector(selector);
const PLAYER_COLORS = ["#ef6aa8", "#55c8ff", "#ffd166", "#63d297", "#b59cff"];

let players = new Map();
let loadingDomains = new Set();
let playerLoadAttempts = new Map();
let playerFetchControllers = new Set();
let allPlayers = [];
let sideTargets = [];
let replayViews = new Map();
let activeViewKey = null;
let pistolRounds = [];
let pistolPlayer = null;
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

const PLAYBACK_SPEEDS = [1, 2, 4];
const clock = { elapsed: 0, playing: true, speed: 2, last: null, raf: null };

function localAnalysisEnabled() {
  return Boolean(document.body && document.body.dataset &&
    document.body.dataset.localAnalysis === "true");
}

function playbackSeconds() {
  return typeof PLAYBACK_S === "number" && PLAYBACK_S > 0 ? PLAYBACK_S : 10;
}

function windowSeconds() {
  return typeof WINDOW_S === "number" && WINDOW_S > 0 ? WINDOW_S : 20;
}

function currentGameTime() {
  return clock.elapsed / playbackSeconds() * windowSeconds();
}

function playbackElapsedDelta(realSeconds, speed = clock.speed) {
  const seconds = Number(realSeconds);
  const rate = Number(speed);
  if (!Number.isFinite(seconds) || seconds < 0 || !PLAYBACK_SPEEDS.includes(rate)) return 0;
  return seconds * rate * playbackSeconds() / windowSeconds();
}

function setPlaybackSpeed(speed) {
  const rate = Number(speed);
  if (!PLAYBACK_SPEEDS.includes(rate)) return;
  clock.speed = rate;
  clock.last = null;
  for (const button of document.querySelectorAll("[data-playback-speed]")) {
    const active = Number(button.dataset.playbackSpeed) === rate;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
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
      (activePlatform === "perfectworld" && !pwaCanAnalyze);
  }
  for (const button of document.querySelectorAll("[data-analysis-mode]")) {
    button.disabled = disabled;
  }
  for (const button of document.querySelectorAll("[data-platform]")) {
    button.disabled = disabled;
  }
  const depth = $("#depth");
  if (depth && activePlatform === "perfectworld") depth.disabled = disabled;
}

function updatePlatformControls() {
  const perfectWorld = activePlatform === "perfectworld";
  for (const button of document.querySelectorAll("[data-platform]")) {
    const active = button.dataset.platform === activePlatform;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  for (const section of document.querySelectorAll("[data-five-e-only]")) {
    section.hidden = perfectWorld;
  }
  const hint = $("#pwa-hint");
  if (hint) hint.hidden = !perfectWorld;
  const mapSelect = $("#map");
  if (mapSelect) mapSelect.disabled = perfectWorld || availableMapNames.length === 0;
  const playerLabel = $("#player-input-label");
  if (playerLabel) playerLabel.textContent = perfectWorld ? "完美平台用户名" : "5E 用户名";
  for (let index = 0; index < 5; index += 1) {
    const input = $(`#u${index}`);
    if (!input) continue;
    input.readOnly = perfectWorld;
    input.setAttribute("aria-readonly", String(perfectWorld));
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
  if (platform !== "5e" && platform !== "perfectworld") return;
  if (activePlatform === "5e" && platform === "perfectworld") {
    fiveEUsernames = Array.from({ length: 5 }, (_item, index) => {
      const input = $(`#u${index}`);
      return input ? input.value : "";
    });
  }
  activePlatform = platform;
  pwaCanAnalyze = false;
  if (platform === "perfectworld") showPerfectWorldTargets([]);
  pollEpoch += 1;
  const epoch = pollEpoch;
  clearPollTimer();
  lastKnownAnalysisRunning = false;
  resetResults();
  updatePlatformControls();
  setAnalysisBusy(false);
  if (platform === "perfectworld") {
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

function drawAll(gameTime = currentGameTime()) {
  const activeView = replayViews.get(activeViewKey);
  if (!activeView || !activeView.player) return;
  try {
    activeView.player.drawAt(gameTime);
  } catch (error) {
    // A malformed player payload must not stop the shared animation clock.
    console.error("Replay draw failed", error);
  }
}

function updateClockControls() {
  const scrub = $("#scrub");
  const label = $("#timelbl");
  const button = $("#playpause");
  if (scrub && document.activeElement !== scrub) {
    scrub.value = String(Math.round(clock.elapsed / playbackSeconds() * 1000));
  }
  if (label) label.textContent = `${currentGameTime().toFixed(1)} / ${windowSeconds().toFixed(1)}s`;
  if (button) {
    button.textContent = clock.playing ? "⏸" : "▶";
    button.title = clock.playing ? "暂停" : "播放";
    button.setAttribute("aria-label", clock.playing ? "暂停回放" : "播放回放");
  }
}

function tick(timestamp) {
  if (clock.last === null) clock.last = timestamp;
  const delta = Math.max(0, Math.min((timestamp - clock.last) / 1000, 1));
  clock.last = timestamp;
  if (clock.playing) {
    clock.elapsed = (clock.elapsed + playbackElapsedDelta(delta)) % playbackSeconds();
  }
  drawAll();
  updateClockControls();
  clock.raf = requestAnimationFrame(tick);
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
  for (const { player, rtype } of sideTargets) player.setFilter(side, rtype);
  drawAll();
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
      clock.playing = !clock.playing;
      clock.last = null;
      updateClockControls();
    });
  }
  if (scrub) {
    scrub.addEventListener("input", event => {
      const value = Number(event.target.value);
      if (!Number.isFinite(value)) return;
      clock.playing = false;
      clock.elapsed = Math.max(0, Math.min(value, 1000)) / 1000 * playbackSeconds();
      clock.last = null;
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
  setPlaybackSpeed(clock.speed);
  setAnalysisMode(analysisMode);
  updatePlatformControls();
  document.addEventListener("visibilitychange", () => { clock.last = null; });
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
    select.disabled = activePlatform === "perfectworld" || mapNames.length === 0;
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

function activateReplayView(viewKey) {
  if (!replayViews.has(viewKey)) return;
  activeViewKey = viewKey;
  for (const [key, view] of replayViews) {
    const active = key === viewKey;
    view.panel.hidden = !active;
    view.button.classList.toggle("active", active);
    view.button.setAttribute("aria-pressed", String(active));
  }
  drawAll();
}

function registerReplayView(viewKey, label, panel, player, color = "", accessibleLabel = label) {
  if (replayViews.has(viewKey)) return replayViews.get(viewKey);
  const switcher = $("#view-switcher");
  const toolbar = $("#view-toolbar");
  const empty = $("#empty-state");
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
  button.addEventListener("click", () => activateReplayView(viewKey));

  panel.hidden = true;
  switcher.appendChild(button);
  const view = { panel, player, button };
  replayViews.set(viewKey, view);
  switcher.hidden = false;
  if (toolbar) toolbar.hidden = false;
  if (empty) empty.hidden = true;
  if (activeViewKey === null) activateReplayView(viewKey);
  return view;
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
  sideTargets = [];
  replayViews = new Map();
  activeViewKey = null;
  pistolRounds = [];
  pistolPlayer = null;
  nextColor = 0;
  serverFailures = [];
  uiFailures = new Map();
  const cards = $("#cards");
  const switcher = $("#view-switcher");
  const toolbar = $("#view-toolbar");
  const legend = $("#pistol-legend");
  const pistol = $("#pistol");
  const empty = $("#empty-state");
  if (cards) cards.replaceChildren();
  if (switcher) {
    switcher.replaceChildren();
    switcher.hidden = true;
  }
  if (toolbar) toolbar.hidden = true;
  if (legend) legend.replaceChildren();
  if (pistol) pistol.hidden = true;
  if (empty) empty.hidden = false;
  clock.elapsed = 0;
  clock.last = null;
  setSide("CT");
  renderFailures();
}

async function runAnalysis() {
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
  const player = new ReplayPlayer(canvas, {
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
  sideTargets.push({ player: pistolPlayer, rtype: "Pistol" });
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

function buildPlayerCard(data, username, color) {
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
  const buyLabel = document.createElement("span");
  buyLabel.className = "buy-label";
  buyLabel.textContent = "Buy";
  heading.append(title, stats, buyLabel);

  const canvas = document.createElement("canvas");
  canvas.className = "replay-canvas";
  canvas.dataset.rtype = "Buy";
  canvas.setAttribute("aria-label", `${username} Buy 回放`);
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

    const { card, canvas } = buildPlayerCard(data, username, color);
    card.id = `buy-${domain}`;
    const buyPlayer = new ReplayPlayer(canvas, {
      radar: data.radar,
      transform: data.transform,
      rounds: data.rounds,
      side: currentSide,
      rtype: "Buy"
    });
    try {
      ensurePistolPlayer(data);
      const cards = $("#cards");
      if (!cards) throw new Error("页面缺少玩家卡片容器");
      cards.appendChild(card);
      allPlayers.push(buyPlayer);
      sideTargets.push({ player: buyPlayer, rtype: "Buy" });
      registerReplayView(`buy:${domain}`, username, card, buyPlayer, color, `${username} 购买局`);
      players.set(domain, { data, buyPlayer, color });

      for (const round of data.rounds) {
        if (round && round.rtype === "Pistol") pistolRounds.push({ ...round, color });
      }
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
  if (typeof requestAnimationFrame === "function") clock.raf = requestAnimationFrame(tick);
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
  };
}
