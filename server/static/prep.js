/**
 * Pre-match panel rendering.
 *
 * Kept in its own file so the same renderer drives the live page and the
 * offline preview (prep_test.html).  Browser: exposes globals; Node: module.
 */

const PREP_RADAR_ORDER = [
  ["fire_power", "火力"],
  ["marksmanship", "枪法"],
  ["follow_up_shot", "补枪"],
  ["first", "开路"],
  ["item", "道具"],
  ["1vn", "残局"],
  ["sniper", "狙击"]
];

function prepRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

function prepNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function drawPrepRadar(canvas, radar) {
  const ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) return;
  const size = canvas.width;
  const center = size / 2;
  const radius = size / 2 - 5;
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = "rgba(255,255,255,.16)";
  ctx.lineWidth = 1;
  for (const ratio of [0.5, 1]) {
    ctx.beginPath();
    ctx.arc(center, center, radius * ratio, 0, Math.PI * 2);
    ctx.stroke();
  }
  const dims = PREP_RADAR_ORDER
    .map(([key]) => (radar && radar[key] ? radar[key].score : null))
    .filter((score) => score !== null && score !== undefined);
  // Fewer than three axes cannot form a readable shape.
  if (dims.length < 3) return;
  const points = dims.map((score, index) => {
    const angle = (Math.PI * 2 * index) / dims.length - Math.PI / 2;
    const scaled = radius * Math.max(0, Math.min(100, Number(score))) / 100;
    return [center + Math.cos(angle) * scaled, center + Math.sin(angle) * scaled];
  });
  ctx.beginPath();
  points.forEach(([x, y], index) => (index ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
  ctx.fillStyle = "rgba(78,168,222,.28)";
  ctx.strokeStyle = "rgba(120,200,240,.9)";
  ctx.fill();
  ctx.stroke();
}

function buildPrepRow(player) {
  const row = document.createElement("article");
  row.className = "prep-row";
  row.dataset.hasStats = String(Boolean(player.has_stats));

  const canvas = document.createElement("canvas");
  canvas.className = "prep-radar";
  canvas.width = 52;
  canvas.height = 52;
  canvas.setAttribute("aria-hidden", "true");

  const who = document.createElement("div");
  who.className = "prep-who";
  const name = document.createElement("strong");
  name.textContent = player.nickname || (player.steam_id ? `…${String(player.steam_id).slice(-6)}` : "未知玩家");
  name.title = name.textContent;
  const facts = document.createElement("span");
  const parts = [];
  if (player.score !== null && player.score !== undefined) parts.push(`分 ${player.score}`);
  if (player.adpr !== null && player.adpr !== undefined) parts.push(`ADR ${prepNumber(player.adpr)}`);
  if (player.season_rating !== null && player.season_rating !== undefined) {
    parts.push(`R ${prepNumber(player.season_rating, 2)}`);
  }
  facts.textContent = parts.length ? parts.join(" · ") : "无平台数据";
  who.append(name, facts);

  const rates = document.createElement("div");
  rates.className = "prep-rates";
  const mapLine = document.createElement("span");
  if (player.map_win_rate === null || player.map_win_rate === undefined) {
    mapLine.textContent = "地图 —";
  } else if (player.map_sample_low) {
    // A 1-0 map record is 100%: label the sample so it is not read as reliable.
    mapLine.textContent = `地图 ${prepRate(player.map_win_rate)}（样本 ${player.map_total} 局）`;
  } else {
    mapLine.textContent = `地图 ${prepRate(player.map_win_rate)}`;
  }
  const recentLine = document.createElement("span");
  const results = Array.isArray(player.recent_results) ? player.recent_results.slice(-5) : [];
  recentLine.textContent = results.length
    ? `近 5 场 ${results.map((item) => (item === "win" ? "胜" : item === "lose" ? "负" : "平")).join("")}`
    : "近况 —";
  rates.append(mapLine, recentLine);

  row.append(canvas, who, rates);
  drawPrepRadar(canvas, player.radar);
  return row;
}

function buildPrepTeam(team) {
  const wrapper = document.createElement("div");
  wrapper.className = "prep-team";
  wrapper.dataset.label = team.label || "unknown";

  const head = document.createElement("div");
  head.className = "prep-team-head";
  const label = document.createElement("span");
  label.className = "prep-team-label";
  if (team.label === "ours") label.textContent = "我方";
  else if (team.label === "theirs") label.textContent = "对方";
  else label.textContent = team.side === 1 ? "A 队" : "B 队";
  const average = document.createElement("span");
  const scores = (team.players || [])
    .filter((player) => player.has_stats && player.score !== null && player.score !== undefined)
    .map((player) => Number(player.score));
  average.textContent = scores.length
    ? `均分 ${Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length)}`
    : "无评分数据";
  head.append(label, average);

  const rows = document.createElement("div");
  rows.className = "prep-rows";
  rows.append(...(team.players || []).map(buildPrepRow));
  if (!rows.childElementCount) {
    const empty = document.createElement("div");
    empty.className = "prep-empty";
    empty.textContent = "该队没有可用数据";
    rows.append(empty);
  }
  wrapper.append(head, rows);
  return wrapper;
}

function renderPrepPanel(data) {
  const doc = document;
  const panel = doc.querySelector("#prep-panel");
  const teams = doc.querySelector("#prep-teams");
  if (!panel || !teams) return;
  const usable = Boolean(data && data.available && Array.isArray(data.teams) && data.teams.length);
  if (!usable) {
    panel.hidden = true;
    teams.replaceChildren();
    return;
  }
  panel.hidden = false;
  const mapEl = doc.querySelector("#prep-map");
  if (mapEl) mapEl.textContent = data.map || "未知地图";
  const metaEl = doc.querySelector("#prep-meta");
  if (metaEl) {
    metaEl.textContent = data.fresh === true ? "日志实时" : (data.fresh === false ? "日志已停更" : "");
  }
  const coverageEl = doc.querySelector("#prep-coverage");
  const coverage = data.stats_coverage || {};
  if (coverageEl) {
    coverageEl.textContent = coverage.total
      ? `平台数据 ${coverage.with_stats}/${coverage.total} 人`
      : "";
  }
  teams.replaceChildren(...data.teams.map(buildPrepTeam));
  const empty = doc.querySelector("#empty-state");
  if (empty) empty.hidden = true;
}

if (typeof module !== "undefined") {
  module.exports = {
    PREP_RADAR_ORDER, prepRate, prepNumber, drawPrepRadar,
    buildPrepRow, buildPrepTeam, renderPrepPanel,
  };
}
