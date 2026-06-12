"""
scenario_engine.py — near-term price scenarios from the stock's own history.

Block-bootstrap Monte Carlo: resample 5-day blocks of the ticker's actual past
daily returns (preserving short-term autocorrelation and fat tails) and roll
them forward N times to build an ensemble of possible paths. From the ensemble:
  • a percentile fan (P10/P25/P50/P75/P90) of where price could be each day
  • a handful of sample paths drawn on the chart
  • terminal stats: probability of finishing above today, median, P10/P90

Runs entirely from the price series already stored in snapshots — no network.
"""

from __future__ import annotations
import json
import numpy as np


def simulate(closes: list[float], horizon: int = 21, n_paths: int = 400,
             block: int = 5, lookback: int = 504, seed: int | None = None) -> dict:
    closes = [c for c in closes if c and c == c]
    if len(closes) < 80:
        return {"ok": False, "note": "need >=80 closes"}
    px = np.asarray(closes, dtype=float)
    rets = np.diff(np.log(px))[-lookback:]
    if len(rets) < 40:
        return {"ok": False, "note": "insufficient returns"}

    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(horizon / block))
    starts_max = len(rets) - block
    spot = float(px[-1])

    paths = np.empty((n_paths, horizon))
    for p in range(n_paths):
        seq = np.concatenate([
            rets[s:s + block] for s in rng.integers(0, starts_max, n_blocks)
        ])[:horizon]
        paths[p] = spot * np.exp(np.cumsum(seq))

    pct = {k: np.percentile(paths, q, axis=0)
           for k, q in [("p10", 10), ("p25", 25), ("p50", 50),
                        ("p75", 75), ("p90", 90)]}
    terminal = paths[:, -1]

    sample_idx = rng.choice(n_paths, size=min(24, n_paths), replace=False)
    return {
        "ok": True, "spot": round(spot, 2), "horizon": horizon,
        "n_paths": n_paths,
        "pct": {k: [round(float(x), 2) for x in v] for k, v in pct.items()},
        "samples": [[round(float(x), 2) for x in paths[i]] for i in sample_idx],
        "prob_above_spot": round(float((terminal > spot).mean()) * 100, 1),
        "median_end": round(float(np.median(terminal)), 2),
        "p10_end": round(float(np.percentile(terminal, 10)), 2),
        "p90_end": round(float(np.percentile(terminal, 90)), 2),
        "note": "Block-bootstrap of this ticker's own past returns. The spread "
                "of paths is the message: it is the realistic range of nearby "
                "futures given how this stock has actually moved.",
    }


def render_scenarios_html(series: list, sim: dict, height: int = 380) -> str:
    """Static canvas chart: recent history + sample paths + percentile fan."""
    if not series or not sim.get("ok"):
        return ("<div style='color:#8899aa;padding:24px;font:14px system-ui'>"
                "No data — run Update all first.</div>")
    hist = [p["c"] for p in series[-90:]]
    payload = json.dumps({"hist": hist, "pct": sim["pct"],
                          "samples": sim["samples"], "spot": sim["spot"]})
    return f"""
<div id="sc-wrap" style="font-family:system-ui;color:#cdd6e0;width:100%">
  <canvas id="sc" style="width:100%;height:{height}px;display:block"></canvas>
</div>
<script>
(function(){{
  const D={payload};
  const cv=document.getElementById('sc'), ctx=cv.getContext('2d');
  const wrap=document.getElementById('sc-wrap');
  function draw(){{
    const dpr=window.devicePixelRatio||1, w=wrap.clientWidth, h={height};
    cv.width=w*dpr; cv.height=h*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
    const padL=8,padR=54,padT=14,padB=10;
    const plotW=w-padL-padR, plotH=h-padT-padB;
    const nH=D.hist.length, nF=D.pct.p50.length, histFrac=0.45;
    let ylo=Infinity,yhi=-Infinity;
    for(const v of D.hist){{ylo=Math.min(ylo,v);yhi=Math.max(yhi,v);}}
    for(const v of D.pct.p10)ylo=Math.min(ylo,v);
    for(const v of D.pct.p90)yhi=Math.max(yhi,v);
    for(const s of D.samples)for(const v of s){{ylo=Math.min(ylo,v);yhi=Math.max(yhi,v);}}
    const m=(yhi-ylo)*0.05||1; ylo-=m; yhi+=m;
    const xH=i=>padL+(nH<=1?0:(i/(nH-1))*plotW*histFrac);
    const xF=j=>padL+plotW*histFrac+((j+1)/nF)*plotW*(1-histFrac);
    const Y=v=>padT+plotH-((v-ylo)/(yhi-ylo))*plotH;
    const x0=xH(nH-1);
    // grid + y labels
    ctx.strokeStyle='#222a38';ctx.fillStyle='#8899aa';ctx.font='11px system-ui';
    ctx.textAlign='left';
    for(let g=0;g<=4;g++){{const val=ylo+(yhi-ylo)*g/4,y=Y(val);
      ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(padL+plotW,y);ctx.stroke();
      ctx.fillText('$'+val.toFixed(val<10?2:0),padL+plotW+6,y+3);}}
    // P10–P90 fan
    ctx.fillStyle='rgba(70,214,200,0.10)';ctx.beginPath();ctx.moveTo(x0,Y(D.spot));
    for(let j=0;j<nF;j++)ctx.lineTo(xF(j),Y(D.pct.p90[j]));
    for(let j=nF-1;j>=0;j--)ctx.lineTo(xF(j),Y(D.pct.p10[j]));
    ctx.closePath();ctx.fill();
    // P25–P75 inner
    ctx.fillStyle='rgba(70,214,200,0.14)';ctx.beginPath();ctx.moveTo(x0,Y(D.spot));
    for(let j=0;j<nF;j++)ctx.lineTo(xF(j),Y(D.pct.p75[j]));
    for(let j=nF-1;j>=0;j--)ctx.lineTo(xF(j),Y(D.pct.p25[j]));
    ctx.closePath();ctx.fill();
    // sample paths (faint)
    ctx.lineWidth=0.8;
    for(const s of D.samples){{
      ctx.strokeStyle=s[s.length-1]>=D.spot?'rgba(63,179,127,0.30)':'rgba(214,90,90,0.30)';
      ctx.beginPath();ctx.moveTo(x0,Y(D.spot));
      for(let j=0;j<s.length;j++)ctx.lineTo(xF(j),Y(s[j]));
      ctx.stroke();}}
    // median path
    ctx.setLineDash([5,4]);ctx.strokeStyle='#e6a23c';ctx.lineWidth=1.4;
    ctx.beginPath();ctx.moveTo(x0,Y(D.spot));
    for(let j=0;j<nF;j++)ctx.lineTo(xF(j),Y(D.pct.p50[j]));
    ctx.stroke();ctx.setLineDash([]);
    // history
    ctx.strokeStyle='#46d6c8';ctx.lineWidth=1.8;ctx.beginPath();
    D.hist.forEach((v,i)=>{{const x=xH(i),y=Y(v);i?ctx.lineTo(x,y):ctx.moveTo(x,y);}});
    ctx.stroke();
    // now divider
    ctx.strokeStyle='#3a4658';ctx.globalAlpha=0.5;ctx.lineWidth=0.8;
    ctx.beginPath();ctx.moveTo(x0,padT);ctx.lineTo(x0,padT+plotH);ctx.stroke();
    ctx.globalAlpha=1;
    ctx.fillStyle='#46d6c8';ctx.beginPath();ctx.arc(x0,Y(D.spot),3,0,7);ctx.fill();
  }}
  window.addEventListener('resize',draw); draw();
}})();
</script>"""
