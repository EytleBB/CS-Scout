const $ = s => document.querySelector(s);
const RTYPES = ["Pistol","Full","Eco"];
let players = {};   // domain -> {data, ps:{rtype:ReplayPlayer}, side}

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
  $("#cards").innerHTML=""; players={}; poll();
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
    p.start(); ps[rt]=p;
  }
  card.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{
    card.querySelectorAll(".tabs button").forEach(x=>x.classList.remove("active"));
    b.classList.add("active");
    const side=b.dataset.s; players[res.domain].side=side;
    for(const rt of RTYPES) ps[rt].setFilter(side, rt);
  });
}

$("#run").onclick = run;
loadMaps();
poll();   // resume if a run is already in progress
