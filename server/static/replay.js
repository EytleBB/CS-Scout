// CS-Scout 2.0 canvas replay engine. Overlays all rounds of one (side,rtype)
// on a 9s loop (45s game time accelerated 5x). No fading trails.
const PLAYBACK_S = 10, WINDOW_S = 20;
const DOT_R = 10;   // player dot radius (px)
const SIDE_COLOR = { CT: "#99c6e3", T: "#e8da5d" };
const NADE_COLOR = { smoke:"#dddddd", flash:"#fff27a", he:"#ff6b6b",
                     molotov:"#ff8c42", decoy:"#9b8cff" };
const NADE_R = { smoke:171.81, molotov:80.5 };   // game-units coverage radius (molotov 70×1.15; smoke 90×1.15×1.66)
// Grenade SVG icons served from /icons (lazy-loaded, shared across instances).
//  - NADE_ICON_SRC: white in-flight glyphs, drawn along the arc while airborne.
//  - NADE_AREA_SRC: post-detonation area art (smoke cloud / inferno fire), drawn
//    at the landing spot during [land_t, expire_t], scaled to the coverage diameter.
const NADE_ICON_SRC = { smoke:"smokegrenade.svg", flash:"flashbang.svg",
                        he:"hegrenade.svg", molotov:"molotov.svg" };
const NADE_AREA_SRC = { smoke:"map_smoke.svg", molotov:"inferno.svg" };
const NADE_ICON_H = 22;   // in-flight icon height (px); width keeps SVG aspect
const _iconCache = {};    // filename -> HTMLImageElement
function _icon(file){
  if(!file) return null;
  let img = _iconCache[file];
  if(!img){ img = new Image(); img.src = "/icons/" + file; _iconCache[file] = img; }
  return img;
}

class ReplayPlayer {
  constructor(canvas, opts) {
    this.cv = canvas; this.ctx = canvas.getContext("2d");
    this.transform = opts.transform;
    this.allRounds = opts.rounds;
    this.side = opts.side; this.rtype = opts.rtype;
    this.disabled = new Set();
    this.img = new Image(); this.imgReady = false;
    this.img.onload = () => { this.imgReady = true;
      this.cv.width = this.img.width; this.cv.height = this.img.height; };
    this.img.src = opts.radar;
  }
  g2p(x, y) { const t=this.transform;
    return [(x - t.pos_x)/t.scale, (t.pos_y - y)/t.scale]; }
  _rounds() { return this.allRounds.filter(r =>
    r.side===this.side && r.rtype===this.rtype && !this.disabled.has(r.round_id)); }
  setFilter(side, rtype){ this.side=side; this.rtype=rtype; }
  toggleRound(id, on){ on ? this.disabled.delete(id) : this.disabled.add(id); }
  _interp(path, gt){ // path: [[t,x,y]], game-time seconds -> [x,y] or null
    if(!path.length) return null;
    if(gt <= path[0][0]) return [path[0][1], path[0][2]];
    if(gt >= path[path.length-1][0]) return null; // gone after last sample
    for(let i=1;i<path.length;i++){ if(gt <= path[i][0]){
      const [t0,x0,y0]=path[i-1], [t1,x1,y1]=path[i], f=(gt-t0)/(t1-t0||1);
      return [x0+(x1-x0)*f, y0+(y1-y0)*f]; } }
    return null;
  }
  _velAt(path, gt){ // movement direction (game-coord delta) of the segment at gt
    for(let i=1;i<path.length;i++){ if(gt <= path[i][0]){
      return [path[i][1]-path[i-1][1], path[i][2]-path[i-1][2]]; } }
    return null;
  }
  drawAt(gt){ // gt = game seconds in [0,WINDOW_S]
    const ctx=this.ctx;
    // Clear first: the radar PNG has transparent regions, so drawImage alone
    // composites over the previous frame and leaves trails outside the map.
    ctx.clearRect(0,0,this.cv.width,this.cv.height);
    if(this.imgReady) ctx.drawImage(this.img,0,0); else { ctx.fillStyle="#1a1a2e";
      ctx.fillRect(0,0,this.cv.width,this.cv.height); }
    for(const r of this._rounds()){
      const col = r.color || SIDE_COLOR[this.side];   // merged views color per player
      // grenades: in-flight arc+glyph, then post-detonation area art / landing marker
      for(const n of (r.grenades||[])){
        if(gt>=n.land_t && gt<n.expire_t){
          const [lx,ly]=this.g2p(n.land[0],n.land[1]);
          if(NADE_AREA_SRC[n.type]){
            // smoke/molotov: area art scaled to the coverage diameter (2×radius)
            this._drawNadeArea(n.type, lx, ly);
          } else {
            // flash/he/decoy: no coverage area — just a small landing marker
            ctx.beginPath(); ctx.arc(lx,ly,4,0,2*Math.PI);
            ctx.fillStyle=NADE_COLOR[n.type]; ctx.fill();
          }
        } else if(gt>=n.throw_t && gt<n.land_t){
          // in-flight arc up to current gt (no trail persistence beyond gt)
          ctx.strokeStyle=NADE_COLOR[n.type]; ctx.lineWidth=1.5; ctx.beginPath();
          let started=false;
          for(const [t,x,y] of n.arc){ if(t>gt) break;
            const [px,py]=this.g2p(x,y);
            started ? ctx.lineTo(px,py) : (ctx.moveTo(px,py), started=true); }
          ctx.stroke();
          // white icon riding the head of the in-flight arc (drop shadow for contrast)
          const head=this._interp(n.arc, gt);
          if(head) this._drawNadeIcon(n.type, this.g2p(head[0],head[1]));
        }
      }
      // player dot (no trail). After death_t, mark the death spot with an X.
      if(r.death_t != null && gt >= r.death_t){
        const dp=this._interp(r.path, r.death_t);
        if(dp) this._drawX(this.g2p(dp[0],dp[1]), col);
      } else {
        const p=this._interp(r.path, gt);
        if(p){ const [px,py]=this.g2p(p[0],p[1]);
          // movement-direction arrow (skip when essentially stationary)
          const v=this._velAt(r.path, gt);
          if(v){ const vx=v[0]/this.transform.scale, vy=-v[1]/this.transform.scale;
            if(Math.hypot(vx,vy) > 0.5) this._drawArrow(px,py,Math.atan2(vy,vx),col); }
          ctx.beginPath(); ctx.arc(px,py,DOT_R,0,2*Math.PI);
          ctx.fillStyle=col; ctx.globalAlpha=0.85; ctx.fill(); ctx.globalAlpha=1; }
      }
    }
  }
  _drawNadeIcon(type, pt){   // white in-flight glyph at the arc head
    const img=_icon(NADE_ICON_SRC[type]);
    if(!img || !img.complete || !img.naturalWidth) return;
    const [px,py]=pt, h=NADE_ICON_H, w=h*(img.naturalWidth/img.naturalHeight);
    const ctx=this.ctx;
    ctx.save();
    ctx.shadowColor="rgba(0,0,0,0.85)"; ctx.shadowBlur=3;
    ctx.shadowOffsetX=1; ctx.shadowOffsetY=1;
    ctx.drawImage(img, px-w/2, py-h/2, w, h);
    ctx.restore();
  }
  _drawNadeArea(type, cx, cy){   // post-detonation area art, fit to coverage diameter
    const img=_icon(NADE_AREA_SRC[type]);
    if(!img || !img.complete || !img.naturalWidth) return;
    const d=2*NADE_R[type]/this.transform.scale;             // target diameter (px)
    const s=d/Math.max(img.naturalWidth, img.naturalHeight);  // fit longest side
    const w=img.naturalWidth*s, h=img.naturalHeight*s;
    const ctx=this.ctx;
    ctx.save(); ctx.globalAlpha=0.85;
    ctx.drawImage(img, cx-w/2, cy-h/2, w, h);
    ctx.restore();
  }
  _drawArrow(px, py, a, col){
    const ctx=this.ctx, r=DOT_R+8, w=6;
    ctx.save(); ctx.translate(px,py); ctx.rotate(a);
    ctx.beginPath(); ctx.moveTo(r,0); ctx.lineTo(r-9,-w); ctx.lineTo(r-9,w); ctx.closePath();
    ctx.fillStyle=col; ctx.globalAlpha=0.95; ctx.fill(); ctx.globalAlpha=1;
    ctx.restore();
  }
  _drawX(pt, col){
    const [x,y]=pt, s=DOT_R*0.85, ctx=this.ctx;
    ctx.strokeStyle=col; ctx.lineWidth=2; ctx.globalAlpha=0.9; ctx.beginPath();
    ctx.moveTo(x-s,y-s); ctx.lineTo(x+s,y+s);
    ctx.moveTo(x+s,y-s); ctx.lineTo(x-s,y+s);
    ctx.stroke(); ctx.globalAlpha=1;
  }
}
if (typeof module !== "undefined") module.exports = { ReplayPlayer };
