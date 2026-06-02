"""
zone_chart.py — interactive price + projection-zone chart.

Emits a self-contained HTML/JS widget (no external libs) with:
  • dated X axis (day/month/year) and a price Y axis on the right
  • 3M / 6M / 1Y range toggle over the stored history
  • hover crosshair showing the price and BOTH deltas (% and $) vs today's price
  • forward projection: green ±1σ zone, red ±2σ edges, dashed drift center,
    dotted valuation-corridor levels — same standard-deviation method as the
    corridor chart.

These zones are probability ranges, not forecasts.
"""

from __future__ import annotations
import json


def render_zone_html(series: list, zones: dict, height: int = 380) -> str:
    """series: [{'d':'YYYY-MM-DD','c':float}]  zones: prediction_zones.build_zones()."""
    cone = (zones or {}).get("cone", {})
    corr = (zones or {}).get("corridor", {})
    if not series or not cone.get("ok"):
        return ("<div style='color:#8899aa;padding:24px;font:14px system-ui'>"
                "No chart data — hit Update for this ticker.</div>")

    payload = json.dumps({
        "series": series,
        "cone": cone.get("points", []),
        "spot": cone.get("spot"),
        "corridor": corr if corr.get("ok") else None,
    })

    # NOTE: doubled braces {{ }} are literal braces for the JS; {payload}/{height}
    # are Python-substituted.
    return f"""
<div id="zc-wrap" style="font-family:system-ui;color:#cdd6e0;width:100%">
  <div style="display:flex;gap:6px;margin:0 0 8px 2px">
    <button class="zc-rng" data-m="3">3M</button>
    <button class="zc-rng" data-m="6">6M</button>
    <button class="zc-rng zc-on" data-m="12">1Y</button>
  </div>
  <canvas id="zc" style="width:100%;height:{height}px;display:block"></canvas>
  <div id="zc-tip" style="position:absolute;pointer-events:none;opacity:0;
       background:#1b2230;border:1px solid #2c3647;border-radius:6px;
       padding:6px 9px;font-size:12px;line-height:1.5;white-space:nowrap;
       transform:translate(-50%,-115%);z-index:5"></div>
</div>
<script>
(function(){{
  const D = {payload};
  const cv = document.getElementById('zc');
  const tip = document.getElementById('zc-tip');
  const wrap = document.getElementById('zc-wrap');
  const ctx = cv.getContext('2d');
  const spot = D.spot;
  let months = 12;

  const COL = {{ line:'#46d6c8', axis:'#3a4658', grid:'#222a38', text:'#8899aa',
                g1:'rgba(63,179,127,0.22)', r2:'rgba(214,90,90,0.16)',
                drift:'#8899aa', fair:'#46d6c8', warn:'#e6a23c', bad:'#d65a5a' }};

  function sliceHist(){{
    const days = Math.round(months*21);
    return D.series.slice(Math.max(0, D.series.length - days));
  }}

  function layout(){{
    const dpr = window.devicePixelRatio || 1;
    const w = wrap.clientWidth, h = {height};
    cv.width = w*dpr; cv.height = h*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
    return {{w, h, padL:8, padR:54, padT:14, padB:26}};
  }}

  function draw(){{
    const L = layout();
    const hist = sliceHist();
    const cone = D.cone;
    ctx.clearRect(0,0,L.w,L.h);

    const plotW = L.w - L.padL - L.padR;
    const plotH = L.h - L.padT - L.padB;
    const histFrac = 0.66;
    const nH = hist.length;
    const horizon = cone.length ? cone[cone.length-1].day : 1;

    // y-range over visible history + projection bands
    let ylo = Infinity, yhi = -Infinity;
    for (const p of hist){{ ylo=Math.min(ylo,p.c); yhi=Math.max(yhi,p.c); }}
    for (const p of cone){{ ylo=Math.min(ylo,p.p2_dn); yhi=Math.max(yhi,p.p2_up); }}
    const m = (yhi-ylo)*0.06 || 1; ylo-=m; yhi+=m;

    const xH = i => L.padL + (nH<=1?0:(i/(nH-1))*plotW*histFrac);
    const xP = day => L.padL + plotW*histFrac + (day/horizon)*plotW*(1-histFrac);
    const Y  = v => L.padT + plotH - ((v-ylo)/(yhi-ylo))*plotH;
    const x0 = xH(nH-1);

    // grid + Y labels (right)
    ctx.strokeStyle=COL.grid; ctx.fillStyle=COL.text; ctx.font='11px system-ui';
    ctx.lineWidth=1; ctx.textAlign='left';
    for(let g=0; g<=4; g++){{
      const val = ylo + (yhi-ylo)*g/4, y=Y(val);
      ctx.beginPath(); ctx.moveTo(L.padL,y); ctx.lineTo(L.padL+plotW,y); ctx.stroke();
      ctx.fillText('$'+val.toFixed(val<10?2:0), L.padL+plotW+6, y+3);
    }}

    // X date labels
    ctx.textAlign='center';
    const ticks=4;
    for(let t=0;t<=ticks;t++){{
      const i=Math.round((nH-1)*t/ticks), p=hist[i]; if(!p) continue;
      const dt=new Date(p.d), lab=(dt.getMonth()+1)+'/'+String(dt.getFullYear()).slice(2);
      ctx.fillText(lab, xH(i), L.h-8);
    }}

    // RED ±2σ zone
    ctx.fillStyle=COL.r2; ctx.beginPath(); ctx.moveTo(x0,Y(spot));
    for(const p of cone) ctx.lineTo(xP(p.day),Y(p.p2_up));
    for(let i=cone.length-1;i>=0;i--) ctx.lineTo(xP(cone[i].day),Y(cone[i].p2_dn));
    ctx.closePath(); ctx.fill();
    // GREEN ±1σ zone
    ctx.fillStyle=COL.g1; ctx.beginPath(); ctx.moveTo(x0,Y(spot));
    for(const p of cone) ctx.lineTo(xP(p.day),Y(p.p1_up));
    for(let i=cone.length-1;i>=0;i--) ctx.lineTo(xP(cone[i].day),Y(cone[i].p1_dn));
    ctx.closePath(); ctx.fill();

    // valuation corridor dotted levels
    if(D.corridor){{
      ctx.setLineDash([2,3]); ctx.lineWidth=1;
      const lv=[['fair',COL.fair],['p1_up',COL.warn],['p1_dn',COL.warn],
                ['p2_up',COL.bad],['p2_dn',COL.bad]];
      for(const [k,c] of lv){{ const v=D.corridor[k];
        if(v!=null&&v>=ylo&&v<=yhi){{ ctx.strokeStyle=c; ctx.globalAlpha=0.55;
          ctx.beginPath(); ctx.moveTo(x0,Y(v)); ctx.lineTo(L.padL+plotW,Y(v)); ctx.stroke();
          ctx.globalAlpha=1; }} }}
      ctx.setLineDash([]);
    }}

    // drift center dashed
    ctx.setLineDash([4,4]); ctx.strokeStyle=COL.drift; ctx.lineWidth=1.2;
    ctx.beginPath(); ctx.moveTo(x0,Y(spot));
    for(const p of cone) ctx.lineTo(xP(p.day),Y(p.center));
    ctx.stroke(); ctx.setLineDash([]);

    // history price line
    ctx.strokeStyle=COL.line; ctx.lineWidth=1.8; ctx.beginPath();
    hist.forEach((p,i)=>{{ const x=xH(i),y=Y(p.c); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }});
    ctx.stroke();

    // "now" divider + dot
    ctx.strokeStyle=COL.axis; ctx.globalAlpha=0.5; ctx.lineWidth=0.8;
    ctx.beginPath(); ctx.moveTo(x0,L.padT); ctx.lineTo(x0,L.padT+plotH); ctx.stroke();
    ctx.globalAlpha=1; ctx.fillStyle=COL.line;
    ctx.beginPath(); ctx.arc(x0,Y(spot),3,0,7); ctx.fill();

    cv._geo={{hist,xH,Y,nH,L,plotH}};
  }}

  function onMove(ev){{
    const g=cv._geo; if(!g) return;
    const rect=cv.getBoundingClientRect();
    const mx=ev.clientX-rect.left;
    // nearest history point
    let bi=0,bd=1e9;
    for(let i=0;i<g.nH;i++){{ const d=Math.abs(g.xH(i)-mx); if(d<bd){{bd=d;bi=i;}} }}
    if(bd>40){{ tip.style.opacity=0; draw(); return; }}
    const p=g.hist[bi]; const x=g.xH(bi), y=g.Y(p.c);
    draw();
    ctx.strokeStyle='#8899aa'; ctx.globalAlpha=0.4; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(x,g.L.padT); ctx.lineTo(x,g.L.padT+g.plotH); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha=1;
    ctx.fillStyle='#46d6c8'; ctx.beginPath(); ctx.arc(x,y,3.5,0,7); ctx.fill();

    const dDol=p.c-spot, dPct=spot?(dDol/spot*100):0;
    const sign=dDol>=0?'+':'', col=dDol>=0?'#3fb37f':'#d65a5a';
    const dt=new Date(p.d);
    tip.innerHTML='<b>$'+p.c.toFixed(2)+'</b>  '
      +'<span style="color:'+col+'">'+sign+dPct.toFixed(1)+'% / '+sign+'$'+dDol.toFixed(2)+'</span>'
      +'<br><span style="color:#8899aa">'+dt.toLocaleDateString(undefined,
        {{year:'numeric',month:'short',day:'numeric'}})+' · vs today</span>';
    tip.style.left=(x)+'px'; tip.style.top=(y)+'px'; tip.style.opacity=1;
  }}

  cv.addEventListener('mousemove',onMove);
  cv.addEventListener('mouseleave',()=>{{ tip.style.opacity=0; draw(); }});
  wrap.querySelectorAll('.zc-rng').forEach(b=>b.addEventListener('click',()=>{{
    wrap.querySelectorAll('.zc-rng').forEach(x=>x.classList.remove('zc-on'));
    b.classList.add('zc-on'); months=+b.dataset.m; draw();
  }}));
  window.addEventListener('resize',draw);
  draw();
}})();
</script>
<style>
  .zc-rng{{background:#1b2230;color:#8899aa;border:1px solid #2c3647;border-radius:5px;
    padding:3px 11px;font-size:12px;cursor:pointer}}
  .zc-rng.zc-on{{background:#46d6c8;color:#0e1117;border-color:#46d6c8;font-weight:600}}
</style>
"""


# kept for any callers expecting the old name; routes to the HTML widget
def render_zone_svg(series: list, zones: dict, width=720, height=380) -> str:
    return render_zone_html(series, zones, height=height)
