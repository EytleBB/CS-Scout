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

const PREP_COMPARE_METRICS = [
  ["avg_score", "均分", 0, false],
  ["avg_adpr", "ADR", 0, false],
  ["avg_season_rating", "赛季 Rating", 2, false],
  ["avg_perfect_power", "完美战力", 0, false],
  ["map_win_rate", "地图胜率", 0, true]
];

const PREP_PARTY_STYLES = 5;

function prepRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

function prepNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function prepMetric(value, digits, percent) {
  return percent ? prepRate(value) : prepNumber(value, digits);
}

function prepNumberOrNull(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
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

function buildPrepPartyBadge(player) {
  const badge = document.createElement("span");
  badge.className = "prep-party";
  badge.hidden = true;
  const party = prepNumberOrNull(player.party);
  const size = prepNumberOrNull(player.party_size);
  if (party === null || party < 1 || size === null || size < 2) return badge;
  badge.textContent = `${size} 人`;
  badge.className = [
    "prep-party",
    `prep-party-${((Math.floor(party) - 1) % PREP_PARTY_STYLES) + 1}`
  ].join(" ");
  badge.hidden = false;
  return badge;
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
  const nameLine = document.createElement("div");
  nameLine.className = "prep-name-line";
  const name = document.createElement("strong");
  name.textContent = player.nickname || (player.steam_id ? `…${String(player.steam_id).slice(-6)}` : "未知玩家");
  name.title = name.textContent;
  nameLine.append(name, buildPrepPartyBadge(player));
  const facts = document.createElement("span");
  const parts = [];
  if (player.score !== null && player.score !== undefined) parts.push(`分 ${player.score}`);
  if (player.adpr !== null && player.adpr !== undefined) parts.push(`ADR ${prepNumber(player.adpr)}`);
  if (player.season_rating !== null && player.season_rating !== undefined) {
    parts.push(`R ${prepNumber(player.season_rating, 2)}`);
  }
  facts.textContent = parts.length ? parts.join(" · ") : "无平台数据";
  who.append(nameLine, facts);

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

function prepSides(teams) {
  const ours = teams.find((team) => team.label === "ours") || teams[0] || null;
  const theirs = teams.find((team) => team.label === "theirs")
    || teams.find((team) => team !== ours)
    || null;
  return [ours, theirs];
}

function prepSideLabel(team) {
  if (!team) return "—";
  if (team.label === "ours") return "我方";
  if (team.label === "theirs") return "对方";
  // Without a session the own side is unknown (D-2): never guess "ours".
  return team.side === 1 ? "A 队" : "B 队";
}

function buildPrepCompare(teams) {
  const [ours, theirs] = prepSides(teams);
  if (!ours || !theirs) return [];
  const oursAgg = ours.aggregates || {};
  const theirsAgg = theirs.aggregates || {};
  const rows = [];
  for (const [key, label, digits, percent] of PREP_COMPARE_METRICS) {
    const oursValue = prepNumberOrNull(oursAgg[key]);
    const theirsValue = prepNumberOrNull(theirsAgg[key]);
    // A one-sided bar would invent a comparison the data cannot support.
    if (oursValue === null || theirsValue === null) continue;
    const total = oursValue + theirsValue;
    const oursShare = total > 0 ? (oursValue / total) * 100 : 50;

    const row = document.createElement("div");
    row.className = "prep-compare-item";
    const name = document.createElement("span");
    name.className = "prep-compare-label";
    name.textContent = label;
    const track = document.createElement("div");
    track.className = "prep-compare-track";
    const oursBar = document.createElement("span");
    oursBar.className = "prep-compare-side prep-compare-ours";
    oursBar.style.width = `${oursShare.toFixed(1)}%`;
    const theirsBar = document.createElement("span");
    theirsBar.className = "prep-compare-side prep-compare-theirs";
    theirsBar.style.width = `${(100 - oursShare).toFixed(1)}%`;
    track.append(oursBar, theirsBar);
    const values = document.createElement("span");
    values.className = "prep-compare-values";
    values.textContent = `${prepMetric(oursValue, digits, percent)} : ${prepMetric(theirsValue, digits, percent)}`;
    row.append(name, track, values);
    rows.push(row);
  }
  return rows;
}

function buildPrepInsights(data) {
  const teams = Array.isArray(data && data.teams) ? data.teams : [];
  const [ours, theirs] = prepSides(teams);
  if (!theirs) return [];
  const oursName = prepSideLabel(ours);
  const theirsName = prepSideLabel(theirs);
  const lines = [];
  const players = Array.isArray(theirs.players) ? theirs.players : [];

  const parties = new Map();
  for (const player of players) {
    const party = prepNumberOrNull(player.party);
    const size = prepNumberOrNull(player.party_size);
    if (party === null || party < 1 || size === null || size < 2) continue;
    if (!parties.has(party)) parties.set(party, []);
    parties.get(party).push(player.nickname || "未知玩家");
  }
  if (parties.size) {
    const groups = [...parties.values()]
      .map((names) => `${names.join("、")}（${names.length} 人）`)
      .join("；");
    lines.push(`${theirsName}开黑：${groups}`);
  }

  const oursAgg = (ours && ours.aggregates) || {};
  const theirsAgg = theirs.aggregates || {};
  const oursScore = prepNumberOrNull(oursAgg.avg_score);
  const theirsScore = prepNumberOrNull(theirsAgg.avg_score);
  if (oursScore !== null && theirsScore !== null) {
    const diff = Math.abs(Math.round(oursScore) - Math.round(theirsScore));
    lines.push(`均分差 ${diff}（${oursName} ${Math.round(oursScore)} / ${theirsName} ${Math.round(theirsScore)}）`);
  }

  const scored = players.filter((player) => prepNumberOrNull(player.score) !== null);
  if (scored.length) {
    const top = scored.reduce((best, player) => (
      Number(player.score) > Number(best.score) ? player : best
    ));
    lines.push(`${theirsName}最高分：${top.nickname || "未知玩家"}（${Math.round(Number(top.score))}）`);
  }

  // Everything below states a fact about the data itself; no threshold-based
  // "strong/weak" verdict is produced without a calibrated sample.
  const gaps = [];
  const missing = players.filter((player) => !player.has_stats).length;
  if (missing) gaps.push(`${missing} 人无平台数据`);
  const lowSample = prepNumberOrNull(theirsAgg.map_sample_low) || 0;
  if (lowSample) gaps.push(`${lowSample} 人地图样本不足 3 局`);
  if (gaps.length) lines.push(`${theirsName} ${gaps.join("、")}`);

  return lines.slice(0, 4).map((text) => {
    const item = document.createElement("div");
    item.className = "prep-insight";
    item.textContent = text;
    return item;
  });
}

function renderPrepPanel(data) {
  const doc = document;
  const panel = doc.querySelector("#prep-panel");
  const teams = doc.querySelector("#prep-teams");
  if (!panel || !teams) return;
  const compare = doc.querySelector("#prep-compare");
  const insights = doc.querySelector("#prep-insights");
  const usable = Boolean(data && data.available && Array.isArray(data.teams) && data.teams.length);
  if (!usable) {
    panel.hidden = true;
    teams.replaceChildren();
    if (compare) {
      compare.replaceChildren();
      compare.hidden = true;
    }
    if (insights) {
      insights.replaceChildren();
      insights.hidden = true;
    }
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
  if (compare) {
    const rows = buildPrepCompare(data.teams);
    compare.replaceChildren(...rows);
    compare.hidden = rows.length === 0;
  }
  if (insights) {
    const rows = buildPrepInsights(data);
    insights.replaceChildren(...rows);
    insights.hidden = rows.length === 0;
  }
  const empty = doc.querySelector("#empty-state");
  if (empty) empty.hidden = true;
}

if (typeof module !== "undefined") {
  module.exports = {
    PREP_RADAR_ORDER, PREP_COMPARE_METRICS, prepRate, prepNumber,
    prepMetric, prepSideLabel, drawPrepRadar, buildPrepPartyBadge, buildPrepRow,
    buildPrepTeam, buildPrepCompare, buildPrepInsights, renderPrepPanel,
  };
}
