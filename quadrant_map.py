"""
quadrant_map.py — the valuation × outlook quadrant chart.

Y axis  : EXPENSIVE (top) ←→ INEXPENSIVE (bottom)
          = price vs corridor fair value (NTM EPS × historical median P/E).
X axis  : DOWNSIDE (left) ←→ UPSIDE (right), definition depends on timeframe:
          short  (~days–2wks) : weighted trend net score, TD-exhaustion nudged
          medium (~1–3mo)     : bootstrap P(up 21d) from the ticker's own
                                return distribution, centered at 50
          long   (~6–12mo)    : 6-month momentum blended with % vs SMA200

Quadrants read:
  bottom-right  cheap + upside      → the sweet spot
  top-right     expensive + upside  → momentum names (paying up for strength)
  bottom-left   cheap + downside    → potential value traps (cheap, still falling)
  top-left      expensive + downside→ danger zone

Honest labels: short-term X is mostly lagging trend agreement; medium is
distribution odds (not a forecast); long is momentum persistence (a documented
factor, not a promise). Names without a corridor fair value can't be placed
honestly and are listed separately instead of faked.
"""

from __future__ import annotations
import json


def _corr_upside(result: dict) -> float | None:
    corr = ((result or {}).get("zones") or {}).get("corridor") or {}
    price = (result or {}).get("price")
    if corr.get("ok") and corr.get("fair") and price:
        return (corr["fair"] - price) / price * 100.0
    return None


def build_points(snaps: dict, timeframe: str = "medium",
                 min_composite: int = 0) -> dict:
    """Returns {points: [...], excluded: [...], xlabel, note}."""
    points, excluded = [], []
    for sym, snap in snaps.items():
        result = (snap or {}).get("result") or {}
        trading = (snap or {}).get("trading") or {}
        composite = result.get("composite_score")
        if composite is not None and composite < min_composite:
            continue

        up = _corr_upside(result)
        if up is None:
            excluded.append(sym)
            continue
        y = max(-75.0, min(75.0, -up))          # expensive = up, cheap = down

        x = None
        if timeframe == "short":
            sg = (trading.get("signal") or {})
            net = sg.get("net_score")
            if net is not None:
                x = max(-100.0, min(100.0, float(net) * 20.0))
                td = trading.get("demark") or {}
                for s in (td.get("recent_setups") or []):
                    if s.get("bars_ago", 99) <= 5:
                        x += 8.0 if s["side"] == "BUY" else -8.0
                x = max(-100.0, min(100.0, x))
        elif timeframe == "medium":
            series = result.get("series") or snap.get("prices") or []
            closes = [p.get("c") for p in series if p.get("c")]
            if len(closes) >= 80:
                try:
                    import scenario_engine as SE
                    sim = SE.simulate(closes, horizon=21, n_paths=200, seed=7)
                    if sim.get("ok"):
                        x = max(-100.0, min(100.0,
                                            (sim["prob_above_spot"] - 50) * 2.5))
                except Exception:
                    x = None
        else:  # long
            series = result.get("series") or snap.get("prices") or []
            closes = [p.get("c") for p in series if p.get("c")]
            price = trading.get("price") or result.get("price")
            sma200 = trading.get("sma200")
            r126 = (closes[-1] / closes[-126] - 1) if len(closes) >= 126 else None
            p200 = (price / sma200 - 1) if (price and sma200) else None
            comps = [v for v in (r126, p200) if v is not None]
            if comps:
                blend = (0.6 * (r126 if r126 is not None else p200)
                         + 0.4 * (p200 if p200 is not None else r126))
                x = max(-100.0, min(100.0, blend * 300.0))

        if x is None:
            excluded.append(sym)
            continue

        bias = ((trading.get("signal") or {}).get("direction")) or "WAIT"
        points.append({
            "sym": sym, "x": round(x, 1), "y": round(y, 1),
            "bias": bias, "composite": composite,
            "price": trading.get("price") or result.get("price"),
            "gap": round(up, 1),
        })

    xlabels = {
        "short": "short-term trend score (weighted votes, TD-nudged · mostly lagging)",
        "medium": "P(up in 21 days) from bootstrap of own returns (odds, not forecast)",
        "long": "6-month momentum + trend position (persistence factor)",
    }
    return {"points": points, "excluded": sorted(excluded),
            "xlabel": xlabels[timeframe], "timeframe": timeframe,
            "note": "Y = price vs corridor fair value. Quadrants are a reading "
                    "aid, not a rating: cheap names can keep falling and "
                    "expensive ones keep running."}


def render_quadrant_html(data: dict, height: int = 480) -> str:
    if not data.get("points"):
        return ("<div style='color:#8899aa;padding:24px;font:14px system-ui'>"
                "No plottable tickers — corridor fair values needed "
                "(run ⟳ Update all).</div>")
    payload = json.dumps({"pts": data["points"], "xlabel": data["xlabel"]})
    return f"""
<div id="qm-wrap" style="font-family:system-ui;color:#cdd6e0;width:100%;position:relative">
  <canvas id="qm" style="width:100%;height:{height}px;display:block"></canvas>
  <div id="qm-tip" style="position:absolute;display:none;background:#141a22;
    border:1px solid #2a3646;border-radius:6px;padding:8px 10px;font-size:12px;
    pointer-events:none;z-index:5;box-shadow:0 4px 14px rgba(0,0,0,.5)"></div>
</div>
<script>
(function(){{
  const D={payload};
  const cv=document.getElementById('qm'), ctx=cv.getContext('2d');
  const wrap=document.getElementById('qm-wrap'), tip=document.getElementById('qm-tip');
  const XR=100, YR=75;
  let px=[];
  function draw(){{
    const dpr=window.devicePixelRatio||1, w=wrap.clientWidth, h={height};
    cv.width=w*dpr; cv.height=h*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
    const padL=46,padR=14,padT=26,padB=40;
    const plotW=w-padL-padR, plotH=h-padT-padB;
    const X=v=>padL+((v+XR)/(2*XR))*plotW;
    const Y=v=>padT+((YR-v)/(2*YR))*plotH;
    ctx.clearRect(0,0,w,h);
    // quadrant tints
    ctx.fillStyle='rgba(63,179,127,0.06)';   // cheap+upside (bottom-right)
    ctx.fillRect(X(0),Y(0),plotW/2,plotH/2);
    ctx.fillStyle='rgba(214,90,90,0.06)';    // expensive+downside (top-left)
    ctx.fillRect(padL,padT,plotW/2,plotH/2);
    // axes
    ctx.strokeStyle='#2a3646';ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(X(0),padT);ctx.lineTo(X(0),padT+plotH);ctx.stroke();
    ctx.beginPath();ctx.moveTo(padL,Y(0));ctx.lineTo(padL+plotW,Y(0));ctx.stroke();
    ctx.strokeStyle='#1a222d';
    ctx.strokeRect(padL,padT,plotW,plotH);
    // quadrant labels
    ctx.font='10px ui-monospace,monospace';ctx.fillStyle='#66788c';
    ctx.textAlign='right';
    ctx.fillText('EXPENSIVE · UPSIDE — momentum', padL+plotW-8, padT+14);
    ctx.fillText('INEXPENSIVE · UPSIDE — sweet spot', padL+plotW-8, padT+plotH-8);
    ctx.textAlign='left';
    ctx.fillText('EXPENSIVE · DOWNSIDE — danger', padL+8, padT+14);
    ctx.fillText('INEXPENSIVE · DOWNSIDE — value trap?', padL+8, padT+plotH-8);
    // axis titles
    ctx.textAlign='center';ctx.fillStyle='#8899aa';ctx.font='11px system-ui';
    ctx.fillText('← DOWNSIDE      '+D.xlabel+'      UPSIDE →', padL+plotW/2, h-10);
    ctx.save();ctx.translate(14,padT+plotH/2);ctx.rotate(-Math.PI/2);
    ctx.fillText('← INEXPENSIVE   (price vs corridor fair value)   EXPENSIVE →',0,0);
    ctx.restore();
    // points
    px=[];
    ctx.font='10px ui-monospace,monospace';ctx.textAlign='left';
    for(const p of D.pts){{
      const x=X(Math.max(-XR,Math.min(XR,p.x))), y=Y(Math.max(-YR,Math.min(YR,p.y)));
      const col=p.bias==='LONG'?'#3fb37f':p.bias==='SHORT'?'#d6504f':'#8899aa';
      ctx.beginPath();ctx.arc(x,y,4.5,0,7);ctx.fillStyle=col;ctx.globalAlpha=.9;
      ctx.fill();ctx.globalAlpha=1;
      ctx.fillStyle='#aebccd';ctx.fillText(p.sym,x+7,y+3);
      px.push({{x,y,p}});
    }}
  }}
  cv.addEventListener('mousemove',e=>{{
    const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
    let best=null,bd=144;
    for(const q of px){{const d=(q.x-mx)**2+(q.y-my)**2; if(d<bd){{bd=d;best=q;}}}}
    if(best){{
      const p=best.p;
      tip.innerHTML='<b>'+p.sym+'</b> · $'+p.price+'<br>'+
        'corridor gap '+(p.gap>0?'+':'')+p.gap+'% ('+(p.gap>0?'below fair — inexpensive':'above fair — expensive')+')<br>'+
        'outlook x '+(p.x>0?'+':'')+p.x+' · bias '+p.bias+
        (p.composite!=null?' · composite '+p.composite:'');
      tip.style.left=Math.min(best.x+12, wrap.clientWidth-220)+'px';
      tip.style.top=(best.y+14)+'px'; tip.style.display='block';
    }} else tip.style.display='none';
  }});
  cv.addEventListener('mouseleave',()=>tip.style.display='none');
  window.addEventListener('resize',draw); draw();
}})();
</script>"""
