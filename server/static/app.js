const $ = s => document.querySelector(s);
const RTYPES = ["Pistol","Full","Eco"];
let players = {};   // domain -> {data, ps:{rtype:ReplayPlayer}, side}

// ── Global playback clock: one loop drives every canvas in lockstep ──────────
const allPlayers = [];                          // every ReplayPlayer on the page
const clock = { elapsed: 0, playing: true, last: null };

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
  $("#playpause").onclick = () => { clock.playing = !clock.playing; clock.last = null; };
  $("#scrub").addEventListener("input", e => {
    clock.playing = false;
    clock.elapsed = (+e.target.value / 1000) * PLAYBACK_S;
  });
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
  $("#cards").innerHTML=""; players={}; allPlayers.length=0; poll();
}

async function poll(){
  const r = await fetch("/api/status"); const s = await r.json();
  $("#status").textContent = s.message || s.status;
  $("#failed").innerHTML = (s.failed||[]).map(f=>`✗ ${f.username}: ${f.reason}`).join("<br>");
  for(const res of (s.results||[])) if(!players[res.domain]) await addCard(res);
  if(s.status === "running") setTimeout(poll, 2000);
}

async function addCard(res){
  const r = await fetch("/api/player/"+res.domain); const data = await r.json();
  const card = document.createElement("div"); card.className="card";
  const cs = data.combat_stats||{};
  card.innerHTML = `<div><b>${data.username}</b>
    <span class="stat">K/D ${cs.kd??"-"}</span>
    <span class="stat">AWP ${cs.awp_rate??"-"}%</span>
    <span class="stat" style="color:#789">${data.round_count} 回合</span></div>
    <div class="tabs"><button data-s="CT" class="active">CT</button>
    <button data-s="T">T</button></div>
    <div class="grid">${RTYPES.map(rt=>`<div class="cell"><h4>${rt}</h4>
      <canvas data-rt="${rt}"></canvas></div>`).join("")}</div>`;
  $("#cards").appendChild(card);
  const ps = {}; players[res.domain] = {data, ps, side:"CT"};
  for(const rt of RTYPES){
    const cv = card.querySelector(`canvas[data-rt="${rt}"]`);
    const p = new ReplayPlayer(cv,{radar:data.radar,transform:data.transform,
      rounds:data.rounds,side:"CT",rtype:rt});
    allPlayers.push(p); ps[rt]=p;
  }
  card.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{
    card.querySelectorAll(".tabs button").forEach(x=>x.classList.remove("active"));
    b.classList.add("active");
    const side=b.dataset.s; players[res.domain].side=side;
    for(const rt of RTYPES) ps[rt].setFilter(side, rt);
  });
}

$("#run").onclick = run;
wireControls();
loadMaps();
poll();   // resume if a run is already in progress
