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
               timeframe="swing", min_conviction=0, profitable_only=False,
               min_upside=None) -> dict:
    """Screen snapshots for long/short setups. Returns ranked candidate lists."""
    regime = (macro or {}).get("regime", "—")
    longs, shorts = [], []

    for sym, snap in snaps.items():
        result = (snap or {}).get("result")
        trading = (snap or {}).get("trading")
        if not trading or not trading.get("ok"):
            continue
        sg = trading.get("signal") or {}
        lane = (sg.get(timeframe) or {})
        bias = lane.get("bias")
        conv = sg.get("probability", 50) or 50
        price = trading.get("price")

        # quality / upside filters
        composite = (result or {}).get("composite_score")
        if profitable_only and (composite is None or composite < 50):
            # composite < 50 is a rough proxy; unprofitable names usually land low
            pass  # soft filter — don't hard-drop, just note. Hard filter below if set.

        corr = _corr(result)
        upside = None
        if corr.get("ok") and corr.get("fair") and price:
            upside = round((corr["fair"] - price) / price * 100, 1)

        # supporting evidence
        st = (trading.get("supertrend") or {})
        vp = trading.get("vp") or {}
        adx = sg.get("adx")
        rs = (trading.get("rel_strength") or {})

        row = {
            "symbol": sym, "price": price, "bias": bias,
            "conviction": conv, "adx": adx,
            "upside": upside, "composite": composite,
            "supertrend": "up" if st.get("dir", 0) > 0 else "down" if st.get("dir", 0) < 0 else "—",
            "rel_strength": rs.get("rs_vs_spy"),
            "support_shelf": vp.get("support"), "overhead_shelf": vp.get("resistance"),
            "reasons": _reasons(sg, lane, st, corr, price, upside, rs, "long" if bias == "LONG" else "short"),
        }

        if conv < min_conviction:
            continue
        if min_upside is not None and bias == "LONG" and (upside is None or upside < min_upside):
            continue
        if profitable_only and (composite is None or composite < 45):
            continue

        if bias == "LONG":
            longs.append(row)
        elif bias == "SHORT":
            shorts.append(row)

    longs.sort(key=lambda r: (r["conviction"], r["upside"] or -999), reverse=True)
    shorts.sort(key=lambda r: r["conviction"], reverse=True)

    out = {"regime": regime, "timeframe": timeframe,
           "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "note": "These are names matching a long/short SETUP right now — "
                   "candidates to investigate, NOT endorsed trades. Signals are "
                   "mostly lagging and have not reliably beaten buy-and-hold in "
                   "backtests. Position sizing and your own diligence matter more "
                   "than any signal here."}
    if direction in ("both", "long"):
        out["longs"] = longs
    if direction in ("both", "short"):
        out["shorts"] = shorts
    return out


def _reasons(sg, lane, st, corr, price, upside, rs, side) -> list:
    r = []
    if lane.get("bias") in ("LONG", "SHORT"):
        r.append(f"{lane['basis']} → {lane['bias']} ({lane.get('score')}/{lane.get('max')})")
    if sg.get("adx") and sg["adx"] >= 25:
        r.append(f"strong trend (ADX {sg['adx']})")
    if st.get("dir"):
        r.append(f"Supertrend {'up' if st['dir']>0 else 'down'}")
    if upside is not None:
        r.append(f"corridor {'upside' if upside>0 else 'downside'} {upside:+.0f}%")
    if rs.get("rs_vs_spy") is not None:
        r.append(f"RS vs SPY {rs['rs_vs_spy']:+.1f}% ({'leading' if rs['rs_vs_spy']>0 else 'lagging'})")
    return r


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
    L.append("| Signal | Vote | Lag | Note |")
    L.append("|---|---|---|---|")
    for vt in s.get("votes", []):
        arrow = "↑" if vt["vote"] > 0 else "↓" if vt["vote"] < 0 else "·"
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
        L.append("| Ticker | Price | Conv % | ADX | Upside | Supertrend | RS vs SPY |")
        L.append("|---|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| {r['symbol']} | {r.get('price','—')} | {r['conviction']} | "
                     f"{r.get('adx','—')} | {r.get('upside','—')} | {r['supertrend']} | "
                     f"{r.get('rel_strength','—')} |")
        L.append("")
        for r in rows[:8]:
            L.append(f"**{r['symbol']}** — " + "; ".join(r["reasons"]))
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
