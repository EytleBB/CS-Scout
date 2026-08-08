"use strict";

// Standalone local demo viewer frontend.
// Reuses the replay-engine modules (engine.js, clock.js, heatmap.js)
// loaded via <script> tags before this file.
(function() {
const engine = (typeof window !== "undefined" && window.__replayEngine) || {};
const createReplay = engine.createReplay;
const createClock = engine.createClock;
const createHeatmap = engine.createHeatmap;

if (!createReplay || !createClock || !createHeatmap) {
  document.addEventListener("DOMContentLoaded", () => {
    document.body.insertAdjacentHTML("afterbegin",
      '<div style="position:fixed;top:0;left:0;right:0;z-index:9999;padding:12px;background:#ff4444;color:#fff;font:14px monospace">' +
      'Engine load error: missing ' +
      ['createReplay','createClock','createHeatmap'].filter(k => !engine[k]).join(', ') +
      '</div>');
  }, { once: true });
}

const $ = sel => document.querySelector(sel);
const $$ = sel => document.querySelectorAll(sel);

// --- State ---
let uploadedFiles = [];       // [{name, size, content}]
let inspectedInfo = null;     // {map, players, files}
let playerData = null;        // parsed player JSON
let replayClock = null;
let activeReplay = null;
let heatmapInstance = null;
let currentSide = "CT";
let views = {};               // key -> {panel, replay, canvas}
let activeViewKey = null;

const PLAYER_COLORS = [
  "#55b8ff", "#ffd166", "#ff8c42", "#a06bff",
  "#6bff9e", "#ff6b9d", "#6bffd4", "#c4ff6b"
];

// --- Clock setup ---
function initClock() {
  if (replayClock) return;
  const PLAYBACK_S = engine.PLAYBACK_S || 10;
  const WINDOW_S = engine.WINDOW_S || 20;
  replayClock = createClock({
    playbackS: PLAYBACK_S,
    windowS: WINDOW_S,
    onTick: gameTime => {
      drawActive(gameTime);
      const scrubber = $("#scrubber");
      const label = $("#time-label");
      if (scrubber) scrubber.value = Math.round(gameTime / PLAYBACK_S * 1000);
      if (label) label.textContent = gameTime.toFixed(1) + "s";
    },
    onControlsUpdate: updateClockControls
  });
}

function updateClockControls() {
  const btn = $("#play-pause");
  if (!btn || !replayClock) return;
  btn.textContent = replayClock.playing ? "⏸ 暂停" : "▶ 播放";
  $$(".speed-btn").forEach(b => {
    b.classList.toggle("active", Number(b.dataset.speed) === replayClock.speed);
  });
}

function drawActive(gameTime) {
  if (activeViewKey && views[activeViewKey]) {
    const v = views[activeViewKey];
    if (v.replay) v.replay.drawAt(gameTime);
    if (v.heatmap) v.heatmap.drawAt(gameTime);
  }
}

// --- File upload ---
function setupUpload() {
  const dropZone = $("#drop-zone");
  const fileInput = $("#file-input");

  dropZone.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) handleFiles(fileInput.files);
  });

  dropZone.addEventListener("dragover", e => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", e => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  });
}

async function handleFiles(fileList) {
  const demFiles = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith(".dem"));
  if (!demFiles.length) {
    setStatus("请上传 .dem 文件", "error");
    return;
  }

  uploadedFiles = demFiles.map(f => ({ name: f.name, size: f.size, file: f }));
  renderFileList();

  // Inspect demos
  setStatus("正在检测 Demo...");
  const formData = new FormData();
  for (const f of demFiles) formData.append("demos", f);

  try {
    const res = await fetch("/api/inspect", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "检测失败");

    inspectedInfo = data;
    renderPlayerSelect(data.players);
    setStatus(`检测完成: ${data.map}, ${data.players.length} 名玩家, ${data.files.length} 个 Demo`, "success");
  } catch (err) {
    setStatus("检测失败: " + err.message, "error");
  }
}

function renderFileList() {
  const list = $("#file-list");
  list.replaceChildren();
  for (const f of uploadedFiles) {
    const item = document.createElement("div");
    item.className = "file-item";
    item.textContent = `${f.name} (${formatBytes(f.size)})`;
    list.appendChild(item);
  }
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KiB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GiB`;
}

function renderPlayerSelect(players) {
  const panel = $("#player-panel");
  const select = $("#player-select");
  const btn = $("#parse-btn");

  panel.style.display = "";
  select.replaceChildren();

  // Sort by appearances (desc), then username
  players.sort((a, b) => b.appearances - a.appearances || a.username.localeCompare(b.username));

  for (const p of players) {
    const opt = document.createElement("option");
    opt.value = p.steamid;
    opt.textContent = `${p.username} (${p.appearances}场)`;
    select.appendChild(opt);
  }

  btn.disabled = false;
  btn.addEventListener("click", parseDemo, { once: true });
}

// --- Parse demo ---
async function parseDemo() {
  const steamid = $("#player-select").value;
  const username = $("#player-select").selectedOptions[0].textContent.split(" (")[0];
  if (!steamid || !inspectedInfo) return;

  const btn = $("#parse-btn");
  btn.disabled = true;
  setStatus("正在解析回放...");

  const formData = new FormData();
  for (const f of uploadedFiles) formData.append("demos", f.file);
  formData.append("steamid", steamid);
  formData.append("username", username);
  formData.append("map", inspectedInfo.map);

  try {
    const res = await fetch("/api/parse", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "解析失败");

    playerData = data;
    renderViews();
    setStatus(`解析完成: ${data.round_count} 回合, K/D ${data.combat_stats.kd}`, "success");
  } catch (err) {
    setStatus("解析失败: " + err.message, "error");
    btn.disabled = false;
  }
}

// --- View rendering ---
function renderViews() {
  if (!playerData) return;

  const toolbar = $("#view-toolbar");
  const empty = $("#empty-state");
  toolbar.classList.remove("hidden");
  empty.classList.add("hidden");

  // Clear old views
  toolbar.querySelectorAll(".view-btn:not(.side-btn)").forEach(b => b.remove());
  views = {};
  const workspace = $("#workspace");
  workspace.querySelectorAll(".replay-panel").forEach(p => p.remove());

  initClock();

  const rounds = playerData.rounds || [];

  // --- Pistol view (all players) ---
  const pistolRounds = rounds.filter(r => r.rtype === "Pistol");
  if (pistolRounds.length) {
    const key = "pistol";
    const btn = document.createElement("button");
    btn.className = "view-btn active";
    btn.textContent = "手枪局 (全员)";
    btn.addEventListener("click", () => switchView(key));
    toolbar.insertBefore(btn, $("#side-toggle"));

    const panel = document.createElement("div");
    panel.className = "replay-panel active";
    const canvas = document.createElement("canvas");
    canvas.className = "replay-canvas";
    canvas.width = 1024; canvas.height = 1024;
    panel.appendChild(canvas);

    // Color each pistol round distinctly
    const coloredRounds = pistolRounds.map((r, i) => ({
      ...r, color: PLAYER_COLORS[i % PLAYER_COLORS.length]
    }));

    const replay = createReplay(canvas, {
      radar: playerData.radar,
      transform: playerData.transform,
      rounds: coloredRounds,
      side: currentSide,
      rtype: "Pistol"
    });

    // Legend
    const legend = document.createElement("div");
    legend.id = "legend";
    coloredRounds.forEach((r, i) => {
      const item = document.createElement("div");
      item.className = "legend-item";
      item.innerHTML = `<span class="legend-dot" style="background:${PLAYER_COLORS[i % PLAYER_COLORS.length]}"></span>R${r.round_id}`;
      legend.appendChild(item);
    });
    panel.appendChild(legend);

    workspace.appendChild(panel);
    views[key] = { panel, replay, canvas };
    activeViewKey = key;
  }

  // --- Heatmap view ---
  const allRounds = rounds;
  if (allRounds.length) {
    const key = "heatmap";
    const btn = document.createElement("button");
    btn.className = "view-btn";
    if (!pistolRounds.length) btn.classList.add("active");
    btn.textContent = "热力图";
    btn.addEventListener("click", () => switchView(key));
    toolbar.insertBefore(btn, $("#side-toggle"));

    const panel = document.createElement("div");
    panel.className = "replay-panel";
    if (!pistolRounds.length) panel.classList.add("active");
    const canvas = document.createElement("canvas");
    canvas.className = "replay-canvas";
    canvas.width = 1024; canvas.height = 1024;
    panel.appendChild(canvas);

    const hm = createHeatmap(canvas, {
      radar: playerData.radar,
      transform: playerData.transform,
      rounds: allRounds,
      side: currentSide
    });

    workspace.appendChild(panel);
    views[key] = { panel, heatmap: hm, canvas };
    if (!pistolRounds.length) activeViewKey = key;
  }

  replayClock.setElapsed(0);
  replayClock.start();
  updateClockControls();
}

function switchView(key) {
  if (!views[key]) return;
  activeViewKey = key;
  $$(".view-btn:not(.side-btn)").forEach(b => b.classList.remove("active"));
  $$(".replay-panel").forEach(p => p.classList.remove("active"));
  // Find the button that matches
  const btns = $$(".view-btn:not(.side-btn)");
  const keys = Object.keys(views);
  const idx = keys.indexOf(key);
  if (idx >= 0 && btns[idx]) btns[idx].classList.add("active");
  views[key].panel.classList.add("active");
  drawActive(replayClock ? replayClock.elapsed : 0);
}

// --- Controls ---
function setupControls() {
  $("#play-pause").addEventListener("click", () => {
    if (!replayClock) return;
    if (replayClock.playing) replayClock.pause();
    else replayClock.start();
    updateClockControls();
  });

  $$(".speed-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (!replayClock) return;
      replayClock.setSpeed(Number(btn.dataset.speed));
      updateClockControls();
    });
  });

  $("#scrubber").addEventListener("input", () => {
    if (!replayClock) return;
    const PLAYBACK_S = engine.PLAYBACK_S || 10;
    const t = Number($("#scrubber").value) / 1000 * PLAYBACK_S;
    replayClock.seek(t);
  });

  $$(".side-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const side = btn.dataset.side;
      if (side === currentSide) return;
      currentSide = side;
      $$(".side-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      for (const key of Object.keys(views)) {
        const v = views[key];
        if (v.replay) v.replay.setFilter(side, v.replay.rtype);
        if (v.heatmap) v.heatmap.setFilter(side);
      }
      drawActive(replayClock ? replayClock.elapsed : 0);
    });
  });
}

function setStatus(msg, type) {
  const el = $("#status-msg");
  el.textContent = msg;
  el.className = type || "";
}

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
  setupUpload();
  setupControls();
});

if (typeof module !== "undefined") {
  module.exports = { handleFiles, parseDemo, renderViews, switchView };
}
})();
