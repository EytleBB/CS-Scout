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
let scoutModeByPlatform = { "5e": "auto", perfectworld: "auto" };
let availableMapNames = [];
let pwaCanAnalyze = false;
let pwaSignerReady = false;
let pwaSignerMessage = "正在检测组件…";
let fiveECanAnalyze = false;
let fiveEManualFallback = false;
let fiveENeedsMap = false;
let fiveENeedsExecutable = false;
let fiveEExecutableMessage = "未找到 5E 客户端";
let fiveEUseAnalysisStatus = false;
let manualUsernames = {
  "5e": ["", "", "", "", ""],
  perfectworld: ["", "", "", "", ""]
};
let lastKnownAnalysisRunning = false;
let analysisBusy = false;
let analysisCancellable = false;
let analysisCancelling = false;
let fiveEModeSyncPending = null;
let fiveEConfigVersion = 0;
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

function currentScoutMode() {
  if (!localAnalysisEnabled()) return "manual";
  return scoutModeByPlatform[activePlatform] === "manual" ? "manual" : "auto";
}

function automaticScoutMode() {
  return currentScoutMode() === "auto";
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

function updateRunButton() {
  const runButton = $("#run");
  if (!runButton) return;
  const unavailable =
    (activePlatform === "perfectworld" &&
      (automaticScoutMode() ? !pwaCanAnalyze : !pwaSignerReady)) ||
    (activePlatform === "5e" && automaticScoutMode() &&
      (fiveEManualFallback || !fiveECanAnalyze));
  runButton.disabled = analysisBusy
    ? !analysisCancellable
    : unavailable;
  runButton.textContent = analysisCancelling
    ? "正在取消…"
    : (analysisCancellable ? "取消分析" : "开始分析");
  if (runButton.classList && typeof runButton.classList.toggle === "function") {
    runButton.classList.toggle(
      "cancel-action", analysisCancellable || analysisCancelling
    );
  }
}

function setAnalysisBusy(busy, options = {}) {
  const disabled = Boolean(busy);
  analysisBusy = disabled;
  analysisCancelling = disabled && Boolean(options.cancelling);
  analysisCancellable = disabled && !analysisCancelling &&
    Boolean(options.cancellable);
  updateRunButton();
  for (const button of document.querySelectorAll("[data-analysis-mode]")) {
    button.disabled = disabled || Boolean(fiveEModeSyncPending);
  }
  for (const button of document.querySelectorAll("[data-platform]")) {
    button.disabled = disabled;
  }
  for (const button of document.querySelectorAll("[data-scout-mode]")) {
    button.disabled = disabled;
  }
  for (const button of document.querySelectorAll("#fivee-team-choice button")) {
    button.disabled = disabled;
  }
  const pwaDirectoryButton = $("#pwa-select-directory");
  if (pwaDirectoryButton) pwaDirectoryButton.disabled = disabled;
  const fiveEExecutableButton = $("#fivee-select-executable");
  if (fiveEExecutableButton) fiveEExecutableButton.disabled = disabled;
  const depth = $("#depth");
  if (depth) depth.disabled = disabled;
}

function updatePlatformControls() {
  const perfectWorld = activePlatform === "perfectworld";
  const localFiveE = activePlatform === "5e" && localAnalysisEnabled();
  const automaticPlatform = automaticScoutMode();
  const automaticFiveE = localFiveE && automaticPlatform;
  for (const button of document.querySelectorAll("[data-platform]")) {
    const active = button.dataset.platform === activePlatform;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  for (const section of document.querySelectorAll("[data-five-e-only]")) {
    section.hidden = perfectWorld;
  }
  for (const button of document.querySelectorAll("[data-scout-mode]")) {
    const active = button.dataset.scoutMode === currentScoutMode();
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  const pwaComponent = $("#pwa-component");
  const pwaComponentStatus = $("#pwa-component-status");
  const pwaDirectoryButton = $("#pwa-select-directory");
  if (pwaComponent) pwaComponent.hidden = !perfectWorld;
  if (pwaComponentStatus) pwaComponentStatus.textContent = pwaSignerMessage;
  if (pwaDirectoryButton) {
    pwaDirectoryButton.hidden = pwaSignerReady;
    pwaDirectoryButton.disabled = analysisBusy;
  }
  const fiveEComponent = $("#fivee-component");
  const fiveEComponentStatus = $("#fivee-component-status");
  const fiveEExecutableButton = $("#fivee-select-executable");
  if (fiveEComponent) {
    fiveEComponent.hidden = !automaticFiveE || !fiveENeedsExecutable;
  }
  if (fiveEComponentStatus) {
    fiveEComponentStatus.textContent = fiveEExecutableMessage;
  }
  if (fiveEExecutableButton) fiveEExecutableButton.disabled = analysisBusy;
  const mapSelect = $("#map");
  if (mapSelect) {
    mapSelect.disabled = availableMapNames.length === 0 ||
      (automaticPlatform && (perfectWorld || !fiveENeedsMap));
  }
  const playerLabel = $("#player-input-label");
  if (playerLabel) playerLabel.textContent = "对手";
  for (let index = 0; index < 5; index += 1) {
    const input = $(`#u${index}`);
    if (!input) continue;
    input.readOnly = automaticPlatform;
    input.setAttribute("aria-readonly", String(automaticPlatform));
    input.setAttribute(
      "aria-label",
      `对手 ${index + 1}`
    );
    input.placeholder = automaticPlatform
      ? "等待识别"
      : `用户名 ${index + 1}`;
  }
  updateRunButton();
  const emptyTitle = $("#empty-title");
  if (emptyTitle) emptyTitle.textContent = "等待分析";
}

function shortPlatformStatus(status) {
  const phase = String(status && status.phase || "");
  const message = String(status && status.message || "");
  if (status && status.platform === "fivee" &&
      ["connecting", "manual"].includes(phase) && message) {
    return message;
  }
  const labels = {
    connecting: "正在连接…",
    waiting: "等待对局",
    detected: "正在识别…",
    awaiting_team_selection: "选择你的队伍",
    awaiting_confirmation: "确认对手",
    queued: "正在分析…",
    analyzing: "正在分析…",
    cancelling: "正在取消…",
    ready: "分析完成",
    manual: "输入对手用户名",
  };
  return labels[phase] || message;
}

function showAutomaticTargets(targets) {
  const names = Array.isArray(targets)
    ? targets.slice(0, 5).map(item => String(item && item.username || ""))
    : [];
  for (let index = 0; index < 5; index += 1) {
    const input = $(`#u${index}`);
    if (input) input.value = names[index] || "";
  }
}

const showPerfectWorldTargets = showAutomaticTargets;

function rememberManualTargets(platform = activePlatform) {
  if (currentScoutMode() !== "manual") return;
  manualUsernames[platform] = Array.from({ length: 5 }, (_item, index) => {
    const input = $(`#u${index}`);
    return input ? input.value : "";
  });
}

function restoreManualTargets(platform = activePlatform) {
  const names = manualUsernames[platform] || [];
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

async function selectPerfectWorldDirectory() {
  if (analysisBusy) return;
  const button = $("#pwa-select-directory");
  if (button) button.disabled = true;
  setStatus("请选择完美平台目录…");
  try {
    const result = await requestJSON("/api/pwa/dll/select", {
      method: "POST",
      headers: { "X-CS-Scout-Request": "1" }
    });
    const signer = result && result.signer && typeof result.signer === "object"
      ? result.signer : null;
    if (signer) {
      pwaSignerReady = Boolean(signer.ready);
      pwaSignerMessage = String(signer.message || "未找到完美平台组件");
    }
    updatePlatformControls();
    if (!result.cancelled) setStatus(pwaSignerMessage);
  } catch (error) {
    setStatus(`目录选择失败：${error.message}`);
  } finally {
    if (button) button.disabled = analysisBusy;
    await poll(pollEpoch);
  }
}

async function selectFiveEExecutable() {
  if (analysisBusy) return;
  const button = $("#fivee-select-executable");
  if (button) button.disabled = true;
  setStatus("请选择 5EClient.exe…");
  try {
    const result = await requestJSON("/api/5e/exe/select", {
      method: "POST",
      headers: { "X-CS-Scout-Request": "1" }
    });
    const executable = result && result.executable &&
      typeof result.executable === "object" ? result.executable : null;
    if (executable) {
      fiveENeedsExecutable = !Boolean(executable.ready || executable.found);
      fiveEExecutableMessage = String(
        executable.message || "未找到 5E 客户端"
      );
    }
    updatePlatformControls();
    if (!result.cancelled) setStatus(fiveEExecutableMessage);
  } catch (error) {
    fiveENeedsExecutable = true;
    fiveEExecutableMessage = `选择失败：${error.message}`;
    updatePlatformControls();
    setStatus(fiveEExecutableMessage);
  } finally {
    if (button) button.disabled = analysisBusy;
    await poll(pollEpoch);
  }
}

function renderFiveETeamOptions(options) {
  const container = $("#fivee-team-choice");
  if (!container) return;
  container.replaceChildren();
  const teams = Array.isArray(options) ? options : [];
  for (const team of teams) {
    if (!team || (team.id !== "t1" && team.id !== "t2")) continue;
    const names = Array.isArray(team.players)
      ? team.players.map(player => String(player && player.username || "未知玩家"))
      : [];
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `这是我的队伍：${names.join(" / ")}`;
    button.addEventListener("click", () => { void selectFiveETeam(team.id); });
    container.appendChild(button);
  }
  container.hidden = container.childElementCount === 0;
}

async function selectFiveETeam(team) {
  setAnalysisBusy(true);
  try {
    await requestJSON("/api/5e/team", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team })
    });
    renderFiveETeamOptions([]);
    setStatus("正在识别…");
  } catch (error) {
    setStatus(error.status === 409 ? "当前对局已经更新，请等待重新识别。" : `错误：${error.message}`);
  } finally {
    setAnalysisBusy(false);
    await poll(pollEpoch);
  }
}

async function configureFiveE() {
  const depth = $("#depth");
  const maxDemos = depth ? Number(depth.value) : 6;
  try {
    return await requestJSON("/api/5e/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_demos: maxDemos, mode: analysisMode })
    });
  } catch (error) {
    if (error.status !== 409) throw error;
  }
}

async function chooseAnalysisMode(mode) {
  if (mode !== "normal" && mode !== "fast") return;
  if (analysisBusy || fiveEModeSyncPending) return;
  setAnalysisMode(mode);
  if (activePlatform !== "5e" || !automaticScoutMode()) return;

  fiveEModeSyncPending = mode;
  fiveEConfigVersion += 1;
  setAnalysisBusy(analysisBusy);
  try {
    const configured = await configureFiveE();
    if (configured && (configured.mode === "normal" || configured.mode === "fast")) {
      setAnalysisMode(configured.mode);
    }
  } catch (error) {
    setStatus(`切换失败：${error.message}`);
  } finally {
    fiveEModeSyncPending = null;
    // Any poll started before or during this request must not overwrite the
    // newly confirmed mode with its older status snapshot.
    fiveEConfigVersion += 1;
    setAnalysisBusy(analysisBusy);
  }
}

async function setScoutMode(mode) {
  if (analysisBusy || !localAnalysisEnabled()) return;
  if (mode !== "auto" && mode !== "manual") return;
  if (currentScoutMode() === mode) return;
  if (currentScoutMode() === "manual") rememberManualTargets();

  scoutModeByPlatform[activePlatform] = mode;
  pwaCanAnalyze = false;
  fiveECanAnalyze = false;
  fiveENeedsMap = false;
  fiveEManualFallback = false;
  fiveEUseAnalysisStatus = false;
  renderFiveETeamOptions([]);
  if (mode === "manual") restoreManualTargets();
  else showAutomaticTargets([]);

  pollEpoch += 1;
  const epoch = pollEpoch;
  clearPollTimer();
  lastKnownAnalysisRunning = false;
  resetResults();
  updatePlatformControls();
  setAnalysisBusy(false);

  if (mode === "manual") {
    setStatus("输入对手用户名");
  } else {
    setStatus("正在连接…");
    try {
      if (activePlatform === "perfectworld") await configurePerfectWorld();
      else await configureFiveE();
    } catch (error) {
      if (epoch !== pollEpoch) return;
      if (activePlatform === "5e") fiveEManualFallback = true;
      setStatus("自动连接失败，请切换手动模式");
    }
  }
  updatePlatformControls();
  if (epoch === pollEpoch) await poll(epoch);
}

async function setPlatform(platform) {
  if (analysisBusy) return;
  if (platform !== "5e" && platform !== "perfectworld") return;
  if (platform === activePlatform) return;
  if (currentScoutMode() === "manual") rememberManualTargets();
  activePlatform = platform;
  pwaCanAnalyze = false;
  if (platform === "perfectworld") {
    pwaSignerReady = false;
    pwaSignerMessage = "正在检测组件…";
  }
  fiveECanAnalyze = false;
  fiveENeedsMap = false;
  fiveEUseAnalysisStatus = false;
  renderFiveETeamOptions([]);
  fiveEManualFallback = false;
  if (automaticScoutMode()) showAutomaticTargets([]);
  else restoreManualTargets();
  pollEpoch += 1;
  const epoch = pollEpoch;
  clearPollTimer();
  lastKnownAnalysisRunning = false;
  resetResults();
  updatePlatformControls();
  setAnalysisBusy(false);
  if (!automaticScoutMode()) {
    setStatus("输入对手用户名");
  } else if (platform === "perfectworld") {
    setStatus("正在连接…");
    try {
      await configurePerfectWorld();
    } catch (error) {
      if (epoch !== pollEpoch) return;
      setStatus(`连接失败：${error.message}`);
    }
  } else if (localAnalysisEnabled()) {
    setStatus("正在连接…");
    try {
      await configureFiveE();
    } catch (error) {
      if (epoch !== pollEpoch) return;
      fiveEManualFallback = true;
      updatePlatformControls();
      setStatus("自动连接失败，请切换手动模式");
    }
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
    setStatus("正在分析…");
    await poll(pollEpoch);
  } catch (error) {
    lastKnownAnalysisRunning = false;
    setStatus(error.status === 409
      ? (pwaSignerReady ? "当前名单尚未就绪或分析已经开始。" : pwaSignerMessage)
      : `错误：${error.message}`);
    await poll(pollEpoch);
  }
}

async function runFiveEAutomaticAnalysis() {
  fiveECanAnalyze = false;
  setAnalysisBusy(true);
  try {
    await configureFiveE();
    const mapSelect = $("#map");
    await requestJSON("/api/5e/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ map: mapSelect ? mapSelect.value : "" })
    });
    fiveEUseAnalysisStatus = true;
    lastKnownAnalysisRunning = true;
    pollEpoch += 1;
    clearPollTimer();
    resetResults();
    setStatus("正在分析…");
    await poll(pollEpoch);
  } catch (error) {
    fiveEUseAnalysisStatus = false;
    lastKnownAnalysisRunning = false;
    setStatus(error.status === 409 ? "当前名单尚未就绪或分析已经开始。" : `错误：${error.message}`);
    setAnalysisBusy(false);
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
  const scoutModeButtons = document.querySelectorAll("[data-scout-mode]");
  const pwaDirectoryButton = $("#pwa-select-directory");
  const fiveEExecutableButton = $("#fivee-select-executable");
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
    button.addEventListener("click", () => { void chooseAnalysisMode(button.dataset.analysisMode); });
  }
  for (const button of platformButtons) {
    button.addEventListener("click", () => { void setPlatform(button.dataset.platform); });
  }
  for (const button of scoutModeButtons) {
    button.addEventListener("click", () => { void setScoutMode(button.dataset.scoutMode); });
  }
  if (pwaDirectoryButton) {
    pwaDirectoryButton.addEventListener("click", () => {
      void selectPerfectWorldDirectory();
    });
  }
  if (fiveEExecutableButton) {
    fiveEExecutableButton.addEventListener("click", () => {
      void selectFiveEExecutable();
    });
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

function progressForStep(item) {
  const step = Number(item && item.step);
  const message = String(item && item.msg || "");
  const fractionMatch = message.match(/(\d+)\s*\/\s*(\d+)/);
  const current = fractionMatch ? Number(fractionMatch[1]) : 0;
  const total = fractionMatch ? Number(fractionMatch[2]) : 0;
  const fraction = total > 0 ? Math.max(0, Math.min(1, current / total)) : 0;
  const percentMatch = message.match(/·\s*(\d+(?:\.\d+)?)%/);
  const byteFraction = percentMatch
    ? Math.max(0, Math.min(1, Number(percentMatch[1]) / 100))
    : 0;
  if (step <= 0) return 5;
  if (step === 1) return 15;
  if (step === 2) return 25;
  if (step === 3) return 25 + 35 * Math.max(fraction, byteFraction);
  if (step === 4) return 60 + 30 * fraction;
  if (step === 5) return 95;
  if (step === 6) return 100;
  return 0;
}

function progressFromMessage(message) {
  const text = String(message || "");
  const fractionMatch = text.match(/(\d+)\s*\/\s*(\d+)/);
  const fraction = fractionMatch && Number(fractionMatch[2]) > 0
    ? Math.max(0, Math.min(1, Number(fractionMatch[1]) / Number(fractionMatch[2])))
    : 0;
  if (text.includes("完成")) return 100;
  if (text.includes("解析")) return 60 + 35 * fraction;
  if (text.includes("下载")) return 20 + 40 * fraction;
  if (text.includes("查询") || text.includes("识别")) return 12;
  return 5;
}

function renderAnalysisProgress(status, running) {
  const panel = $("#progress-panel");
  const track = $("#progress-track");
  const fill = $("#progress-fill");
  const count = $("#progress-count");
  if (!panel || !track || !fill || !count) return;

  const value = status && typeof status === "object" ? status : {};
  const progress = Array.isArray(value.progress) ? value.progress : [];
  const results = Array.isArray(value.results) ? value.results : [];
  const failures = Array.isArray(value.failed) ? value.failed : [];
  const targetCount = Array.isArray(value.targets) ? value.targets.length : 0;
  const total = Math.max(
    0,
    Number(value.total_players) || targetCount || progress.length ||
      results.length + failures.length
  );
  const progressCompleted = progress.filter(
    item => Number(item && item.step) === 6
  ).length;
  const completed = Math.min(
    total || Number.MAX_SAFE_INTEGER,
    Math.max(results.length + failures.length, progressCompleted)
  );
  const finished = value.status === "done" || value.phase === "ready";
  const cancelled = value.status === "cancelled";
  let percent = 0;

  if (finished && (total > 0 || results.length + failures.length > 0)) {
    percent = 100;
  } else if (running || cancelled || value.status === "error" || value.phase === "error") {
    if (progress.length > 0 && total > 0) {
      const completedNames = new Set([
        ...results.map(item => String(item && item.username || "")),
        ...failures.map(item => String(item && item.username || "")),
      ]);
      const scores = new Map();
      for (const item of progress) {
        const name = String(item && item.id || "");
        scores.set(name, completedNames.has(name) ? 100 : progressForStep(item));
      }
      for (const name of completedNames) {
        if (name && !scores.has(name)) scores.set(name, 100);
      }
      let sum = 0;
      for (const score of scores.values()) sum += score;
      percent = sum / total;
    } else {
      percent = progressFromMessage(value.message);
    }
  }

  percent = Math.round(Math.max(0, Math.min(100, percent)));
  fill.style.width = `${percent}%`;
  track.setAttribute("aria-valuenow", String(percent));
  count.textContent = cancelled
    ? "已取消"
    : (total > 0
    ? `${completed}/${total} · ${percent}%`
    : (running ? `${percent}%` : "—"));
  panel.dataset.state = value.status === "error" || value.phase === "error"
    ? "error" : (cancelled ? "cancelled" :
      (running ? "running" : (finished ? "done" : "idle")));

  if (running && progress.length > 0) {
    const latest = [...progress].sort((left, right) =>
      Number(right && right.updated_at || 0) - Number(left && left.updated_at || 0)
    )[0];
    const name = String(latest && latest.id || "");
    const message = String(latest && latest.msg || "").replace(/\.{3}|…/g, "");
    setStatus(name ? `${name} · ${message}` : message);
  }
}

function renderFailures() {
  const target = $("#failed");
  if (!target) return;
  const details = $("#failure-details");
  const summary = $("#failure-summary");
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
    line.textContent = `${failure.username}：${failure.reason}`;
    target.appendChild(line);
  }
  if (details) {
    details.hidden = failures.length === 0;
    if (failures.length === 0) details.open = false;
  }
  if (summary) summary.textContent = `失败 ${failures.length}`;
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
    updatePlatformControls();
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
  const changed = activeViewKey !== null && activeViewKey !== viewKey;
  activeViewKey = viewKey;
  for (const [key, view] of replayViews) {
    const active = key === viewKey;
    view.panel.hidden = !active;
    view.button.classList.toggle("active", active);
    view.button.setAttribute("aria-pressed", String(active));
  }
  if (changed) {
    clock.elapsed = 0;
    clock.playing = true;
    clock.last = null;
    updateClockControls();
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

async function cancelAnalysis() {
  if (!analysisCancellable || analysisCancelling) return;
  const key = $("#key");
  if (!localAnalysisEnabled()) {
    accessKey = key && typeof key.value === "string" ? key.value.trim() : "";
  }
  setAnalysisBusy(true, { cancelling: true });
  setStatus("正在取消分析…");
  try {
    const options = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    };
    if (localAnalysisEnabled()) await requestJSON("/api/cancel", options);
    else await requestProtectedJSON("/api/cancel", options);
  } catch (error) {
    setStatus(`取消失败：${error.message}`);
  } finally {
    await poll(pollEpoch);
  }
}

async function runAnalysis() {
  if (analysisCancelling) return;
  if (analysisCancellable) {
    await cancelAnalysis();
    return;
  }
  if (analysisBusy) return;
  if (activePlatform === "perfectworld" && automaticScoutMode()) {
    await runPerfectWorldAnalysis();
    return;
  }
  if (activePlatform === "5e" && automaticScoutMode()) {
    await runFiveEAutomaticAnalysis();
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
    if (activePlatform === "perfectworld") {
      await requestJSON("/api/pwa/manual/analyze", options);
    } else if (localAnalysisEnabled()) await requestJSON("/api/analyze", options);
    else await requestProtectedJSON("/api/analyze", options);
    fiveEUseAnalysisStatus = activePlatform === "5e" && localAnalysisEnabled();
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
  const configVersionAtStart = fiveEConfigVersion;
  try {
    const perfectWorld = activePlatform === "perfectworld";
    const localFiveE = activePlatform === "5e" && localAnalysisEnabled();
    const automaticMode = automaticScoutMode();
    const status = perfectWorld
      ? await requestJSON("/api/pwa/status")
      : (localFiveE
        ? await requestJSON("/api/5e/status")
        : await requestJSON("/api/status"));
    if (epoch !== pollEpoch) return;
    const analysisStatus = (perfectWorld || localFiveE) && status.analysis &&
      typeof status.analysis === "object" ? status.analysis : null;
    const resultStatus = analysisStatus || status;
    const concisePlatformStatus = automaticMode &&
      (perfectWorld || localFiveE) && !analysisStatus
      ? shortPlatformStatus(status)
      : "";
    const manualIdleStatus = localAnalysisEnabled() &&
      !automaticMode && !analysisStatus
      ? "输入对手用户名" : "";
    setStatus(manualIdleStatus || concisePlatformStatus ||
      resultStatus.message || status.message || resultStatus.status || "");
    const cancelling = status.phase === "cancelling" ||
      resultStatus.status === "cancelling";
    const activeAnalysis = perfectWorld
      ? (automaticMode && ["queued", "analyzing"].includes(status.phase)) ||
        Boolean(analysisStatus && analysisStatus.status === "running")
      : (localFiveE
        ? (automaticMode && ["queued", "analyzing"].includes(status.phase)) ||
          Boolean(analysisStatus && analysisStatus.status === "running")
        : status.status === "running");
    const platformRunning = perfectWorld
      ? (automaticMode && ["detected", "queued", "analyzing", "cancelling"].includes(status.phase)) ||
        Boolean(analysisStatus && ["running", "cancelling"].includes(
          analysisStatus.status
        ))
      : (localFiveE
        ? (automaticMode && ["queued", "analyzing", "cancelling"].includes(status.phase)) ||
          Boolean(analysisStatus && ["running", "cancelling"].includes(
            analysisStatus.status
          ))
        : ["running", "cancelling"].includes(status.status));
    const running = platformRunning || Boolean(status.analysis_busy);
    renderAnalysisProgress(resultStatus, running);
    if (automaticMode && (perfectWorld || localFiveE) && status.map) {
      const mapSelect = $("#map");
      if (mapSelect && availableMapNames.includes(String(status.map))) {
        mapSelect.value = String(status.map);
      }
    }
    if (perfectWorld) {
      const signer = status.signer && typeof status.signer === "object"
        ? status.signer : {};
      pwaSignerReady = Boolean(signer.ready);
      pwaSignerMessage = String(signer.message || "正在检测组件…");
      pwaCanAnalyze = automaticMode && pwaSignerReady &&
        status.phase === "awaiting_confirmation";
      if (automaticMode) showAutomaticTargets(status.targets);
      updatePlatformControls();
      const emptyTitle = $("#empty-title");
      if (automaticMode && emptyTitle && status.phase === "awaiting_confirmation") {
        emptyTitle.textContent = "确认对手";
      }
    } else if (localFiveE) {
      fiveEManualFallback = automaticMode && Boolean(status.manual_fallback);
      fiveENeedsExecutable = automaticMode && Boolean(status.needs_executable);
      fiveEExecutableMessage = String(status.message || "未找到 5E 客户端");
      fiveENeedsMap = automaticMode && Boolean(status.needs_map);
      fiveECanAnalyze = automaticMode &&
        status.phase === "awaiting_confirmation";
      if (automaticMode && !fiveEManualFallback) showAutomaticTargets(status.targets);
      renderFiveETeamOptions(automaticMode ? status.team_options : []);
      updatePlatformControls();
      const emptyTitle = $("#empty-title");
      if (automaticMode && emptyTitle && status.phase === "awaiting_confirmation") {
        emptyTitle.textContent = "确认对手";
      } else if (automaticMode && emptyTitle && status.phase === "awaiting_team_selection") {
        emptyTitle.textContent = "选择你的队伍";
      }
      const reportedMode = analysisStatus ? analysisStatus.mode : status.mode;
      if (!fiveEModeSyncPending && configVersionAtStart === fiveEConfigVersion &&
          (reportedMode === "normal" || reportedMode === "fast")) {
        setAnalysisMode(reportedMode);
      }
    }
    if (running && !lastKnownAnalysisRunning && players.size > 0) {
      resetResults();
    }
    lastKnownAnalysisRunning = running;
    if (!perfectWorld && !localFiveE && running &&
        (resultStatus.mode === "normal" || resultStatus.mode === "fast")) {
      setAnalysisMode(resultStatus.mode);
    }
    setAnalysisBusy(running, {
      cancellable: activeAnalysis && !cancelling,
      cancelling
    });
    serverFailures = Array.isArray(resultStatus.failed) ? resultStatus.failed : [];
    let retryNeeded = false;
    const results = Array.isArray(resultStatus.results) ? resultStatus.results : [];
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

    const completedLocalFiveE = localFiveE && analysisStatus && !platformRunning &&
      ["done", "error", "cancelled"].includes(analysisStatus.status);
    if (completedLocalFiveE) fiveEUseAnalysisStatus = false;

    if (running) schedulePoll(epoch, perfectWorld ? 1000 : 2000);
    else if (retryNeeded) schedulePoll(epoch, 2500);
    else if (perfectWorld) schedulePoll(epoch, status.phase === "error" ? 3000 : 1500);
    else if (localFiveE && publicMonitoringEnabled) schedulePoll(epoch, 1500);
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
      if (!automaticScoutMode()) return;
      if (activePlatform === "perfectworld") void configurePerfectWorld();
      else if (activePlatform === "5e") void configureFiveE();
    });
  }
  // Viewing current progress and the latest completed replay is public. The
  // access key is only read when the visitor starts a new analysis.
  publicMonitoringEnabled = true;
  void (async () => {
    await loadMaps();
    if (activePlatform === "5e" && automaticScoutMode()) {
      fiveEManualFallback = false;
      updatePlatformControls();
      setStatus("正在连接…");
      try {
        await configureFiveE();
      } catch (error) {
        fiveEManualFallback = true;
        updatePlatformControls();
        setStatus("自动连接失败，请切换手动模式");
      }
    }
    void poll(pollEpoch);
  })();
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
    wireControls, setAnalysisMode, chooseAnalysisMode, setAnalysisBusy,
    runAnalysis, cancelAnalysis,
    connectWithEnteredKey, setPlatform, setScoutMode, updatePlatformControls,
    showPerfectWorldTargets, showAutomaticTargets, runPerfectWorldAnalysis,
    selectPerfectWorldDirectory, selectFiveEExecutable,
    runFiveEAutomaticAnalysis, renderFiveETeamOptions,
    progressForStep, progressFromMessage, renderAnalysisProgress,
  };
}
