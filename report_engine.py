"""
report_engine.py — deep-dive reports + a long/short setup scanner.

Two modes, both built ONLY from data the app already computes (snapshots):

  1. deep_dive(snap, macro) — one ticker, everything: valuation, corridor,
     full technicals, the signal vote breakdown, volume shelves, scenario
     summary, macro context, and the honest caveats. Rendered to markdown/HTML.

  2. scan_ideas(snaps, macro, filters) — screens the whole watchlist for names
     that currently MATCH a long or short SETUP. This is a scanner that surfaces
     candidates for you to investigate — NOT a list of trades the app endorses.
     The backtest has repeatedly shown these signals don't reliably beat
     buy-and-hold; every report says so.

Honest framing is not decoration here — it's the point. A report that looks
authoritative while presenting lagging indicators as "ideas" would mislead. The
renderers carry the caveats inline.
"""

from __future__ import annotations
import datetime as dt
import html as _html


# ---------- scanner ----------------------------------------------------------
def _corr(result):
    return ((result or {}).get("zones") or {}).get("corridor") or {}


def scan_ideas(snaps: dict, macro: dict | None, *, direction="both",
               timeframe="swing", min_score=0, profitable_only=False,
               min_upside=None, ledger_stats: dict | None = None) -> dict:
    """Screen snapshots for long/short setups, ranked by SETUP QUALITY.

    Design: the trend bias is only the GATE (it's lagging — it decides who's
    eligible, not who ranks first). The score is built from evidence with some
    forward information content:
      leading-ish : relative strength vs SPY, corridor valuation gap, news
                    sentiment, regime alignment, bootstrap P(up) from the
                    ticker's own return distribution
      positioning : distance to volume shelves (entry quality, not prediction)
      timing      : mean-reversion stretch (rewards pullback-in-trend,
                    penalizes chasing an extended move)
      measured    : the prediction ledger's realized hit-rates — the app's own
                    empirical record, the only factor that's actually validated
    Nothing here makes markets predictable; the ledger is the honest test."""
    regime = (macro or {}).get("regime", "—")
    mult = (macro or {}).get("risk_multiplier", 1.0)
    stats = ledger_stats or {}
    longs, shorts = [], []

    def _hit(name):
        s = stats.get(name) or {}
        n = s.get("n", 0)
        return (s.get("hits", 0) / n) if n >= 8 else None

    for sym, snap in snaps.items():
        result = (snap or {}).get("result")
        trading = (snap or {}).get("trading")
        if not trading or not trading.get("ok"):
            continue
        sg = trading.get("signal") or {}
        lane = (sg.get(timeframe) or {})
        bias = lane.get("bias")
        if bias not in ("LONG", "SHORT"):
            continue
        side = 1 if bias == "LONG" else -1
        price = trading.get("price")
        conv = sg.get("probability", 50) or 50
        composite = (result or {}).get("composite_score")

        corr = _corr(result)
        upside = None
        if corr.get("ok") and corr.get("fair") and price:
            upside = round((corr["fair"] - price) / price * 100, 1)

        stx = (trading.get("supertrend") or {})
        vp = trading.get("vp") or {}
        adx = sg.get("adx")
        rs = (trading.get("rel_strength") or {}).get("rs_vs_spy")
        sent = ((result or {}).get("sentiment") or {}).get("score")
        atr = trading.get("atr14")
        stretch = ((sg.get("meanrev") or {}).get("stretch"))

        ev = []   # (factor, pts, tag, note)

        # relative strength (leading-ish)
        if rs is not None:
            pts = max(-8.0, min(10.0, side * rs * 0.5))
            ev.append(("Rel strength vs SPY", round(pts, 1), "leading",
                       f"{rs:+.1f}% over 63d (persists short-term)"))

        # corridor valuation gap — a 12-MONTH anchor: at a ≤2-week horizon
        # valuation has ~no predictive power, so it's context, not a driver
        if upside is not None:
            pts = max(-4.0, min(4.0, side * upside * 0.08))
            ev.append(("Corridor gap (context)", round(pts, 1), "context",
                       f"{upside:+.0f}% to fair value — 12-mo anchor, weak at "
                       f"2-wk horizon"))

        # bootstrap P(up) from the ticker's own return history (probabilistic)
        p_up = None
        series = (result or {}).get("series") or (snap or {}).get("prices") or []
        closes = [p.get("c") for p in series if p.get("c")]
        if len(closes) >= 80:
            try:
                import scenario_engine as SE
                sim = SE.simulate(closes, horizon=10, n_paths=200, seed=7)
                if sim.get("ok"):
                    p_up = sim["prob_above_spot"]
                    pts = max(-12.0, min(12.0, side * (p_up - 50) * 0.6))
                    ev.append(("Bootstrap P(up 10d)", round(pts, 1), "leading",
                               f"{p_up:.0f}% of simulated paths end up"))
            except Exception:
                pass

        # news sentiment (leading-ish)
        if sent is not None:
            pts = max(-8.0, min(8.0, side * (sent - 50) / 50 * 8))
            ev.append(("News sentiment", round(pts, 1), "leading",
                       f"{sent:.0f}/100"))

        # regime alignment (context)
        pts = (6.0 if mult > 1 else -8.0 if mult < 1 else 0.0) * side
        if pts:
            ev.append(("Regime alignment", round(pts, 1), "leading",
                       f"{regime} ×{mult}"))

        # volume shelf positioning (entry quality, not prediction)
        if atr and price:
            if side > 0 and vp.get("support"):
                d = (price - vp["support"]) / atr
                if 0 <= d <= 2:
                    ev.append(("On support shelf", 12.0, "positioning",
                               f"{d:.1f} ATR above shelf {vp['support']}"))
            if side < 0 and vp.get("resistance"):
                d = (vp["resistance"] - price) / atr
                if 0 <= d <= 2:
                    ev.append(("Under overhead shelf", 12.0, "positioning",
                               f"{d:.1f} ATR below shelf {vp['resistance']}"))
        op = vp.get("overhead_pct")
        if op is not None:
            if side > 0 and op < 25:
                ev.append(("Light overhead supply", 6.0, "positioning", f"{op}% above"))
            if side < 0 and op > 50:
                ev.append(("Heavy overhead supply", 6.0, "positioning", f"{op}% above"))

        # mean-reversion timing: reward pullback-in-trend, penalize chasing
        if stretch is not None:
            if side * stretch <= -0.8:
                ev.append(("Pullback entry", 9.0, "timing",
                           f"stretch {stretch:+.2f} against bias"))
            elif side * stretch >= 1.5:
                ev.append(("Chasing extended move", -9.0, "timing",
                           f"stretch {stretch:+.2f} with bias"))

        # TD Sequential exhaustion (counter-trend timing)
        td = trading.get("demark") or {}
        if td.get("ok"):
            fresh = [x for x in td.get("recent_setups", [])
                     if x.get("bars_ago", 99) <= 5]
            cd = td.get("countdown") or {}
            for x in fresh[-1:]:
                if (x["side"] == "BUY") == (side > 0):
                    pts = 12.0 if x.get("perfected") else 10.0
                    ev.append(("TD exhaustion for entry", pts, "timing",
                               f"TD {x['side']} 9 · {x['bars_ago']}b ago"
                               + (" perfected" if x.get("perfected") else "")))
                else:
                    ev.append(("TD exhaustion against entry", -9.0, "timing",
                               f"TD {x['side']} 9 {x['bars_ago']}b ago"))
            if cd.get("complete"):
                pts = 9.0 if (cd["side"] == "BUY") == (side > 0) else -9.0
                ev.append(("TD Countdown 13 complete", pts, "timing",
                           f"{cd['side']} 13/13 — deep exhaustion"))
            elif cd.get("count", 0) >= 11:
                pts = 7.0 if (cd["side"] == "BUY") == (side > 0) else -7.0
                ev.append(("TD Countdown late", pts, "timing",
                           f"{cd['side']} {cd['count']}/13"))

        # the app's own MEASURED record (prediction ledger hit-rates)
        hr = _hit(timeframe.upper()) or _hit("OVERALL")
        if hr is not None:
            pts = max(-10.0, min(10.0, (hr - 0.5) * 80))
            ev.append(("Measured hit-rate", round(pts, 1), "measured",
                       f"{hr*100:.0f}% on scored calls (7d scoring — matches "
                       f"this horizon)"))

        # asymmetric setup bonus: planned R:R >= 2 from the lane's levels
        su = (lane.get("setup") or {})
        try:
            rr_v = float(str(su.get("rr", "")).rstrip("Rr"))
        except (TypeError, ValueError):
            rr_v = None
        if rr_v and rr_v >= 2.0:
            ev.append(("Asymmetric setup", 4.0, "positioning",
                       f"planned R:R {rr_v:.1f} (entry/stop/target)"))

        # trend strength — small, and honestly tagged lagging
        if adx and adx >= 25:
            ev.append(("Strong trend (ADX)", 4.0, "lagging", f"ADX {adx:.0f}"))

        setup_score = round(max(0, min(100, 50 + sum(p for _, p, _, _ in ev))))

        row = {
            "symbol": sym, "price": price, "bias": bias,
            "setup_score": setup_score, "conviction": conv, "adx": adx,
            "upside": upside, "composite": composite, "p_up": p_up,
            "supertrend": "up" if stx.get("dir", 0) > 0 else "down" if stx.get("dir", 0) < 0 else "—",
            "rel_strength": rs,
            "support_shelf": vp.get("support"), "overhead_shelf": vp.get("resistance"),
            "evidence": [{"factor": f, "pts": p, "tag": t, "note": n}
                         for f, p, t, n in ev],
        }

        if setup_score < min_score:
            continue
        if min_upside is not None and bias == "LONG" and (upside is None or upside < min_upside):
            continue
        if profitable_only and (composite is None or composite < 45):
            continue

        (longs if bias == "LONG" else shorts).append(row)

    longs.sort(key=lambda r: r["setup_score"], reverse=True)
    shorts.sort(key=lambda r: r["setup_score"], reverse=True)

    out = {"regime": regime, "timeframe": timeframe,
           "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "note": "SHORT-TERM ideas (horizon ≤ 2 weeks). Trend is only the "
                   "gate (lagging); the score favors horizon-appropriate "
                   "evidence: TD exhaustion, pullback-vs-chase, volume-shelf "
                   "positioning, 10-day bootstrap odds, RS, sentiment, regime, "
                   "and the ledger's MEASURED 7-day hit-rates. The corridor "
                   "gap is shown as context only — a 12-month valuation anchor "
                   "says little about the next two weeks. Candidates to "
                   "investigate — not endorsed trades, not predictions."}
    if direction in ("both", "long"):
        out["longs"] = longs
    if direction in ("both", "short"):
        out["shorts"] = shorts
    return out


# ---------- deep dive --------------------------------------------------------
def deep_dive(snap: dict, macro: dict | None) -> dict:
    """Assemble the full structured report for one ticker from its snapshot."""
    result = (snap or {}).get("result") or {}
    trading = (snap or {}).get("trading") or {}
    sym = result.get("symbol") or trading.get("symbol") or "—"
    sg = trading.get("signal") or {}
    corr = _corr(result)

    price = trading.get("price") or result.get("price")
    fair = corr.get("fair") if corr.get("ok") else None
    upside = round((fair - price) / price * 100, 1) if (fair and price) else None

    return {
        "symbol": sym,
        "company": result.get("company", sym),
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "price": price,
        "composite": result.get("composite_score"),
        "valuation": {
            "fair_value": fair, "upside_pct": upside,
            "pe": (result.get("multiples") or {}).get("P/E"),
            "ps": (result.get("multiples") or {}).get("P/S"),
            "pfcf": (result.get("multiples") or {}).get("P/FCF"),
            "ev_ebitda": (result.get("multiples") or {}).get("EV/EBITDA"),
            "dcf": (result.get("dcf") or {}).get("intrinsic_value"),
            "pe_median": (result.get("pe_distribution") or {}).get("median"),
        },
        "signal": {
            "direction": sg.get("direction"), "conviction": sg.get("probability"),
            "adx": sg.get("adx"), "trend_strength": sg.get("trend_strength"),
            "swing": sg.get("swing"), "day": sg.get("day"),
            "votes": sg.get("votes", []), "meanrev": sg.get("meanrev"),
        },
        "technicals": {
            "rsi": {k: trading.get(k) for k in ("rsi_1d", "rsi_D", "rsi_W", "rsi_M")},
            "sma": {k: trading.get(k) for k in ("sma20", "sma50", "sma200", "sma325")},
            "supertrend": trading.get("supertrend"),
            "macd": trading.get("macd"), "atr": trading.get("atr14"),
            "vwap": trading.get("vwap_session") or trading.get("vwap_roll20"),
        },
        "volume_shelves": trading.get("vp"),
        "demark": trading.get("demark"),
        "rel_strength": trading.get("rel_strength"),
        "sentiment": (result.get("sentiment") or {}).get("score"),
        "macro": {"regime": (macro or {}).get("regime"),
                  "risk_multiplier": (macro or {}).get("risk_multiplier"),
                  "vix": (macro or {}).get("vix"),
                  "spread_10y_2y": (macro or {}).get("spread_10y_2y")},
        "has_data": bool(result or trading),
    }


# ---------- renderers --------------------------------------------------------
def deep_dive_markdown(d: dict) -> str:
    v, s, t = d["valuation"], d["signal"], d["technicals"]
    L = []
    L.append(f"# {d['symbol']} — {d['company']}")
    L.append(f"*Report generated {d['generated']} · price ${d.get('price','—')}*\n")
    L.append("> Not financial advice. Valuations are model outputs; signals are "
             "mostly lagging indicators that have not reliably beaten "
             "buy-and-hold in backtests. Use as research, not as a trade call.\n")

    L.append("## Snapshot")
    L.append(f"- **Composite score:** {d.get('composite','—')}/100")
    L.append(f"- **Bias:** {s.get('direction','—')} · conviction {s.get('conviction','—')}% "
             f"(indicator agreement, capped 72%)")
    L.append(f"- **Trend strength:** ADX {s.get('adx','—')} ({s.get('trend_strength','—')})\n")

    L.append("## Valuation")
    if v.get("fair_value"):
        L.append(f"- **Corridor fair value:** ${v['fair_value']} "
                 f"({v['upside_pct']:+.0f}% vs price)" if v.get("upside_pct") is not None
                 else f"- **Corridor fair value:** ${v['fair_value']}")
    else:
        L.append("- **Corridor fair value:** n/a (no positive forward EPS)")
    L.append(f"- P/E {v.get('pe','—')} (hist median {v.get('pe_median','—')}) · "
             f"P/S {v.get('ps','—')} · P/FCF {v.get('pfcf','—')} · "
             f"EV/EBITDA {v.get('ev_ebitda','—')}")
    if v.get("dcf"):
        L.append(f"- DCF intrinsic value: ${v['dcf']}\n")
    else:
        L.append("")

    L.append("## Swing setup (days–weeks)")
    sw = s.get("swing") or {}
    L.append(f"- **{sw.get('bias','—')}** ({sw.get('bull','?')} bull / {sw.get('bear','?')} bear)")
    if sw.get("setup"):
        su = sw["setup"]
        L.append(f"- Entry ${su.get('entry')} · Stop ${su.get('stop')} · "
                 f"Target ${su.get('target')} · R:R {su.get('rr')}")
    L.append(f"- Basis: {sw.get('basis','—')}\n")

    L.append("## Day setup (intraday)")
    dy = s.get("day") or {}
    L.append(f"- **{dy.get('bias','—')}** · basis: {dy.get('basis','—')}")
    if dy.get("setup"):
        su = dy["setup"]
        L.append(f"- Entry ${su.get('entry')} · Stop ${su.get('stop')} · "
                 f"Target ${su.get('target')} · R:R {su.get('rr')}\n")
    else:
        L.append("")

    L.append("## Technicals")
    rsi = t.get("rsi", {})
    L.append(f"- RSI — 1d {rsi.get('rsi_1d','—')} · D {rsi.get('rsi_D','—')} · "
             f"W {rsi.get('rsi_W','—')} · M {rsi.get('rsi_M','—')}")
    sma = t.get("sma", {})
    L.append(f"- SMA — 20 {sma.get('sma20','—')} · 50 {sma.get('sma50','—')} · "
             f"200 {sma.get('sma200','—')} · 325 {sma.get('sma325','—')}")
    stt = t.get("supertrend") or {}
    L.append(f"- Supertrend: {stt.get('value','—')} "
             f"({'up' if stt.get('dir',0)>0 else 'down' if stt.get('dir',0)<0 else '—'})")
    mh = (t.get("macd") or {}).get("hist")
    L.append(f"- MACD hist {mh if mh is not None else '—'} · ATR {t.get('atr','—')} · "
             f"VWAP {t.get('vwap','—')}")
    mr = s.get("meanrev") or {}
    if mr.get("stretch") is not None:
        L.append(f"- Mean-reversion stretch: {mr.get('state')} ({mr['stretch']:+.2f}) "
                 f"— separate from trend\n")
    else:
        L.append("")

    vp = d.get("volume_shelves")
    if vp:
        L.append("## Volume shelves (approx CVD)")
        L.append(f"- POC {vp.get('poc','—')} · support {vp.get('support','—')} · "
                 f"overhead {vp.get('resistance','—')} · supply above "
                 f"{vp.get('overhead_pct','—')}%\n")
    td = d.get("demark") or {}
    if td.get("ok"):
        L.append("## TD Sequential (exhaustion — unofficial published rules)")
        if td.get("read"):
            L.append(f"- **{td['read']}**")
        elif td.get("setup_count"):
            L.append(f"- {td['setup_side']} setup {td['setup_count']}/9 in progress")
        else:
            L.append("- No active setup")
        for x in (td.get("recent_setups") or [])[-3:]:
            L.append(f"- {x.get('side','?')} 9 on {x.get('date','?')} "
                     f"({x.get('bars_ago','?')}b ago)"
                     + (" · perfected" if x.get("perfected") else "")
                     + (f" · TDST {x.get('tdst')}" if x.get("tdst") else ""))
        cd = td.get("countdown") or {}
        if cd:
            L.append(f"- Countdown: {cd.get('side')} {cd.get('count')}/13"
                     + (" — COMPLETE" if cd.get("complete") else ""))
        L.append("- *Counter-trend flags (fail in strong trends); hit-rate "
                 "measured by the prediction ledger.*\n")

    rs = d.get("rel_strength") or {}
    if rs.get("ok"):
        L.append("## Relative strength")
        L.append(f"- vs SPY: {rs.get('read')} {rs.get('rs_vs_spy')}% · "
                 f"{'rising' if rs.get('rising') else 'falling'} (leading-ish)\n")

    m = d.get("macro", {})
    L.append("## Macro context")
    L.append(f"- Regime: **{m.get('regime','—')}** (conviction ×{m.get('risk_multiplier','—')}) · "
             f"10y-2y {m.get('spread_10y_2y','—')} · VIX {m.get('vix','—')}")
    if d.get("sentiment") is not None:
        L.append(f"- News sentiment: {d['sentiment']}/100\n")

    L.append("## Why this bias (signal votes)")
    _has_w = any(vt.get("weight") not in (None, 1.0) for vt in s.get("votes", []))
    if _has_w:
        L.append("| Signal | Vote | Learned weight | Lag | Note |")
        L.append("|---|---|---|---|---|")
    else:
        L.append("| Signal | Vote | Lag | Note |")
        L.append("|---|---|---|---|")
    for vt in s.get("votes", []):
        arrow = "↑" if vt["vote"] > 0 else "↓" if vt["vote"] < 0 else "·"
        if _has_w:
            L.append(f"| {vt['signal']} | {arrow} | {vt.get('weight', 1.0)}× | "
                     f"{vt.get('lag','')} | {vt.get('note','')} |")
        else:
            L.append(f"| {vt['signal']} | {arrow} | {vt.get('lag','')} | {vt.get('note','')} |")
    L.append("\n*Trend votes are deduplicated; oscillator stretch shown separately, "
             "not counted in the trend score.*")
    return "\n".join(L)


def deep_dive_html(d: dict) -> str:
    """Wrap the markdown render in a clean printable HTML shell."""
    md = deep_dive_markdown(d)
    # minimal md->html (headers, bold, lists, tables handled crudely)
    body = _md_to_html(md)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{_html.escape(d['symbol'])} report</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:820px;margin:40px auto;
padding:0 20px;color:#1a1a1a;line-height:1.55}}
h1{{border-bottom:2px solid #ff6b1a;padding-bottom:8px}}
h2{{margin-top:28px;color:#0f1318;border-bottom:1px solid #ddd;padding-bottom:4px}}
blockquote{{background:#fff8f3;border-left:3px solid #ff6b1a;margin:0;padding:10px 16px;
color:#555;font-size:14px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
th,td{{border:1px solid #ddd;padding:5px 8px;text-align:left}}
th{{background:#f5f5f5}}
code{{background:#f0f0f0;padding:1px 4px;border-radius:3px}}
em{{color:#777}}
</style></head><body>{body}</body></html>"""


def scan_markdown(scan: dict) -> str:
    L = [f"# Trading ideas scan — {scan['timeframe']} setups",
         f"*Generated {scan['generated']} · regime {scan['regime']}*\n",
         f"> {scan['note']}\n"]
    for side, key in [("LONG candidates", "longs"), ("SHORT candidates", "shorts")]:
        if key not in scan:
            continue
        rows = scan[key]
        L.append(f"## {side} ({len(rows)})")
        if not rows:
            L.append("_None currently match._\n")
            continue
        L.append("| Ticker | Setup | Price | P(up 10d) | Upside | RS vs SPY | Conv % |")
        L.append("|---|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| {r['symbol']} | {r['setup_score']} | {r.get('price','—')} | "
                     f"{r.get('p_up','—')} | {r.get('upside','—')} | "
                     f"{r.get('rel_strength','—')} | {r['conviction']} |")
        L.append("")
        for r in rows[:8]:
            evs = "; ".join(f"{e['factor']} {e['pts']:+.0f} [{e['tag']}]"
                            for e in r["evidence"])
            L.append(f"**{r['symbol']}** (setup {r['setup_score']}) — {evs}")
        L.append("")
    return "\n".join(L)


# ---------- tiny markdown->html ---------------------------------------------
def _md_to_html(md: str) -> str:
    import re
    lines = md.split("\n")
    out = []
    in_table = False
    in_list = False
    for ln in lines:
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue  # separator row
            tag = "td"
            if not in_table:
                out.append("<table>"); in_table = True
                tag = "th"
            out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>"); in_table = False
        if ln.startswith("# "):
            out.append(f"<h1>{_inline(ln[2:])}</h1>")
        elif ln.startswith("## "):
            out.append(f"<h2>{_inline(ln[3:])}</h2>")
        elif ln.startswith("> "):
            out.append(f"<blockquote>{_inline(ln[2:])}</blockquote>")
        elif ln.startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(ln[2:])}</li>")
        elif ln.strip() == "":
            if in_list:
                out.append("</ul>"); in_list = False
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<p>{_inline(ln)}</p>")
    if in_list: out.append("</ul>")
    if in_table: out.append("</table>")
    return "\n".join(out)


def _inline(s: str) -> str:
    import re
    s = _html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s
