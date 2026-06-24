const $ = s => document.querySelector(s);
let players = {};   // domain -> {data, buyPlayer}

// Per-player colors for the merged pistol overlay + card accents.
const PLAYER_COLORS = ["#4aa3ff","#ffc23b","#5ad469","#ff6b9d","#b48cff"];

// ── Global playback clock: one loop drives every canvas in lockstep ──────────
const allPlayers = [];                          // every ReplayPlayer on the page
const clock = { elapsed: 0, playing: true, last: null };

// ── Unified CT/T state: one toggle drives every canvas's side ────────────────
let curSide = "CT";
const sideTargets = [];   // [{rp, rtype}] — each canvas keeps its own fixed rtype
function applySide(){ for(const {rp,rtype} of sideTargets) rp.setFilter(curSide, rtype); }

// ── View switcher: one big canvas at a time, picked via top buttons ──────────
// views[i] = {id, btn, panel}. id = "pistol" or a player domain.
const views = [];
let activeView = null;
function selectView(id){
  activeView = id;
  for(const v of views){
    const on = v.id === id;
    v.panel.classList.toggle("active", on);
    v.btn.classList.toggle("active", on);
  }
}
function addView(id, label, panel){
  const btn = document.createElement("button");
  btn.textContent = label;
  btn.onclick = () => selectView(id);
  $("#viewtabs").appendChild(btn);
  $("#stage").appendChild(panel);
  views.push({id, btn, panel});
  if(activeView === null) selectView(id);   // first view becomes active
}

// Merged pistol overlay: one canvas, all scanned players' pistol rounds, each
// round pre-tagged with its player color. We grow this shared array as players
// load; the ReplayPlayer reads it live every frame.
let pistolRounds = [];
let pistolPlayer = null;

function tick(ts){
  if(clock.last === null) clock.last = ts;
  const dt = (ts - clock.last) / 1000; clock.last = ts;
  if(clock.playing) clock.elapsed = (clock.elapsed + dt) % PLAYBACK_S;
  const gt = clock.elapsed / PLAYBACK_S * WINDOW_S;
  for(const p of allPlayers) p.drawAt(gt);
  const scrub = $("#scrub"), lbl = $("#timelbl"), btn = $("#playpause");
  if(scrub && document.activeElement !== scrub) scrub.value = (clock.elapsed/PLAYBACK_S*1000)|0;
  if(lbl) lbl.textContent = gt.toFixed(1) + " / " + WINDOW_S.toFixed(1) + "s";
  if(btn) btn.textContent = clock.playing ? "⏸" : "▶";
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

function wireControls(){
  const btn = $("#playpause"), scrub = $("#scrub");
  if(btn && scrub){
    btn.onclick = () => { clock.playing = !clock.playing; clock.last = null; };
    scrub.addEventListener("input", e => {
      clock.playing = false;
      clock.elapsed = (+e.target.value / 1000) * PLAYBACK_S;
    });
  }
  const ct = $("#sideCT"), t = $("#sideT");
  if(ct && t){
    const set = (side, on, off) => { curSide = side; on.classList.add("active");
      off.classList.remove("active"); applySide(); };
    ct.onclick = () => set("CT", ct, t);
    t.onclick  = () => set("T",  t, ct);
  }
}

async function loadMaps(){
  const r = await fetch("/api/maps"); const {maps} = await r.json();
  $("#map").innerHTML = maps.map(m=>`<option>${m}</option>`).join("");
}
function names(){ return [...Array(5)].map((_,i)=>$("#u"+i).value.trim()).filter(Boolean); }

async function run(){
  const body = {usernames:names(), map:$("#map").value,
    max_demos:+$("#depth").value, key:$("#key").value};
  const r = await fetch("/api/analyze",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j = await r.json();
  if(j.error){ $("#status").textContent = "错误："+j.error; return; }
  // reset all view state for a fresh scan
  $("#viewtabs").innerHTML=""; $("#stage").innerHTML="";
  players={}; allPlayers.length=0; sideTargets.length=0; views.length=0;
  pistolRounds.length=0; pistolPlayer=null; activeView=null; curSide="CT";
  $("#sideCT").classList.add("active"); $("#sideT").classList.remove("active");
  poll();
}

async function poll(){
  const r = await fetch("/api/status"); const s = await r.json();
  $("#status").textContent = s.message || s.status;
  $("#failed").innerHTML = (s.failed||[]).map(f=>`✗ ${f.username}: ${f.reason}`).join("<br>");
  for(const res of (s.results||[])) if(!players[res.domain]) await addPlayer(res);
  if(s.status === "running") setTimeout(poll, 2000);
}

// Lazily build the merged pistol view once the first player's map is known.
function ensurePistolView(data){
  if(pistolPlayer) return;
  const panel = document.createElement("div"); panel.className="view";
  panel.innerHTML = `<div class="viewhead">手枪局 · 全队合并
    <span class="legend" id="pistolLegend"></span></div><canvas></canvas>`;
  const cv = panel.querySelector("canvas");
  pistolPlayer = new ReplayPlayer(cv, {radar:data.radar, transform:data.transform,
    rounds:pistolRounds, side:curSide, rtype:"Pistol"});
  allPlayers.push(pistolPlayer);
  sideTargets.push({rp:pistolPlayer, rtype:"Pistol"});
  addView("pistol", "手枪局", panel);
}

async function addPlayer(res){
  const r = await fetch("/api/player/"+res.domain); const data = await r.json();
  const idx = Object.keys(players).length;
  const color = PLAYER_COLORS[idx % PLAYER_COLORS.length];

  ensurePistolView(data);
  // merge this player's pistol rounds (tagged with their color) into the overlay
  for(const rd of data.rounds) if(rd.rtype==="Pistol") pistolRounds.push({...rd, color});
  $("#pistolLegend").insertAdjacentHTML("beforeend",
    `<span><i style="background:${color}"></i>${data.username}</span>`);

  // per-player view: stats header + one big Buy-round canvas
  const cs = data.combat_stats||{};
  const panel = document.createElement("div"); panel.className="view";
  panel.innerHTML = `<div class="viewhead"><b style="color:${color}">${data.username}</b>
    <span class="stat">K/D ${cs.kd??"-"}</span>
    <span class="stat">持狙 ${cs.awp_rate??"-"}%</span>
    <span class="stat" style="color:#789">${data.round_count} 回合 · 买局</span></div>
    <canvas></canvas>`;
  const cv = panel.querySelector("canvas");
  const buyPlayer = new ReplayPlayer(cv,{radar:data.radar,transform:data.transform,
    rounds:data.rounds, side:curSide, rtype:"Buy"});
  allPlayers.push(buyPlayer);
  sideTargets.push({rp:buyPlayer, rtype:"Buy"});
  addView(res.domain, data.username, panel);
  players[res.domain] = {data, buyPlayer};
}

$("#run").onclick = run;
loadMaps();           // core first — must not be blocked by optional control wiring
poll();               // resume if a run is already in progress
wireControls();
