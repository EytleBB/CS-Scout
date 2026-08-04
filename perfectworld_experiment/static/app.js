const ui = {
  phase: document.getElementById("phase"),
  pulse: document.getElementById("pulse"),
  message: document.getElementById("message"),
  detail: document.getElementById("detail"),
  players: document.getElementById("players"),
  viewer: document.getElementById("viewer"),
  failed: document.getElementById("failed"),
  start: document.getElementById("start-analysis")
};

const model = {
  side: "CT",
  rtype: "Buy",
  speed: 2,
  loaded: new Map(),
  activeDomain: null,
  resultSignature: "",
  startedAt: performance.now()
};

function phaseLabel(phase) {
  return ({waiting: "等待对局", detected: "识别中", awaiting_confirmation: "等待确认", queued: "准备中", analyzing: "分析中", ready: "已完成", error: "需要注意"})[phase] || phase;
}

function setActiveButtons(selector, attribute, value) {
  document.querySelectorAll(selector).forEach(button => {
    button.classList.toggle("active", button.dataset[attribute] === String(value));
  });
}

function showPlayer(domain) {
  model.activeDomain = domain;
  ui.players.querySelectorAll("button").forEach(button => {
    button.classList.toggle("active", button.dataset.domain === domain);
  });
  const entry = model.loaded.get(domain);
  if (!entry) return;
  ui.viewer.classList.remove("empty");
  ui.viewer.replaceChildren(entry.element);
  model.startedAt = performance.now();
}

async function loadPlayer(result) {
  if (model.loaded.has(result.domain)) return;
  const response = await fetch(`/api/player/${encodeURIComponent(result.domain)}`);
  if (!response.ok) throw new Error("玩家数据尚未就绪");
  const data = await response.json();
  const element = document.createElement("article");
  element.className = "player-view";
  element.innerHTML = `
    <aside class="player-meta">
      <h2></h2>
      <div class="metric"><span>K / D</span><strong class="kd"></strong></div>
      <div class="metric"><span>AWP 持有率</span><strong class="awp"></strong></div>
      <div class="metric"><span>有效回合</span><strong class="rounds"></strong></div>
      <div class="metric"><span>历史 Demo</span><strong class="demos"></strong></div>
    </aside>
    <div class="radar-wrap"><canvas width="1024" height="1024"></canvas></div>`;
  element.querySelector("h2").textContent = data.username;
  element.querySelector(".kd").textContent = Number(data.combat_stats?.kd || 0).toFixed(2);
  element.querySelector(".awp").textContent = `${Number(data.combat_stats?.awp_rate || 0).toFixed(1)}%`;
  element.querySelector(".rounds").textContent = data.round_count || 0;
  element.querySelector(".demos").textContent = data.demos_found || 0;
  const player = new ReplayPlayer(element.querySelector("canvas"), {
    radar: data.radar,
    transform: data.transform,
    rounds: data.rounds,
    side: model.side,
    rtype: model.rtype
  });
  model.loaded.set(result.domain, {element, player, data});

  const button = document.createElement("button");
  button.type = "button";
  button.dataset.domain = result.domain;
  button.textContent = data.username;
  button.addEventListener("click", () => showPlayer(result.domain));
  ui.players.append(button);
  if (!model.activeDomain) showPlayer(result.domain);
}

async function syncResults(status) {
  const signature = (status.results || []).map(item => item.domain).join("|");
  if (signature !== model.resultSignature) {
    model.resultSignature = signature;
    for (const result of status.results || []) await loadPlayer(result);
  }
  const failed = status.failed || [];
  ui.failed.hidden = failed.length === 0;
  ui.failed.textContent = failed.length ? `未完成：${failed.map(item => `${item.username}（${item.reason}）`).join("；")}` : "";
}

async function poll() {
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    const status = await response.json();
    ui.phase.textContent = phaseLabel(status.phase);
    ui.message.textContent = status.message;
    const details = [];
    if (status.map) details.push(status.map);
    if (status.roster_count) details.push(`已读取 ${status.roster_count} 人阵容`);
    if (status.targets?.length) details.push(`对手：${status.targets.map(item => item.username).join("、")}`);
    ui.detail.textContent = details.length ? details.join(" · ") : "进入完美平台对局后会自动识别对手，确认后开始。";
    ui.start.disabled = status.phase !== "awaiting_confirmation";
    ui.pulse.className = `pulse ${["waiting", "detected", "queued", "analyzing"].includes(status.phase) ? "busy" : ""} ${status.phase === "error" ? "error" : ""}`;
    await syncResults(status);
  } catch (_) {
    ui.message.textContent = "正在连接本地自动侦察服务…";
  } finally {
    window.setTimeout(poll, 1200);
  }
}

ui.start.addEventListener("click", async () => {
  ui.start.disabled = true;
  try {
    await fetch("/api/analyze", {method: "POST"});
  } catch (_) {
    ui.message.textContent = "无法开始分析，请重新确认当前对局。";
  }
});

document.querySelectorAll("[data-side]").forEach(button => button.addEventListener("click", () => {
  model.side = button.dataset.side;
  setActiveButtons("[data-side]", "side", model.side);
  model.loaded.forEach(entry => entry.player.setFilter(model.side, model.rtype));
}));
document.querySelectorAll("[data-rtype]").forEach(button => button.addEventListener("click", () => {
  model.rtype = button.dataset.rtype;
  setActiveButtons("[data-rtype]", "rtype", model.rtype);
  model.loaded.forEach(entry => entry.player.setFilter(model.side, model.rtype));
}));
document.querySelectorAll("[data-speed]").forEach(button => button.addEventListener("click", () => {
  model.speed = Number(button.dataset.speed);
  setActiveButtons("[data-speed]", "speed", model.speed);
  model.startedAt = performance.now();
}));

function animate(now) {
  const entry = model.loaded.get(model.activeDomain);
  if (entry) {
    const loopSeconds = typeof PLAYBACK_S === "number" ? PLAYBACK_S : 10;
    const windowSeconds = typeof WINDOW_S === "number" ? WINDOW_S : 20;
    const elapsed = ((now - model.startedAt) / 1000 * model.speed) % loopSeconds;
    entry.player.drawAt(elapsed / loopSeconds * windowSeconds);
  }
  requestAnimationFrame(animate);
}

poll();
requestAnimationFrame(animate);
