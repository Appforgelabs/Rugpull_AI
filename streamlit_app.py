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


def fetch_macro(client):
    """Fetch + store the shared market-regime snapshot."""
    import macro_engine as ME
    m = ME.build_macro(client)
    SS.save_macro(m)
    return m


# Your Google Apps Script web-app URL (same backend as the corridor chart).
# Hardcoded so sync works on every computer with no setup. Override via the
# APPS_SCRIPT_URL secret if you ever rotate it.
APPS_SCRIPT_URL_DEFAULT = "https://script.google.com/macros/s/AKfycbzbGKyBiLmWS7736GDhYeoKt6QHJIFKbywKza83N7AcfoeE4-cSYV4sNvydwuvK4LGWRw/exec"


def get_cloud_url():
    """The corridor's Apps Script URL: secret > session entry > hardcoded default."""
    try:
        if "APPS_SCRIPT_URL" in st.secrets:
            return st.secrets["APPS_SCRIPT_URL"]
    except Exception:
        pass
    return st.session_state.get("cloud_url") or APPS_SCRIPT_URL_DEFAULT


# ---- state ----------------------------------------------------------------
if "watchlist" not in st.session_state:
    st.session_state.watchlist = W.load_watchlist()
if "starred" not in st.session_state:
    st.session_state.starred = []
# one-time auto-pull from cloud so progress follows you across computers
if "cloud_pulled" not in st.session_state:
    st.session_state.cloud_pulled = True
    _url = get_cloud_url()
    if _url:
        try:
            import cloud_sync as CS
            blob = CS.load_app_state(_url)
            if blob:
                if blob.get("watchlist"):
                    st.session_state.watchlist = blob["watchlist"]
                if blob.get("starred"):
                    st.session_state.starred = blob["starred"]
        except Exception:
            pass  # silent on startup; manual sync surfaces errors

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

    with st.expander(f"📋 {len(syms)} tickers", expanded=False):
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
        try:
            fetch_macro(client)   # refresh the market regime first
        except Exception as e:
            st.warning(f"macro: {e}")
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

    # ---- cloud sync (cross-computer, via your corridor Apps Script) ----
    st.divider()
    st.caption("☁ Cloud sync (cross-computer)")
    cloud_url = get_cloud_url()
    if not cloud_url:
        st.session_state.cloud_url = st.text_input(
            "Apps Script URL (ends in /exec)",
            value=st.session_state.get("cloud_url", ""), type="password")
        cloud_url = st.session_state.cloud_url
    else:
        st.caption("✓ Connected (built-in URL)")

    if st.button("🔌 Test connection", use_container_width=True):
        import cloud_sync as CS
        st.session_state.conn_status = CS.test_connection(cloud_url)
    cs = st.session_state.get("conn_status")
    if cs:
        if cs["ok"]:
            st.success(f"🟢 {cs['status']} — {cs['detail']}")
        else:
            st.error(f"🔴 {cs['status']} — {cs['detail']}")

    sc1, sc2 = st.columns(2)
    if sc1.button("⬆ Save cloud", use_container_width=True):
        try:
            import cloud_sync as CS
            CS.save_app_state(cloud_url, st.session_state.watchlist,
                              st.session_state.starred)
            st.success("Saved to cloud")
        except Exception as e:
            st.error(f"{e}")
    if sc2.button("⬇ Load cloud", use_container_width=True):
        try:
            import cloud_sync as CS
            blob = CS.load_app_state(cloud_url)
            if blob:
                st.session_state.watchlist = blob.get("watchlist",
                                                       st.session_state.watchlist)
                st.session_state.starred = blob.get("starred",
                                                     st.session_state.starred)
                st.success("Loaded from cloud")
                st.rerun()
            else:
                st.info("Nothing saved in cloud yet — Save once to seed it.")
        except Exception as e:
            st.error(f"{e}")

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
tab_dash, tab1, tab_trade, tab_macro, tab_bt, tab_learn, tab2 = st.tabs(
    ["⬢ Dashboard", "Analyzer", "Trading", "Macro", "Backtest", "Learn",
     "Corridor Chart"])

with tab_dash:
    import dashboard as DB
    import macro_engine as ME
    import datetime as _dt

    macro_snap = SS.load_macro()
    macro = (macro_snap or {}).get("macro")
    mult = (macro or {}).get("risk_multiplier", 1.0)

    items = []
    newest = 0
    for s in syms:
        snap = SS.load_snapshot(s)
        if not snap:
            items.append({"symbol": s, "price": None, "result": None, "trading": None})
            continue
        newest = max(newest, snap.get("fetched_at", 0))
        trading = snap.get("trading")
        # regime re-weight: discount LONGs in risk-off, boost SHORTs, etc.
        if trading and trading.get("signal"):
            sg = trading["signal"]
            base = sg.get("probability")
            sg["raw_probability"] = base
            sg["probability"] = ME.apply_regime_to_conviction(
                base, sg.get("direction"), mult)
        items.append({
            "symbol": s,
            "price": (trading or {}).get("price")
                     or (snap.get("result") or {}).get("price"),
            "result": snap.get("result"),
            "trading": trading,
        })
    gen = (_dt.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
           if newest else "—")
    html = DB.render_dashboard(items, gen, macro=macro)
    st.components.v1.html(
        f'<meta charset="utf-8">{html}',
        height=max(430, 240 + 150 * len(items)), scrolling=True)
    st.caption("Synthesizes Analyzer + Trading + Macro snapshots. Conviction is "
               "regime-adjusted. Update via sidebar **⟳ Update all** and Trading "
               "tab **⟳ Update trading data**.")

with tab1:
    # starred values (synced across computers)
    if st.session_state.starred:
        with st.expander(f"⭐ Starred values ({len(st.session_state.starred)}) "
                         "— synced to cloud", expanded=False):
            st.dataframe(st.session_state.starred, use_container_width=True,
                         hide_index=True)
            st.caption("Save these across computers with **⬆ Save cloud** in the "
                       "sidebar. Star/unstar from each ticker below.")

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

                # ⭐ star/save this ticker's key values (syncs to cloud)
                starred_syms = [s["symbol"] for s in st.session_state.starred]
                if sym in starred_syms:
                    if st.button(f"★ Starred — remove {sym}", key=f"unstar_{sym}"):
                        st.session_state.starred = [
                            s for s in st.session_state.starred if s["symbol"] != sym]
                        st.rerun()
                else:
                    if st.button(f"☆ Star {sym}", key=f"star_{sym}"):
                        import datetime as _dt2
                        st.session_state.starred.append({
                            "symbol": sym, "price": r.get("price"),
                            "composite": r.get("composite_score"),
                            "pe": pe, "ps": r.get("multiples", {}).get("P/S"),
                            "pfcf": r.get("multiples", {}).get("P/FCF"),
                            "saved_at": _dt2.date.today().isoformat(),
                        })
                        st.rerun()

                # zone chart — native SVG, instant
                corr = r.get("zones", {}).get("corridor", {})
                esrc = corr.get("eps_source", "none")
                src_label = {"analyst_estimates": "real analyst estimates",
                             "proxy_ttm_x1.08": "TTM proxy (no estimates on plan)",
                             "none": "unavailable"}.get(esrc, esrc)
                st.markdown("**Probability zones** "
                            "<span class='muted'>(green ±1σ, red ±2σ; dashed = drift; "
                            f"dotted = valuation corridor · NTM EPS: {src_label} · "
                            f"side bands = volume-at-price, approx CVD)</span>",
                            unsafe_allow_html=True)
                svg = ZC.render_zone_html(r.get("series", []), r.get("zones", {}),
                                          profile=r.get("volume_profile"))
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
        # fetch SPY once for relative-strength comparisons
        spy_closes = None
        try:
            import trade_signals as _TS
            spy_closes = _TS.to_ohlcv(client.history("SPY"))["close"].tolist()
        except Exception:
            spy_closes = None
        for i, s in enumerate(syms):
            try:
                tr = A.build_trading(client, s, intraday_interval=interval,
                                     spy_closes=spy_closes)
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
        if snap and (snap.get("trading") or {}).get("ok"):
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
                # relative strength vs SPY (leading-ish single-name tell)
                rs = r.get("rel_strength")
                if rs and rs.get("ok"):
                    arrow = "↑ rising" if rs.get("rising") else "↓ falling"
                    st.markdown(f"**Relative strength vs S&P** "
                                f"<span class='muted'>(leading)</span>: "
                                f"{rs['read']} {rs['rs_vs_spy']}% · {arrow}",
                                unsafe_allow_html=True)

                # volume-at-price shelves (disposition-effect zones)
                vp = r.get("vp")
                if vp:
                    st.markdown("**Volume shelves** "
                                "<span class='muted'>(approx CVD · reaction "
                                "zones, not forecasts)</span>",
                                unsafe_allow_html=True)
                    v1, v2, v3, v4 = st.columns(4)
                    v1.metric("POC", vp.get("poc"))
                    v2.metric("Support shelf", vp.get("support") or "—")
                    v3.metric("Overhead shelf", vp.get("resistance") or "—")
                    v4.metric("Supply above", f"{vp.get('overhead_pct')}%")

                # mean-reversion stretch — shown SEPARATELY from trend bias
                mr = sg.get("meanrev")
                if mr and mr.get("stretch") is not None:
                    sc = ("#d6504f" if mr["stretch"] >= 0.5
                          else "#3fb37f" if mr["stretch"] <= -0.5 else "#8899aa")
                    st.markdown(
                        f"**Mean-reversion stretch** "
                        f"<span class='muted'>(separate philosophy — not in the "
                        f"trend score)</span>: "
                        f"<span style='color:{sc}'>{mr['state']} "
                        f"({mr['stretch']:+.2f})</span>", unsafe_allow_html=True)
                    st.caption("Trend says direction; stretch says whether it's "
                               "extended right now. 'Uptrend + stretched up' = "
                               "wait for a pullback rather than chase.")

                st.markdown("**Why this bias** (TREND votes · LEAD/LAG tagged)")
                votes = sg.get("votes", [])
                vtable = [{"Signal": v["signal"],
                           "Vote": "↑" if v["vote"] > 0 else "↓" if v["vote"] < 0 else "·",
                           "Lag": v.get("lag", "lagging").upper(),
                           "Note": v["note"]} for v in votes]
                st.dataframe(vtable, use_container_width=True, hide_index=True)
                st.caption("Trend votes are deduplicated (MA structure is one "
                           "vote, not five). Oscillator stretch is shown above, "
                           "not counted here. " + sg.get("note", ""))

with tab_macro:
    st.caption("The tide under every single-stock move. Leading signals (curve, "
               "VIX, breadth) try to see ahead; coincident ones describe now. "
               "This is context, not prediction.")
    msnap = SS.load_macro()
    if not msnap or not (msnap.get("macro") or {}).get("ok"):
        st.info("No macro data yet. Hit **⟳ Update all** in the sidebar.")
    else:
        mac = msnap["macro"]
        reg = mac.get("regime", "—")
        rc = {"RISK-ON": "#3fb37f", "RISK-OFF": "#d6504f"}.get(reg, "#6b7682")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Regime", reg)
        c2.metric("Conviction ×", mac.get("risk_multiplier"))
        c3.metric("10y–2y", mac.get("spread_10y_2y"))
        c4.metric("VIX", mac.get("vix"))

        st.markdown("**Signals** — each tagged by how forward-looking it is")
        rows = []
        for s in mac.get("signals", []):
            rows.append({"Lag": s["lag"].upper(), "Signal": s["name"],
                         "Read": s["read"], "Value": s["value"],
                         "Bias": s["bias"]})
        st.dataframe(rows, use_container_width=True, hide_index=True)

        lead = [s for s in mac["signals"] if s["lag"] == "leading"]
        st.markdown(f"**Leading signals ({len(lead)})** — the ones that try to "
                    "see ahead:")
        for s in lead:
            st.write(f"• {s['name']}: **{s['read']}** ({s['bias']})")
        st.caption(mac.get("note", ""))

with tab_bt:
    import backtest as BT
    st.caption("Does any of this have edge? Replays daily swing strategies with "
               "point-in-time correctness (no lookahead), real costs, and "
               "buy-and-hold as the benchmark. The number that matters is "
               "EXCESS vs holding — and whether edge survives out-of-sample.")

    bc1, bc2, bc3 = st.columns([2, 2, 2])
    bt_sym = bc1.selectbox("Ticker", syms or ["SPY"])
    strat_name = bc2.selectbox("Strategy", list(BT.STRATEGIES.keys()))
    yrs = bc3.selectbox("History", [2, 3, 5], index=2)

    oc1, oc2, oc3, oc4 = st.columns(4)
    allow_short = oc1.checkbox("Allow shorts", value=False)
    atr_stop = oc2.slider("ATR stop (0=off)", 0.0, 4.0, 2.0, 0.5)
    cost_bps = oc3.slider("Cost per side (bps)", 0, 30, 5, 1)
    do_split = oc4.checkbox("Out-of-sample split", value=True)

    if st.button("▶ Run backtest", type="primary"):
        with st.spinner("Replaying history bar by bar…"):
            try:
                import trade_signals as _TS
                hist = client.history(bt_sym)
                df = _TS.to_ohlcv(hist)
                # trim to requested years (~252 trading days/yr + warmup)
                need = yrs * 252 + 220
                if len(df) > need:
                    df = df.iloc[-need:].reset_index(drop=True)
                fn = BT.STRATEGIES[strat_name]
                kw = dict(allow_short=allow_short, atr_stop=atr_stop,
                          commission=cost_bps/10000, slippage=cost_bps/10000)
                res = BT.run_backtest(df, fn, **kw)
                split_res = BT.run_split(df, fn, **kw) if do_split else None
            except Exception as e:
                res = {"ok": False, "note": f"{type(e).__name__}: {e}"}
                split_res = None

        if not res.get("ok"):
            st.error(f"Backtest failed: {res.get('note')}")
        else:
            # verdict banner
            beat = res["beat_hold"]
            vc = "#3fb37f" if (beat and res["significant"]) else "#e6a23c" if res["significant"] else "#6b7682"
            st.markdown(
                f'<div style="border:1px solid {vc};border-radius:4px;padding:12px 16px;'
                f'margin:8px 0;font-family:JetBrains Mono,monospace;color:{vc};'
                f'font-size:13px">{res["verdict"]}</div>', unsafe_allow_html=True)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Strategy return", f"{res['total_return_pct']}%")
            m2.metric("Buy & hold", f"{res['buy_hold_pct']}%")
            m3.metric("Excess vs hold", f"{res['excess_vs_hold_pct']}%",
                      delta=("beat" if beat else "lost"))
            m4.metric("Sharpe", res["sharpe"])
            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Trades", res["num_trades"])
            m6.metric("Win rate", f"{res['win_rate_pct']}%")
            m7.metric("Max drawdown", f"{res['max_drawdown_pct']}%")
            m8.metric("Profit factor", res["profit_factor"])
            if not res["significant"]:
                st.warning(f"⚠ Only {res['num_trades']} trades — below 30, not "
                           "statistically meaningful. Treat as anecdote.")

            # equity curve vs buy & hold
            eq = res["equity_curve"]
            bh = res["buy_hold_curve"]
            L = min(len(eq), len(bh))
            if L > 1:
                import pandas as _pd
                chart_df = _pd.DataFrame({
                    "Strategy": eq[-L:], "Buy & Hold": bh[-L:]})
                st.line_chart(chart_df, height=260)

            # out-of-sample
            if split_res and split_res.get("ok"):
                hc = "#3fb37f" if split_res["holds_up"] else "#d6504f"
                st.markdown(
                    f'<div style="border-left:3px solid {hc};padding:8px 14px;'
                    f'margin:10px 0;font-size:13px">{split_res["verdict"]}</div>',
                    unsafe_allow_html=True)
                sp1, sp2 = st.columns(2)
                ins, oos = split_res["in_sample"], split_res["out_sample"]
                sp1.metric("In-sample excess", f"{ins['excess_vs_hold_pct']}%",
                           f"{ins['num_trades']} trades")
                sp2.metric("Out-sample excess", f"{oos['excess_vs_hold_pct']}%",
                           f"{oos['num_trades']} trades")
            elif split_res:
                st.caption(f"Split test skipped: {split_res.get('note')}")

            # trade list
            with st.expander(f"Trade log ({res['num_trades']} trades)"):
                st.dataframe(res["trades"], use_container_width=True, hide_index=True)

with tab_learn:
    import learn_content as LC
    st.subheader("How to read every signal in this app")
    st.caption("What each value measures, how to use it well, and the honest "
               "caveat. Most chart indicators are lagging — they describe the "
               "past. Read the principles first.")

    with st.expander("⭐ Core principles — read these first", expanded=True):
        for p in LC.PRINCIPLES:
            st.markdown(f"- {p}")

    cats = sorted(set(g[1] for g in LC.GUIDE), key=lambda c: c)
    pick = st.multiselect("Filter by category", cats, default=[])
    lag_color = {"leading": "#3fb37f", "coincident": "#e6a23c",
                 "lagging": "#d6504f", "n/a": "#6b7682"}

    for name, cat, lag, what, read, use, watch in LC.GUIDE:
        if pick and cat not in pick:
            continue
        lc = lag_color.get(lag, "#6b7682")
        with st.expander(f"{name}  ·  {cat}"):
            if lag != "n/a":
                st.markdown(
                    f'<span style="color:{lc};border:1px solid {lc};'
                    f'border-radius:3px;padding:1px 8px;font-size:11px;'
                    f'font-weight:600">{lag.upper()}</span>',
                    unsafe_allow_html=True)
            st.markdown(f"**What it is** — {what}")
            st.markdown(f"**How to read it** — {read}")
            st.markdown(f"**How to use it well** — {use}")
            st.markdown(f"**Watch out** — {watch}")

with tab2:
    st.caption("Original corridor chart. Loads on demand to keep the app fast.")
    if st.checkbox("Load corridor chart"):
        html = Path(__file__).parent.joinpath("corridor.html").read_text(encoding="utf-8")
        inject = (f"window.__FMP_KEY__={json.dumps(key)};"
                  f"window.__RUGPULL_WATCHLIST__={json.dumps(st.session_state.watchlist)};")
        html = html.replace("/*__RUGPULL_INJECT__*/", inject)
        st.components.v1.html(html, height=1000, scrolling=True)
