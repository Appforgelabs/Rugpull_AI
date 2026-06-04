"""
streamlit_app.py — Rugpull_AI

Minimalist analyzer with stored snapshots. Data is fetched ONLY when you hit
Update for a ticker (or Update all). Everything else reads instantly from the
on-disk snapshot — no lag, no re-fetch on tab switches or slider drags.

Tabs:
  • Analyzer       — fundamentals table + composite + prediction zone chart
  • Corridor Chart — your original chart, embedded (load on demand)

FMP key comes from st.secrets or the FMP_API_KEY env var. Never committed.
"""

from __future__ import annotations
import os
import json
from pathlib import Path

import streamlit as st

from fmp_client import FMPClient, FMPError
import analyze as A
import signals as S
import watchlist as W
import snapshot_store as SS
import zone_chart as ZC

st.set_page_config(page_title="Rugpull_AI", page_icon="📈", layout="wide")

# ---- minimalist styling ---------------------------------------------------
st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; max-width: 1100px;}
  [data-testid="stMetricValue"] {font-size: 1.1rem;}
  h1 {font-weight: 600; letter-spacing: -0.5px;}
  .muted {color:#8899aa; font-size:0.85rem;}
  .pill {display:inline-block; padding:2px 10px; border-radius:10px;
         font-size:0.78rem; font-weight:600;}
</style>
""", unsafe_allow_html=True)


def get_key():
    try:
        if "FMP_API_KEY" in st.secrets:
            return st.secrets["FMP_API_KEY"]
    except Exception:
        pass
    return os.environ.get("FMP_API_KEY")


@st.cache_resource
def get_client(key: str) -> FMPClient:
    return FMPClient(api_key=key)


def show_svg(svg: str, height: int = 430):
    """Render the interactive chart widget. Uses components.html (not sanitized,
    unlike st.markdown) so the canvas + JS hover/toggle work."""
    st.components.v1.html(svg, height=height, scrolling=False)


def fetch_and_store(client, sym):
    """The ONLY place that hits the network. Freezes a snapshot to disk."""
    res = A.analyze(client, sym, S.FMPNewsSentiment(client))
    SS.save_snapshot(sym, res, price_series=res.get("series"))
    return res


# ---- state ----------------------------------------------------------------
if "watchlist" not in st.session_state:
    st.session_state.watchlist = W.load_watchlist()

st.title("Rugpull_AI")
st.markdown('<div class="muted">Transparent scoring + σ-based probability zones. '
            'Data is stored until you hit Update. Not financial advice; the zones '
            'are probability ranges, not forecasts.</div>', unsafe_allow_html=True)

key = get_key()
if not key:
    st.error("No FMP API key. Streamlit Cloud: Settings → Secrets → "
             "`FMP_API_KEY = \"...\"`. Local: set FMP_API_KEY env var.")
    st.stop()
client = get_client(key)
syms = [t["symbol"] for t in st.session_state.watchlist]

# ---- sidebar ---------------------------------------------------------------
with st.sidebar:
    st.subheader("Watchlist")
    new = st.text_input("Add ticker", placeholder="PLTR").upper().strip()
    if st.button("Add", use_container_width=True) and new:
        if new not in syms:
            st.session_state.watchlist.append({"symbol": new, "name": new})
            st.rerun()

    for i, t in enumerate(list(st.session_state.watchlist)):
        c1, c2 = st.columns([4, 1])
        snap = SS.load_snapshot(t["symbol"])
        age = SS.age_str(snap) if snap else "no data"
        c1.write(f"**{t['symbol']}**  ·  {age}")
        if c2.button("✕", key=f"d{i}"):
            st.session_state.watchlist.pop(i)
            SS.delete_snapshot(t["symbol"])
            st.rerun()

    st.divider()
    if st.button("⟳ Update all", type="primary", use_container_width=True):
        prog = st.progress(0.0)
        for i, s in enumerate(syms):
            try:
                fetch_and_store(client, s)
            except Exception as e:
                st.warning(f"{s}: {e}")
            prog.progress((i + 1) / max(len(syms), 1))
        prog.empty()
        st.rerun()

    ca, cb = st.columns(2)
    if ca.button("Save list", use_container_width=True):
        W.save_watchlist(st.session_state.watchlist)
        st.success("Saved")
    cb.download_button("Export", use_container_width=True,
                       data=json.dumps({"watchlist": st.session_state.watchlist}, indent=2),
                       file_name="tickers.json", mime="application/json")

    st.divider()
    st.caption("Weights")
    w = {}
    for leg, default in A.WEIGHTS.items():
        w[leg] = st.slider(leg, 0.0, 1.0, float(default), 0.05)
    tot = sum(w.values()) or 1.0
    A.WEIGHTS = {k: v / tot for k, v in w.items()}

    macro = S.macro_regime(client)
    st.caption(f"Macro: {macro.get('regime','?')} · tilt ×{macro.get('tilt',1.0)}")

# ---- tabs ------------------------------------------------------------------
tab_dash, tab1, tab_trade, tab2 = st.tabs(
    ["⬢ Dashboard", "Analyzer", "Trading", "Corridor Chart"])

with tab_dash:
    import dashboard as DB
    import datetime as _dt
    items = []
    newest = 0
    for s in syms:
        snap = SS.load_snapshot(s)
        if not snap:
            items.append({"symbol": s, "price": None, "result": None, "trading": None})
            continue
        newest = max(newest, snap.get("fetched_at", 0))
        items.append({
            "symbol": s,
            "price": (snap.get("trading") or {}).get("price")
                     or (snap.get("result") or {}).get("price"),
            "result": snap.get("result"),
            "trading": snap.get("trading"),
        })
    gen = (_dt.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
           if newest else "—")
    html = DB.render_dashboard(items, gen)
    # wrap with explicit UTF-8 so glyphs/entities render correctly in the iframe
    st.components.v1.html(
        f'<meta charset="utf-8">{html}',
        height=max(360, 150 + 150 * len(items)), scrolling=True)
    st.caption("Synthesizes Analyzer + Trading snapshots. Update both "
               "(sidebar **⟳ Update all** and Trading tab **⟳ Update trading "
               "data**) to populate every lane.")

with tab1:
    # build rows from STORED snapshots only — no network here
    rows = []
    for s in syms:
        snap = SS.load_snapshot(s)
        if snap and snap.get("result"):
            rows.append(snap["result"])

    if not rows:
        st.info("No stored data yet. Hit **⟳ Update all** in the sidebar.")
    else:
        rows.sort(key=lambda r: r.get("composite_score", 0), reverse=True)

        # compact ranking table with the multiples you asked for
        table = []
        for r in rows:
            m = r.get("multiples", {})
            table.append({
                "Symbol": r["symbol"], "Price": r.get("price"),
                "Score": r.get("composite_score"),
                "P/E": m.get("P/E"), "P/S": m.get("P/S"), "P/FCF": m.get("P/FCF"),
                "P/B": m.get("P/B"), "EV/EBITDA": m.get("EV/EBITDA"),
                "ROE": m.get("ROE"),
            })
        st.dataframe(table, use_container_width=True, hide_index=True)

        for r in rows:
            sym = r["symbol"]
            keep_open = st.session_state.get("just_updated") == sym
            with st.expander(f"{sym} — {r.get('company', sym)}  ·  "
                             f"{r.get('composite_score')}/100  ·  "
                             f"{A._verdict(r.get('composite_score', 0))}",
                             expanded=keep_open):
                top = st.columns([1, 1, 1, 1])
                top[0].metric("Price", r.get("price"))
                top[1].metric("Composite", f"{r.get('composite_score')}/100")
                pe = r.get("multiples", {}).get("P/E")
                top[2].metric("P/E", pe)
                pd_ = r.get("pe_distribution", {})
                top[3].metric("P/E hist median", pd_.get("median"))

                # zone chart — native SVG, instant
                corr = r.get("zones", {}).get("corridor", {})
                esrc = corr.get("eps_source", "none")
                src_label = {"analyst_estimates": "real analyst estimates",
                             "proxy_ttm_x1.08": "TTM proxy (no estimates on plan)",
                             "none": "unavailable"}.get(esrc, esrc)
                st.markdown("**Probability zones** "
                            "<span class='muted'>(green ±1σ, red ±2σ; dashed = drift; "
                            f"dotted = valuation corridor · NTM EPS: {src_label})</span>",
                            unsafe_allow_html=True)
                svg = ZC.render_zone_html(r.get("series", []), r.get("zones", {}))
                show_svg(svg, height=430)

                # full multiples grid
                m = r.get("multiples", {})
                st.markdown("**Valuation multiples**")
                cols = st.columns(5)
                items = list(m.items())
                for idx, (k, val) in enumerate(items):
                    cols[idx % 5].metric(k, val if val is not None else "—")

                # entry/exit
                ee = r.get("entry_exit", {})
                if ee:
                    st.markdown("**Entry / exit (rule-based)**")
                    e = st.columns(4)
                    e[0].metric("Support", ee.get("support"))
                    e[1].metric("Resistance", ee.get("resistance"))
                    e[2].metric("VWAP band",
                                f"{ee.get('vwap_lower_band')}–{ee.get('vwap_upper_band')}")
                    e[3].metric("Stop 2·ATR", ee.get("suggested_stop_2atr"))

                # per-ticker update
                if st.button(f"⟳ Update {sym}", key=f"u_{sym}"):
                    try:
                        fetch_and_store(client, sym)
                        st.session_state.just_updated = sym
                        st.rerun()
                    except Exception as ex:
                        st.warning(f"{sym}: {ex}")

with tab_trade:
    st.caption("Multi-timeframe technicals + a transparent long/short rules "
               "score. Stored until you Update — the score is indicator agreement, "
               "not a backtested win-rate.")

    interval = st.selectbox("Intraday interval (for 1-day RSI + session VWAP)",
                            ["1min", "5min", "15min", "30min", "1hour"], index=1)
    if st.button("⟳ Update trading data", type="primary"):
        prog = st.progress(0.0)
        ok_count, fails = 0, []
        for i, s in enumerate(syms):
            try:
                tr = A.build_trading(client, s, intraday_interval=interval)
                if tr.get("ok"):
                    SS.save_trading(s, tr)
                    ok_count += 1
                else:
                    fails.append(f"{s}: no daily price data returned (rate limit "
                                 "or symbol issue)")
            except Exception as e:
                fails.append(f"{s}: {type(e).__name__}: {e}")
            prog.progress((i + 1) / max(len(syms), 1))
        prog.empty()
        if ok_count:
            st.success(f"Updated {ok_count}/{len(syms)} tickers.")
        for f in fails:
            st.error(f)
        if ok_count:
            st.rerun()

    trows = []
    for s in syms:
        snap = SS.load_snapshot(s)
        if snap and snap.get("trading", {}).get("ok"):
            trows.append(snap["trading"])

    if not trows:
        st.info("No trading data yet. Hit **⟳ Update trading data** above.")
    else:
        # headline long/short table
        def _arrow(d):
            return {"LONG": "🟢 LONG", "SHORT": "🔴 SHORT", "NEUTRAL": "⚪ NEUTRAL"}.get(d, d)
        head = []
        for r in trows:
            sg = r.get("signal", {})
            head.append({
                "Symbol": r["symbol"], "Price": r.get("price"),
                "Bias": _arrow(sg.get("direction")),
                "Prob %": sg.get("probability"),
                "ADX": sg.get("adx"), "Trend": sg.get("trend_strength"),
                "RSI 1d": r.get("rsi_1d"), "RSI D": r.get("rsi_D"),
                "RSI W": r.get("rsi_W"), "RSI M": r.get("rsi_M"),
            })
        head.sort(key=lambda x: (x["Prob %"] or 0), reverse=True)
        st.dataframe(head, use_container_width=True, hide_index=True)

        for r in trows:
            sg = r.get("signal", {})
            with st.expander(f"{r['symbol']} · {_arrow(sg.get('direction'))} · "
                             f"{sg.get('probability')}% · "
                             f"{sg.get('bull_votes')}↑/{sg.get('bear_votes')}↓"):
                if not r.get("intraday_available"):
                    st.caption("⚠ Intraday unavailable on this fetch — 1-day RSI / "
                               "session VWAP may be blank. Daily/weekly/monthly still valid.")

                # moving averages row
                st.markdown("**Trend — price vs moving averages**")
                mcols = st.columns(5)
                price = r.get("price")
                for idx, k in enumerate(["sma20", "sma50", "sma200", "sma325"]):
                    v = r.get(k)
                    delta = (f"{((price-v)/v*100):+.1f}%" if v and price else None)
                    mcols[idx].metric(k.upper().replace("SMA", "SMA "), v, delta)
                mcols[4].metric("VWAP(sess)", r.get("vwap_session") or r.get("vwap_roll20"))

                # oscillators row
                st.markdown("**Momentum & oscillators**")
                ocols = st.columns(6)
                ocols[0].metric("MACD hist", r.get("macd", {}).get("hist"))
                ocols[1].metric("ADX", r.get("adx"))
                ocols[2].metric("Stoch %K", r.get("stoch", {}).get("k"))
                ocols[3].metric("CCI", r.get("cci"))
                ocols[4].metric("Williams %R", r.get("williams_r"))
                bb = r.get("bb", {})
                ocols[5].metric("BB %b", bb.get("pctb"))

                # levels
                lv1, lv2 = st.columns(2)
                with lv1:
                    st.markdown("**Pivot points**")
                    p = r.get("pivots", {})
                    if p:
                        st.write(f"R3 {p.get('R3')} · R2 {p.get('R2')} · R1 {p.get('R1')}")
                        st.write(f"**P {p.get('P')}**")
                        st.write(f"S1 {p.get('S1')} · S2 {p.get('S2')} · S3 {p.get('S3')}")
                with lv2:
                    st.markdown("**Fibonacci**")
                    f = r.get("fib", {})
                    if f:
                        st.write(f"swing {f.get('low')}–{f.get('high')} ({f.get('dir')})")
                        st.write(f"0.382 {f.get('0.382')} · 0.5 {f.get('0.5')} · "
                                 f"0.618 {f.get('0.618')}")

                # the vote breakdown — show the work
                st.markdown("**Why this bias** (signal votes)")
                votes = sg.get("votes", [])
                vtable = [{"Signal": v["signal"],
                           "Vote": "↑" if v["vote"] > 0 else "↓" if v["vote"] < 0 else "·",
                           "Note": v["note"]} for v in votes]
                st.dataframe(vtable, use_container_width=True, hide_index=True)
                st.caption(sg.get("note", ""))

with tab2:
    st.caption("Original corridor chart. Loads on demand to keep the app fast.")
    if st.checkbox("Load corridor chart"):
        html = Path(__file__).parent.joinpath("corridor.html").read_text(encoding="utf-8")
        inject = (f"window.__FMP_KEY__={json.dumps(key)};"
                  f"window.__RUGPULL_WATCHLIST__={json.dumps(st.session_state.watchlist)};")
        html = html.replace("/*__RUGPULL_INJECT__*/", inject)
        st.components.v1.html(html, height=1000, scrolling=True)
