"""
cvd_chart.py — interactive price + CVD chart widget.

Self-contained HTML/JS (no external libs), same pattern as zone_chart.py:
  • top panel: intraday close line with session (day) dividers
  • bottom panel: CVD line — toggle between multi-day CUMULATIVE and
    SESSION-ANCHORED (resets each day) — over a green/red per-bar delta histogram
  • hover crosshair across both panels: time, price, bar delta, CVD
  • X is index-spaced (overnight gaps collapsed); vertical lines mark new days
"""

from __future__ import annotations
import json


def render_cvd_html(cvd: dict, height: int = 470) -> str:
    """cvd: the dict returned by cvd_analysis.fetch_cvd()/compute()."""
    pts = (cvd or {}).get("points") or []
    if not (cvd or {}).get("ok") or len(pts) < 2:
        return ("<div style='color:#8899aa;padding:24px;font:14px system-ui'>"
                "No CVD data — hit ⟳ Update CVD first.</div>")

    payload = json.dumps({
        "pts": pts,
        "sym": cvd.get("symbol", ""),
        "interval": cvd.get("interval_used", ""),
        "method": cvd.get("method", "location"),
    })

    # NOTE: doubled braces {{ }} are literal JS braces; {payload}/{height} are
    # Python-substituted.
    return f"""
<div id="cvd-wrap" style="font-family:system-ui;color:#cdd6e0;width:100%;position:relative">
  <div style="display:flex;gap:6px;align-items:center;margin:0 0 8px 2px">
    <button class="cvd-mode cvd-on" data-m="cv">Cumulative</button>
    <button class="cvd-mode" data-m="cs">By session</button>
    <span style="font-size:12px;color:#8899aa;margin-left:10px">
      <span style="color:#46d6c8">—</span> price &nbsp;
      <span style="color:#e6a23c">—</span> CVD &nbsp;
      <span style="color:#3fb37f">▮</span><span style="color:#d65a5a">▮</span> bar delta
    </span>
  </div>
  <canvas id="cvd" style="width:100%;height:{height}px;display:block"></canvas>
  <div id="cvd-tip" style="position:absolute;pointer-events:none;opacity:0;
       background:#1b2230;border:1px solid #2c3647;border-radius:6px;
       padding:6px 9px;font-size:12px;line-height:1.5;white-space:nowrap;
       transform:translate(-50%,0);z-index:5"></div>
</div>
<script>
(function(){{
  const D = {payload};
  const pts = D.pts;
  const cv = document.getElementById('cvd');
  const tip = document.getElementById('cvd-tip');
  const wrap = document.getElementById('cvd-wrap');
  const ctx = cv.getContext('2d');
  let mode = 'cv';           // 'cv' cumulative | 'cs' session-anchored

  const COL = {{ price:'#46d6c8', cvd:'#e6a23c', up:'#3fb37f', dn:'#d65a5a',
                axis:'#3a4658', grid:'#222a38', text:'#8899aa' }};

  // session start indices (where the date string changes)
  const sessAt = [0];
  for (let i=1;i<pts.length;i++) if (pts[i].s!==pts[i-1].s) sessAt.push(i);

  function layout(){{
    const dpr = window.devicePixelRatio || 1;
    const w = wrap.clientWidth, h = {height};
    cv.width = w*dpr; cv.height = h*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
    const padL=8, padR=64, padT=12, padB=22, gap=26;
    const plotW = w-padL-padR;
    const hTop = Math.round((h-padT-padB-gap)*0.52);
    const hBot = h-padT-padB-gap-hTop;
    return {{w,h,padL,padR,padT,padB,gap,plotW,hTop,hBot,
             topY:padT, botY:padT+hTop+gap}};
  }}

  function fmtVol(v){{
    const a=Math.abs(v);
    if(a>=1e9) return (v/1e9).toFixed(2)+'B';
    if(a>=1e6) return (v/1e6).toFixed(2)+'M';
    if(a>=1e3) return (v/1e3).toFixed(1)+'K';
    return String(Math.round(v));
  }}

  function draw(){{
    const L=layout(); const n=pts.length;
    ctx.clearRect(0,0,L.w,L.h);
    const X = i => L.padL + (n<=1 ? 0 : (i/(n-1))*L.plotW);

    // ---- y ranges
    let plo=Infinity, phi=-Infinity, clo=Infinity, chi=-Infinity, dmax=0;
    for (const p of pts){{
      plo=Math.min(plo,p.c); phi=Math.max(phi,p.c);
      const v=p[mode]; clo=Math.min(clo,v); chi=Math.max(chi,v);
      dmax=Math.max(dmax,Math.abs(p.d));
    }}
    let m=(phi-plo)*0.07||1; plo-=m; phi+=m;
    const cpad=Math.max((chi-clo)*0.08, Math.abs(chi)*0.02, Math.abs(clo)*0.02, 1);
    clo-=cpad; chi+=cpad;
    const Yp = v => L.topY + L.hTop - ((v-plo)/(phi-plo))*L.hTop;
    const Yc = v => L.botY + L.hBot - ((v-clo)/(chi-clo))*L.hBot;

    // ---- grids + y labels (right)
    ctx.strokeStyle=COL.grid; ctx.fillStyle=COL.text; ctx.font='11px system-ui';
    ctx.lineWidth=1; ctx.textAlign='left';
    for(let g=0;g<=3;g++){{
      const pv=plo+(phi-plo)*g/3, y=Yp(pv);
      ctx.beginPath(); ctx.moveTo(L.padL,y); ctx.lineTo(L.padL+L.plotW,y); ctx.stroke();
      ctx.fillText('$'+pv.toFixed(pv<10?2:pv<1000?1:0), L.padL+L.plotW+6, y+3);
    }}
    for(let g=0;g<=2;g++){{
      const vv=clo+(chi-clo)*g/2, y=Yc(vv);
      ctx.beginPath(); ctx.moveTo(L.padL,y); ctx.lineTo(L.padL+L.plotW,y); ctx.stroke();
      ctx.fillText(fmtVol(Math.round(vv)), L.padL+L.plotW+6, y+3);
    }}

    // ---- session dividers + date labels
    ctx.textAlign='center';
    for(const si of sessAt){{
      const x=X(si);
      ctx.strokeStyle=COL.axis; ctx.globalAlpha=0.45; ctx.setLineDash([2,4]);
      ctx.beginPath(); ctx.moveTo(x,L.topY); ctx.lineTo(x,L.botY+L.hBot); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha=1;
      const lab=pts[si].s.slice(5);   // MM-DD
      ctx.fillStyle=COL.text; ctx.fillText(lab, x, L.h-8);
    }}

    // ---- delta histogram (bottom panel, anchored at its base)
    const base=L.botY+L.hBot, maxH=L.hBot*0.30;
    const bw=Math.max(1, L.plotW/n - 0.5);
    for(let i=0;i<n;i++){{
      const p=pts[i]; if(!p.d) continue;
      const hgt=dmax? Math.abs(p.d)/dmax*maxH : 0;
      ctx.fillStyle = p.d>=0 ? 'rgba(63,179,127,0.45)' : 'rgba(214,90,90,0.45)';
      ctx.fillRect(X(i)-bw/2, base-hgt, bw, hgt);
    }}

    // ---- CVD zero line
    if (clo<0 && chi>0){{
      ctx.strokeStyle=COL.axis; ctx.setLineDash([4,4]); ctx.globalAlpha=0.7;
      ctx.beginPath(); ctx.moveTo(L.padL,Yc(0)); ctx.lineTo(L.padL+L.plotW,Yc(0)); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha=1;
    }}

    // ---- CVD line
    ctx.strokeStyle=COL.cvd; ctx.lineWidth=1.8; ctx.beginPath();
    pts.forEach((p,i)=>{{ const x=X(i),y=Yc(p[mode]); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }});
    ctx.stroke();

    // ---- price line
    ctx.strokeStyle=COL.price; ctx.lineWidth=1.8; ctx.beginPath();
    pts.forEach((p,i)=>{{ const x=X(i),y=Yp(p.c); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }});
    ctx.stroke();

    // ---- panel labels
    ctx.fillStyle=COL.text; ctx.textAlign='left'; ctx.font='10px system-ui';
    ctx.fillText('PRICE', L.padL+4, L.topY+11);
    ctx.fillText('CVD ('+(mode==='cv'?'cumulative':'session-anchored')+') + bar delta',
                 L.padL+4, L.botY+11);

    cv._geo={{X,Yp,Yc,L,n}};
  }}

  function onMove(ev){{
    const g=cv._geo; if(!g) return;
    const rect=cv.getBoundingClientRect();
    const mx=ev.clientX-rect.left;
    let bi=0,bd=1e9;
    for(let i=0;i<g.n;i++){{ const dd=Math.abs(g.X(i)-mx); if(dd<bd){{bd=dd;bi=i;}} }}
    if(bd>60){{ tip.style.opacity=0; draw(); return; }}
    const p=pts[bi], x=g.X(bi);
    draw();
    ctx.strokeStyle='#8899aa'; ctx.globalAlpha=0.4; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(x,g.L.topY); ctx.lineTo(x,g.L.botY+g.L.hBot); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha=1;
    ctx.fillStyle=COL.price; ctx.beginPath(); ctx.arc(x,g.Yp(p.c),3.5,0,7); ctx.fill();
    ctx.fillStyle=COL.cvd;   ctx.beginPath(); ctx.arc(x,g.Yc(p[mode]),3.5,0,7); ctx.fill();

    const dc=p.d>=0?'#3fb37f':'#d65a5a';
    tip.innerHTML='<b>'+D.sym+' $'+p.c.toFixed(2)+'</b>'
      +' <span style="color:#8899aa">'+p.t+'</span><br>'
      +'bar delta <span style="color:'+dc+'">'+(p.d>=0?'+':'')+fmtVol(p.d)+'</span>'
      +' · CVD <span style="color:#e6a23c">'+(p[mode]>=0?'+':'')+fmtVol(p[mode])+'</span>'
      +'<br><span style="color:#8899aa">cum '+fmtVol(p.cv)+' · session '+fmtVol(p.cs)+'</span>';
    tip.style.left=Math.min(Math.max(x,90),g.L.w-90)+'px';
    tip.style.top=(cv.offsetTop+g.L.topY+2)+'px'; tip.style.opacity=1;
  }}

  cv.addEventListener('mousemove',onMove);
  cv.addEventListener('mouseleave',()=>{{ tip.style.opacity=0; draw(); }});
  wrap.querySelectorAll('.cvd-mode').forEach(b=>b.addEventListener('click',()=>{{
    wrap.querySelectorAll('.cvd-mode').forEach(x=>x.classList.remove('cvd-on'));
    b.classList.add('cvd-on'); mode=b.dataset.m; draw();
  }}));
  window.addEventListener('resize',draw);
  draw();
}})();
</script>
<style>
  .cvd-mode{{background:#1b2230;color:#8899aa;border:1px solid #2c3647;border-radius:5px;
    padding:3px 11px;font-size:12px;cursor:pointer}}
  .cvd-mode.cvd-on{{background:#46d6c8;color:#0e1117;border-color:#46d6c8;font-weight:600}}
</style>
"""
