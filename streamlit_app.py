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
import datetime as dt
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


def lookup_ticker(client, sym):
    """On-demand full analysis for ANY ticker (not just watchlist). Stores a
    snapshot so other tabs/report can use it, and also fetches trading data."""
    sym = sym.upper().strip()
    res = fetch_and_store(client, sym)
    try:
        import trade_signals as _TS
        _spy = _TS.to_ohlcv(client.history("SPY"))["close"].tolist()
    except Exception:
        _spy = None
    try:
        tr = A.build_trading(client, sym, intraday_interval="5min", spy_closes=_spy)
        if tr.get("ok"):
            SS.save_trading(sym, tr)
    except Exception:
        pass
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
if "favorites" not in st.session_state:
    st.session_state.favorites = []   # simple symbol list — for spot-ability
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
                if blob.get("favorites") is not None:
                    st.session_state.favorites = blob["favorites"]
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

# ---- app settings (visible tabs + inactive tickers), persisted to the Sheet --
if "app_settings" not in st.session_state:
    st.session_state.app_settings = {"visible_tabs": None, "inactive": []}
    try:
        import cloud_sync as _CS
        _blob = _CS.load_blob(get_cloud_url(), "settings")
        if _blob:
            st.session_state.app_settings.update(
                {k: _blob.get(k) for k in ("visible_tabs", "inactive")
                 if _blob.get(k) is not None})
    except Exception:
        pass

def save_settings():
    try:
        import cloud_sync as _CS
        _CS.save_blob(get_cloud_url(), "settings",
                      dict(st.session_state.app_settings, version=1))
    except Exception:
        pass

syms_all = [t["symbol"] for t in st.session_state.watchlist]
_inactive = set(st.session_state.app_settings.get("inactive") or [])
# `syms` = ACTIVE tickers only — drives updates, rankings, scans, ledger, paper.
# Inactive tickers stay in the watchlist (and in old snapshots/search) but are
# skipped by analysis until re-enabled.
syms = [s for s in syms_all if s not in _inactive]

# ---- favorites (symbol set for spot-ability, synced to the Sheet) ----------
def _fav_save():
    try:
        import cloud_sync as _CS
        blob = _CS.load_blob(get_cloud_url(), "rugpull") or {}
        blob["favorites"] = st.session_state.favorites
        _CS.save_blob(get_cloud_url(), "rugpull", blob)
    except Exception:
        pass

def is_fav(sym):
    return sym in st.session_state.favorites

def fav_toggle(sym, key_prefix=""):
    """Inline ★/☆ button — click to add/remove a favorite. Returns nothing;
    reruns on change so markers refresh everywhere."""
    on = is_fav(sym)
    label = "★" if on else "☆"
    if st.button(label, key=f"fav_{key_prefix}_{sym}",
                 help="Remove favorite" if on else "Mark favorite"):
        if on:
            st.session_state.favorites = [
                s for s in st.session_state.favorites if s != sym]
        else:
            st.session_state.favorites = st.session_state.favorites + [sym]
        _fav_save()
        st.rerun()

def fav_mark(sym):
    """Return '★ ' prefix if favorite, else '' — for table cells/labels."""
    return "★ " if is_fav(sym) else ""



# ---- 🔎 find-anything search (type → match → full profile inline) ----------
_q = st.text_input("search",
                   placeholder="🔎 Find any ticker or company… then press Enter",
                   label_visibility="collapsed", key="global_search")
if _q and _q.strip():
    _ql = _q.strip().upper()
    _snaps_idx = SS.all_snapshots()
    _names = {t["symbol"]: t.get("name", t["symbol"])
              for t in st.session_state.watchlist}
    _universe = sorted(set(list(_names) + list(_snaps_idx)))
    _hits = [s for s in _universe
             if _ql in s or _ql in str(_names.get(s, "")).upper()
             or _ql in str(((_snaps_idx.get(s) or {}).get("result") or {})
                           .get("company", "")).upper()]
    if _hits:
        _sel = _hits[0] if len(_hits) == 1 else st.selectbox(
            f"{len(_hits)} matches — pick one", _hits, key="search_pick")
        _snp = _snaps_idx.get(_sel) or {}
        _res = _snp.get("result") or {}
        _trd = _snp.get("trading") or {}
        _sg = _trd.get("signal") or {}
        _corr = ((_res.get("zones") or {}).get("corridor") or {})
        _px = _trd.get("price") or _res.get("price")
        _up = (round((_corr["fair"] - _px) / _px * 100, 1)
               if _corr.get("ok") and _corr.get("fair") and _px else None)
        fav_toggle(_sel, "search")
        _paused = " · ⏸ PAUSED" if _sel in _inactive else ""
        st.markdown(f"#### {fav_mark(_sel)}{_sel} — {_res.get('company', _names.get(_sel, _sel))}"
                    f"{_paused}  <span class='muted'>({SS.age_str(_snp) if _snp else 'no data'})</span>",
                    unsafe_allow_html=True)
        if not (_res or _trd):
            st.info(f"{_sel} is known but has no stored analysis yet — hit "
                    "⟳ Update all, or analyze it on demand below.")
            if st.button(f"⚡ Analyze {_sel} now", key="search_analyze_known"):
                with st.spinner(f"Analyzing {_sel}…"):
                    try:
                        lookup_ticker(client, _sel)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't analyze {_sel}: {e}")
        else:
            _c1, _c2, _c3, _c4 = st.columns(4)
            _c1.metric("Price", f"${_px}" if _px else "—")
            _c2.metric("Composite", _res.get("composite_score", "—"))
            _c3.metric("Bias", f"{_sg.get('direction','—')}",
                       f"{_sg.get('probability','')}% agreement" if _sg.get("probability") else None,
                       delta_color="off")
            _c4.metric("Corridor gap", f"{_up:+.0f}%" if _up is not None else "—")
            _d1, _d2, _d3, _d4 = st.columns(4)
            _d1.metric("Swing", ((_sg.get("swing") or {}).get("bias")) or "—")
            _d2.metric("Day", ((_sg.get("day") or {}).get("bias")) or "—")
            _rs = (_trd.get("rel_strength") or {}).get("rs_vs_spy")
            _d3.metric("RS vs SPY", f"{_rs:+.1f}%" if _rs is not None else "—")
            _sent = (_res.get("sentiment") or {}).get("score")
            _d4.metric("Sentiment", f"{_sent:.0f}/100" if _sent is not None else "—")
            _vp = _trd.get("vp") or {}
            if _vp:
                st.caption(f"Volume shelves — POC {_vp.get('poc','—')} · "
                           f"support {_vp.get('support','—')} · "
                           f"overhead {_vp.get('resistance','—')} · "
                           f"supply above {_vp.get('overhead_pct','—')}%")
            _series = _res.get("series") or _snp.get("prices") or []
            if len(_series) > 10:
                import pandas as _spd
                _sc = _spd.Series({p["d"]: p["c"] for p in _series[-120:]},
                                  name=_sel)
                st.line_chart(_sc, height=160)
            st.caption("Full detail → Analyzer · Trading · Report tabs.")
    else:
        st.caption(f"No match for '{_q}' in your data.")
        if _ql.isalnum() and len(_ql) <= 5:
            if st.button(f"⚡ Analyze {_ql} on demand (any US ticker)",
                         key="search_analyze_new"):
                with st.spinner(f"Analyzing {_ql}…"):
                    try:
                        lookup_ticker(client, _ql)
                        st.session_state.last_lookup = _ql
                        st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't analyze {_ql}: {e}")


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
        try:
            import prediction_tracker as PT
            cyc = PT.auto_cycle(syms, SS.load_snapshot, get_cloud_url())
            sv = cyc.get("save", {})
            if sv.get("cloud"):
                st.toast(f"✓ Ledger saved to cloud: +{cyc['recorded']} recorded, "
                         f"{cyc['scored']} scored, {cyc['pending']} pending")
            else:
                st.toast(f"⚠ LEDGER NOT SAVED TO CLOUD: {sv.get('error')} — "
                         f"data will be lost on reboot!", icon="⚠️")
        except Exception:
            pass
        # daily paper-portfolio mark-to-market with the fresh prices, so the
        # performance chart stays continuous even if the Paper tab isn't opened.
        # Only when the cloud load SUCCEEDED (never save over data we couldn't read).
        try:
            import paper_portfolio as PP
            _pp, _pst = PP.load_portfolio(get_cloud_url())
            if _pst == "loaded" and _pp.get("positions"):
                _ppx = {}
                for _s in set(syms + ["SPY"]):
                    _sn = SS.load_snapshot(_s)
                    _px = (((_sn or {}).get("trading") or {}).get("price")
                           or ((_sn or {}).get("result") or {}).get("price"))
                    if _px:
                        _ppx[_s] = _px
                if _ppx:
                    PP.snapshot_value(_pp, _ppx)
                    PP.save_portfolio(_pp, get_cloud_url())
        except Exception:
            pass
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
ALL_TABS = [("⬢ Dashboard", "tab_dash"), ("Analyzer", "tab1"),
            ("Trading", "tab_trade"), ("Scenarios", "tab_sc"),
            ("Research", "tab_research"), ("Paper Trade", "tab_paper"),
            ("Report", "tab_report"), ("Macro", "tab_macro"),
            ("Backtest", "tab_bt"), ("Learn", "tab_learn"),
            ("Corridor Chart", "tab2"), ("Map", "tab_map"),
            ("CVD", "tab_cvd"), ("Seasonality", "tab_seas"),
            ("⚙ Settings", "tab_set")]
_visible = st.session_state.app_settings.get("visible_tabs") or [n for n, _ in ALL_TABS]
_visible = [n for n, _ in ALL_TABS if n in _visible] or [n for n, _ in ALL_TABS]
if "⚙ Settings" not in _visible:      # can never be hidden — it's the way back
    _visible.append("⚙ Settings")
_created = st.tabs(_visible)
_lookup = dict(zip(_visible, _created))
tab_dash, tab1, tab_trade, tab_sc, tab_research, tab_paper, tab_report, \
    tab_macro, tab_bt, tab_learn, tab2, tab_map, tab_cvd, tab_seas, tab_set = (
        _lookup.get(n) for n, _ in ALL_TABS)

if tab_dash is not None:
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

if tab1 is not None:
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

if tab_trade is not None:
    with tab_trade:
        st.caption("Multi-timeframe technicals + a transparent long/short rules "
                   "score. Stored until you Update — the score is indicator agreement, "
                   "not a backtested win-rate.")

        interval = st.selectbox("Intraday interval (for 1-day RSI + session VWAP)",
                                ["1min", "5min", "15min", "30min", "1hour"], index=1)
        if st.button("⟳ Update trading data", type="primary"):
            prog = st.progress(0.0)
            ok_count, fails = 0, []
            import prediction_tracker as PT
            _learned_weights = PT.signal_weights(PT.load_ledger(get_cloud_url()))
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
                                         spy_closes=spy_closes,
                                         weights=_learned_weights)
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
                try:
                    cyc = PT.auto_cycle(syms, SS.load_snapshot, get_cloud_url())
                    st.toast(f"Tracker: +{cyc['recorded']} recorded, "
                             f"{cyc['scored']} scored")
                except Exception:
                    pass
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
            def _td_cell(r):
                td = r.get("demark") or {}
                if not td.get("ok"):
                    return "—"
                cd = td.get("countdown") or {}
                if cd.get("count", 0) >= 11:
                    return f"{cd['side'][0]}·CD {cd['count']}/13"
                rec = [x for x in (td.get("recent_setups") or [])
                       if x.get("bars_ago", 99) <= 9]
                if rec:
                    x = rec[-1]
                    return f"{x['side']} 9 ({x['bars_ago']}b)"
                if td.get("setup_count"):
                    return f"{td['setup_side'][0]} {td['setup_count']}/9"
                return "—"

            def _td_plain(r):
                td = r.get("demark") or {}
                if not td.get("ok"):
                    return "not computed — run ⟳ Update trading data"
                cd = td.get("countdown") or {}
                if cd.get("complete"):
                    return (f"Countdown 13 done — deep "
                            f"{'downside' if cd['side'] == 'BUY' else 'upside'} "
                            f"exhaustion (strongest flag)")
                if cd.get("count", 0) >= 11:
                    return (f"Countdown {cd['count']}/13 — deeper "
                            f"{'downside' if cd['side'] == 'BUY' else 'upside'} "
                            f"exhaustion building")
                rec = [x for x in (td.get("recent_setups") or [])
                       if x.get("bars_ago", 99) <= 9]
                if rec:
                    x = rec[-1]
                    if x["side"] == "BUY":
                        return (f"Decline looks tired — buy setup 9 completed "
                                f"{x['bars_ago']}b ago"
                                + (", perfected" if x.get("perfected") else "")
                                + " (bounce zone)")
                    return (f"Rally looks tired — sell setup 9 completed "
                            f"{x['bars_ago']}b ago"
                            + (", perfected" if x.get("perfected") else "")
                            + " (stall/pullback zone)")
                if td.get("setup_count", 0) >= 6:
                    side = ("selling streak" if td["setup_side"] == "BUY"
                            else "buying streak")
                    return (f"{td['setup_count']}/9 — persistent {side}, "
                            f"watch for 9")
                if td.get("setup_count"):
                    return f"{td['setup_count']}/9 counting — not a signal yet"
                return "no exhaustion signal (normal state)"

            head = []
            for r in trows:
                sg = r.get("signal", {})
                head.append({
                    "Symbol": r["symbol"], "Price": r.get("price"),
                    "Bias": _arrow(sg.get("direction")),
                    "Prob %": sg.get("probability"),
                    "ADX": sg.get("adx"), "Trend": sg.get("trend_strength"),
                    "TD": _td_cell(r),
                    "RSI 1d": r.get("rsi_1d"), "RSI D": r.get("rsi_D"),
                    "RSI W": r.get("rsi_W"), "RSI M": r.get("rsi_M"),
                    "TD meaning": _td_plain(r),
                })
            head.sort(key=lambda x: (x["Prob %"] or 0), reverse=True)
            st.dataframe(
                head, use_container_width=True, hide_index=True,
                column_config={
                    "TD": st.column_config.TextColumn(
                        "TD",
                        help="TD Sequential (DeMark) — exhaustion counter, in "
                             "plain English:\n\n"
                             "• **BUY 9 (3b)** — nine straight days of closes "
                             "below the close 4 days earlier, completed 3 bars "
                             "ago. The DECLINE looks tired → bounce zone.\n\n"
                             "• **SELL 9 (6b)** — nine straight days of closes "
                             "above 4 days earlier, 6 bars ago. The RALLY looks "
                             "tired → stall/pullback zone.\n\n"
                             "• **B 4/9 or S 7/9** — a count in progress, not "
                             "yet a signal.\n\n"
                             "• **B·CD 12/13** — post-setup Countdown near 13 "
                             "= deeper exhaustion.\n\n"
                             "• **—** — nothing active (the normal state).\n\n"
                             "Counter-trend flags, not commands — they fail in "
                             "strong trends, and the prediction ledger measures "
                             "their real hit-rate on your names. Open a "
                             "ticker's dropdown for its full TD sentence."),
                    "TD meaning": st.column_config.TextColumn(
                        "TD meaning", width="large",
                        help="Plain-English decode of the TD column — "
                             "exhaustion flags, not commands."),
                    "Prob %": st.column_config.NumberColumn(
                        "Prob %",
                        help="Trend-vote agreement (deduplicated), capped at "
                             "72%. NOT a win-rate — the ledger measures those."),
                })

            for r in trows:
                sg = r.get("signal", {})
                with st.expander(f"{fav_mark(r['symbol'])}{r['symbol']} · {_arrow(sg.get('direction'))} · "
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
                    _fc1, _fc2 = st.columns([1, 11])
                    with _fc1:
                        fav_toggle(r["symbol"], "trade")
                    with _fc2:
                        st.caption("★ favorite" if is_fav(r["symbol"])
                                   else "click ☆ to favorite")
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

                    # TD Sequential exhaustion state (unofficial published rules)
                    td = r.get("demark")
                    if td is None:
                        st.caption("TD Sequential: not computed yet — this snapshot "
                                   "predates the DeMark update. Hit ⟳ Update "
                                   "trading data to populate.")
                    elif td.get("ok"):
                        if td.get("read"):
                            st.markdown(f"**TD Sequential:** {td['read']} "
                                        "<span class='muted'>(exhaustion flag — "
                                        "counter-trend, measured by the ledger)</span>",
                                        unsafe_allow_html=True)
                        elif td.get("setup_count"):
                            st.caption(f"TD Sequential: {td['setup_side']} setup "
                                       f"{td['setup_count']}/9 in progress")
                        else:
                            _lasttd = (td.get("recent_setups") or [])
                            if _lasttd:
                                _x = _lasttd[-1]
                                st.caption(f"TD Sequential: no active setup · last "
                                           f"{_x['side']} 9 was {_x['bars_ago']} bars "
                                           f"ago ({_x['date']})")
                            else:
                                st.caption("TD Sequential: no active setup, none "
                                           "completed in the last 30 bars")

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

if tab_research is not None:
    with tab_research:
        import research_screener as RS
        st.caption("Upside rankings to corridor fair value (NTM EPS × historical "
                   "P/E), plus a regime + sentiment-adjusted version. Reads stored "
                   "snapshots — run ⟳ Update all to populate. Targets are model "
                   "outputs, not promises.")

        snaps = {s: SS.load_snapshot(s) for s in syms}
        snaps = {k: v for k, v in snaps.items() if v}
        macro = (SS.load_macro() or {}).get("macro")
        rk = RS.build_rankings(snaps, macro)

        if not rk["ok"]:
            # diagnose WHY it's empty rather than a generic message
            n_snaps = len(snaps)
            n_with_result = sum(1 for v in snaps.values() if v.get("result"))
            n_with_corr = sum(1 for v in snaps.values()
                              if ((v.get("result") or {}).get("zones") or {})
                              .get("corridor", {}).get("ok"))
            if n_snaps == 0:
                st.info("No snapshots yet. Hit **⟳ Update all** in the sidebar.")
            elif n_with_result == 0:
                st.warning("Your snapshots only have **trading** data (from the "
                           "Trading tab), not the **analyzer/valuation** data this "
                           "tab needs. Hit **⟳ Update all** in the sidebar — that "
                           "fetches the corridor fair values the rankings rank by.")
            elif n_with_corr == 0:
                st.warning(f"{n_with_result} ticker(s) have analyzer data, but none "
                           "have a corridor fair value yet — that needs a positive "
                           "forward EPS (analyst estimates or the TTM proxy). "
                           "Unprofitable names won't produce a target. Try adding a "
                           "profitable ticker, or re-run **⟳ Update all**.")
            else:
                st.info("No rankings to show.")
            st.caption(f"Debug: {n_snaps} snapshots · {n_with_result} with analyzer "
                       f"data · {n_with_corr} with a corridor target.")
            # show one ticker's corridor diagnosis so we can see WHY it failed
            with st.expander("🔍 Why no corridor? (per-ticker diagnosis)"):
                for s, v in list(snaps.items())[:8]:
                    dbg = (v.get("result") or {}).get("corridor_debug", "no debug "
                           "(re-run Update all after uploading the latest analyze.py)")
                    st.text(f"{s}: {dbg}")
        else:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Names ranked", rk["n_total"])
            k2.metric("Trading below fair", rk["n_below_fair"])
            k3.metric("Avg upside", f"{rk['avg_corridor_upside']}%")
            k4.metric("Regime tilt", f"{rk['regime']} ×{rk['risk_multiplier']}")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Upside to corridor fair value**")
                chart12 = {r["ticker"]: r["corridor_upside"]
                           for r in rk["by_corridor"][:12]}
                if chart12:
                    st.bar_chart(chart12, horizontal=True, height=300)
                else:
                    st.caption("No rankable names.")
            with c2:
                st.markdown("**Regime + sentiment-adjusted upside**")
                chartadj = {r["ticker"]: r["adjusted_upside"]
                            for r in rk["by_adjusted"][:12]}
                if chartadj:
                    st.bar_chart(chartadj, horizontal=True, height=300)
                else:
                    st.caption("No rankable names.")

            st.markdown("**Current price vs corridor fair value**")
            price_cmp = {}
            for r in rk["by_corridor"]:
                price_cmp[r["ticker"]] = {"Current": r["price"],
                                          "Fair value": r["fair_value"]}
            import pandas as _pd
            st.bar_chart(_pd.DataFrame(price_cmp).T, height=300)

            st.markdown("**Ranking table**")
            tbl = [{"#": r["rank_corridor"], "Ticker": fav_mark(r["ticker"]) + r["ticker"],
                    "Company": r["company"], "Price": r["price"],
                    "Fair value": r["fair_value"],
                    "Upside %": r["corridor_upside"],
                    "Adj upside %": r["adjusted_upside"],
                    "Sentiment": r["sentiment"], "Composite": r["composite"]}
                   for r in rk["by_corridor"]]
            st.dataframe(tbl, use_container_width=True, hide_index=True)

            if rk["missing"]:
                st.caption(f"No corridor target (e.g. negative/again no forward EPS): "
                           f"{', '.join(rk['missing'])}. These are excluded from "
                           "rankings rather than given a fabricated target.")
            st.caption(rk["note"])

if tab_sc is not None:
    with tab_sc:
        import scenario_engine as SE
        import prediction_tracker as PT
        st.caption("Possible near-term paths, simulated by resampling this stock's "
                   "own past returns (block bootstrap). The spread IS the message. "
                   "Below: the app's prediction ledger — it records its calls, "
                   "scores them later, and re-weights signals that have been right.")

        s1, s2, s3 = st.columns([2, 2, 2])
        sc_sym = s1.selectbox("Ticker", syms or ["SPY"], key="sc_sym")
        sc_h = s2.selectbox("Horizon", ["21 days (~1M)", "63 days (~3M)"], key="sc_h")
        sc_seed = s3.checkbox("New roll each run", value=True,
                              help="Off = reproducible paths")
        horizon = 21 if sc_h.startswith("21") else 63

        snap = SS.load_snapshot(sc_sym)
        series = (snap or {}).get("result", {}).get("series") or              (snap or {}).get("prices") or []
        if not series:
            st.info("No stored prices for this ticker — hit ⟳ Update all first.")
        else:
            closes = [p["c"] for p in series]
            sim = SE.simulate(closes, horizon=horizon,
                              seed=None if sc_seed else 42)
            if not sim.get("ok"):
                st.warning(sim.get("note"))
            else:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Spot", f"${sim['spot']}")
                c2.metric(f"P(above spot in {horizon}d)",
                          f"{sim['prob_above_spot']}%")
                c3.metric("Median path ends", f"${sim['median_end']}")
                c4.metric("P10 – P90 range",
                          f"${sim['p10_end']} – ${sim['p90_end']}")
                st.components.v1.html(
                    '<meta charset="utf-8">'
                    + SE.render_scenarios_html(series, sim), height=400)
                st.caption("Teal fan = 25–75% / 10–90% of simulated outcomes · "
                           "amber dash = median path · faint lines = sample paths "
                           "(green ended up, red ended down). "
                           + sim["note"])

        st.divider()
        st.subheader("Prediction ledger — the app grading itself")
        # cloud-persistence health: prove the ledger is actually saving to the Sheet
        _url = get_cloud_url()
        try:
            import cloud_sync as CS
            _conn = CS.test_connection(_url)
            if _conn["ok"]:
                st.success(f"☁ Ledger persistence: connected — survives reboots. "
                           f"({_conn['detail']})")
            else:
                st.error(f"☁ Ledger persistence BROKEN: {_conn['status']} — "
                         f"{_conn['detail']}. Until this is green, ledger data is "
                         f"LOST on every reboot. Most likely fix: paste the latest "
                         f"Code.gs into Apps Script and redeploy (it needs the "
                         f"'saveApp' namespace).")
        except Exception as e:
            st.error(f"☁ Ledger persistence check failed: {e}")
        ledger = PT.load_ledger(get_cloud_url())
        preds = ledger.get("predictions", [])
        n_scored = sum(1 for p in preds if p.get("scored"))
        overall = (ledger.get("stats") or {}).get("OVERALL", {})
        l1, l2, l3, l4 = st.columns(4)
        l1.metric("Calls recorded", len(preds))
        l2.metric("Scored (≥1wk old)", n_scored)
        hr = (overall.get("hits", 0) / overall["n"] * 100) if overall.get("n") else None
        l3.metric("Overall hit-rate", f"{hr:.0f}%" if hr is not None else "—")
        l4.metric("Signals re-weighted", len(PT.signal_weights(ledger)))

        rows = PT.stats_table(ledger)
        if rows:
            st.markdown("**Per-signal track record** — weights >1 mean a signal "
                        "earned a louder vote; <1 means it's been muted:")
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("No scored predictions yet. The loop is automatic: every "
                    "Update records the day's calls; once a call is a week old, "
                    "the next Update scores it and the weights adapt. Just keep "
                    "updating — the track record builds itself.")

        if preds:
            with st.expander(f"Raw ledger ({len(preds)} calls)"):
                show = [{"Date": p["date"], "Sym": p["symbol"],
                         "Dir": p.get("direction"), "Conv": p.get("probability"),
                         "Result %": p.get("realized_ret_pct", "pending"),
                         "Correct": ("✓" if p.get("correct") else "✗")
                                    if p.get("scored") and "correct" in p else "…"}
                        for p in reversed(preds[-100:])]
                st.dataframe(show, use_container_width=True, hide_index=True)

if tab_paper is not None:
    with tab_paper:
        import paper_portfolio as PP
        import research_screener as RS
        st.caption("Automated 12-ticker equal-weight paper portfolio, driven by the "
                   "Research adjusted-upside ranking. Rebalances weekly (with a "
                   "hysteresis buffer so it doesn't churn on noise). $100k start, "
                   "small modeled commission. Benchmarked vs SPY, dollar-matched. "
                   "Persists to your Sheet. Not advice — a measurement of whether "
                   "the top-ranked basket beats SPY.")

        cloud = get_cloud_url()
        try:
            port, load_status = PP.load_portfolio(cloud)
            load_err = None
        except Exception as e:
            port, load_status, load_err = None, "error", f"{type(e).__name__}: {e}"

        if load_status == "error":
            # Couldn't confirm a load. Show WHY, and offer to proceed — but default
            # to NOT auto-saving so we can't overwrite good cloud data on a hiccup.
            st.warning("⚠ Couldn't confirm the paper portfolio loaded from the "
                       "cloud. Not auto-saving (so saved data isn't overwritten). "
                       f"Reason: `{load_err or 'cloud read returned error'}`")
            # let the user still see/use the tab with whatever we have
            if port is None:
                port = PP.new_portfolio()
            safe_to_save = False
        else:
            if port is None:                  # genuinely empty (first run)
                port = PP.new_portfolio()
            safe_to_save = True

        # gather current prices from snapshots (+ SPY)
        prices = {}
        for s in list(set(syms + ["SPY"])):
            snap = SS.load_snapshot(s)
            px = ((snap or {}).get("trading") or {}).get("price") \
                or ((snap or {}).get("result") or {}).get("price")
            if px:
                prices[s] = px

        # rankings from the research screener (adjusted upside order)
        snaps = {s: SS.load_snapshot(s) for s in syms}
        snaps = {k: v for k, v in snaps.items() if v}
        macro = (SS.load_macro() or {}).get("macro")
        rk = RS.build_rankings(snaps, macro)
        ranked = [r["ticker"] for r in rk.get("by_adjusted", [])] if rk.get("ok") else []

        cc1, cc2, cc3 = st.columns([2, 2, 2])
        auto_paper = cc1.toggle("Conviction engine (auto)", value=True,
                                help="No calendar. Acts ONLY when the data "
                                     "changes: a holding's thesis breaks "
                                     "(trend/quality/sentiment/visibility), "
                                     "the macro regime shifts, or there's "
                                     "open capacity with qualified names. "
                                     "Otherwise it sits on hands — and logs "
                                     "that decision too.")
        if cc2.button("⚖ Evaluate now", help="Run the conviction engine once, "
                                             "immediately"):
            if not ranked:
                st.error("No rankings yet — run ⟳ Update all so Research can rank.")
            elif not prices:
                st.error("No prices — run ⟳ Update all.")
            else:
                res = PP.decide(port, ranked, snaps, macro, prices, force=True)
                PP.snapshot_value(port, prices)
                sv = PP.save_portfolio(port, cloud)
                if sv["cloud"]:
                    _n_act = sum(1 for d in res["decisions"] if d["kind"] != "HOLD")
                    st.success(f"Evaluated & saved — {_n_act} action(s), "
                               f"{len(res.get('roster', []))} holdings.")
                else:
                    st.warning(f"Evaluated but NOT saved: {sv['error']}")
                st.rerun()
        if cc3.button("↺ Reset portfolio", help="Wipe and start fresh at $100k"):
            port = PP.new_portfolio()
            PP.save_portfolio(port, cloud)
            st.rerun()

        # auto-rebalance on tab view if due (only if we safely loaded — never save
        # over good cloud data after a failed load). Cloud writes are GATED: only
        # when a rebalance acted or on the first view of the day — not on every
        # widget interaction (each save is an Apps Script POST).
        if auto_paper and ranked and prices and safe_to_save:
            _today = dt.date.today().isoformat()
            # evaluate at most once per day automatically (manual button anytime)
            if port.get("last_decide") != _today:
                res = PP.decide(port, ranked, snaps, macro, prices, force=False)
                PP.snapshot_value(port, prices)
                PP.save_portfolio(port, cloud)
                if res.get("acted"):
                    _acts = [d for d in res["decisions"] if d["kind"] != "HOLD"]
                    st.info("Conviction engine acted: " + " · ".join(
                        f"{d['kind']} {d['symbol']}" for d in _acts[:6]))
            else:
                _have_today = any(h["date"] == _today for h in port["history"])
                if not _have_today:
                    PP.snapshot_value(port, prices)
                    PP.save_portfolio(port, cloud)

        _dl = port.get("decisions", [])
        if _dl:
            with st.expander(f"🧭 Decision log ({len(_dl)} entries — includes "
                             f"the sits-on-hands)"):
                for d in reversed(_dl[-25:]):
                    _icon = {"BUY": "🟢", "EXIT": "🔴", "TRIM": "🟠",
                             "HOLD": "⚪"}.get(d["kind"], "·")
                    st.markdown(f"`{d['date']}` {_icon} **{d['kind']}** "
                                f"{d['symbol']} — {d['why']}")

        if not port["positions"]:
            st.info("Portfolio is empty. Click **⚖ Evaluate now** to deploy into "
                    "qualified names (needs ⟳ Update all run first so Research "
                    "can rank).")
        else:
            perf = PP.performance(port, prices)
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Value", f"${perf['value']:,.0f}",
                      f"{perf['total_return_pct']:+.2f}%")
            m2.metric("vs SPY", f"{perf['vs_spy']:+.2f}%" if perf['vs_spy'] is not None else "—",
                      help="Total return minus SPY's, dollar-matched")
            m3.metric("SPY return", f"{perf['spy_return_pct']:+.2f}%"
                      if perf['spy_return_pct'] is not None else "—")
            m4.metric("Positions", perf["positions"])
            m5.metric("Cash", f"{perf['cash_pct']}%")

            w1, w2, w3, w4 = st.columns(4)
            w1.metric("7D", f"{perf['ret_7d']:+.2f}%" if perf['ret_7d'] is not None else "—")
            w2.metric("30D", f"{perf['ret_30d']:+.2f}%" if perf['ret_30d'] is not None else "—")
            w3.metric("YTD", f"{perf['ret_ytd']:+.2f}%" if perf['ret_ytd'] is not None else "—")
            w4.metric("1Y", f"{perf['ret_1y']:+.2f}%" if perf['ret_1y'] is not None else "—")

            # performance chart vs SPY
            if len(port["history"]) >= 2:
                import pandas as _pd
                hist = port["history"]
                window = st.radio("Window", ["7D", "30D", "YTD", "1Y", "Max"],
                                  index=4, horizontal=True)
                days_map = {"7D": 7, "30D": 30, "YTD": (dt.date.today()
                            - dt.date(dt.date.today().year, 1, 1)).days,
                            "1Y": 365, "Max": 99999}
                cutoff = (dt.date.today()
                          - dt.timedelta(days=days_map[window])).isoformat()
                rows = [h for h in hist if h["date"] >= cutoff] or hist
                base_v = rows[0]["value"] or PP.START_CASH
                base_s = rows[0].get("spy_value") or base_v
                chart = {}
                for h in rows:
                    chart[h["date"]] = {
                        "Portfolio %": round((h["value"] / base_v - 1) * 100, 2),
                        "SPY %": round((h["spy_value"] / base_s - 1) * 100, 2)
                                      if h.get("spy_value") else None}
                df_c = _pd.DataFrame(chart).T
                st.line_chart(df_c, height=320)
                st.caption("Cumulative return, both rebased to 0 at the window start "
                           "— a fair dollar-matched comparison.")
            else:
                st.caption("Performance chart appears once there are 2+ daily "
                           "snapshots. Keep the app updating.")

            # holdings table with colored returns
            st.markdown("**Holdings**")
            hold_rows = []
            for sym, pos in sorted(port["positions"].items()):
                px = prices.get(sym, pos["avg_cost"])
                mv = pos["shares"] * px
                ret = (px / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] else 0
                hold_rows.append({"Ticker": sym, "Shares": round(pos["shares"], 2),
                                  "Avg cost": round(pos["avg_cost"], 2),
                                  "Price": round(px, 2),
                                  "Value": round(mv, 0),
                                  "Return %": round(ret, 2),
                                  "Weight %": round(mv / perf["value"] * 100, 1)})
            try:
                import pandas as _pd
                _df = _pd.DataFrame(hold_rows)
                def _col(v):
                    return ("color:#3fb37f" if v > 0 else
                            "color:#d6504f" if v < 0 else "color:#8899aa")
                sty = _df.style.map(_col, subset=["Return %"]) \
                              .format({"Return %": "{:+.2f}", "Avg cost": "{:.2f}",
                                       "Price": "{:.2f}", "Value": "{:,.0f}",
                                       "Weight %": "{:.1f}"})
                st.dataframe(sty, use_container_width=True, hide_index=True)
            except Exception:
                st.dataframe(hold_rows, use_container_width=True, hide_index=True)

            # activity feed
            st.markdown("**Activity** (most recent first)")
            act = [{"Date": a["date"], "Action": a["action"], "Ticker": a["symbol"],
                    "Shares": a.get("shares"), "Price": a.get("price"),
                    "Note": a.get("note")} for a in reversed(port["activity"][-60:])]
            st.dataframe(act, use_container_width=True, hide_index=True)
            if port.get("last_rebalance"):
                st.caption(f"Last rebalance: {port['last_rebalance']} · next due after "
                           f"{port['settings']['rebalance_days']} days. Hold "
                           f"{PP.N_HOLD} equal-weight; a name is dropped only if it "
                           f"falls past rank {PP.DROP_RANK} (hysteresis).")

if tab_report is not None:
    with tab_report:
        import report_engine as RE
        st.caption("Generate a full deep-dive report on any analyzed ticker, or scan "
                   "your watchlist for long/short setups. Reports are research "
                   "write-ups, not trade calls — signals are mostly lagging and "
                   "haven't reliably beaten buy-and-hold.")

        macro = (SS.load_macro() or {}).get("macro")
        mode = st.radio("Mode", ["📄 Deep-dive report (one ticker)",
                                 "🔍 Scan for trading ideas (watchlist)"],
                        horizontal=True)

        if mode.startswith("📄"):
            # any ticker that has a snapshot (watchlist OR searched)
            avail = sorted(set(syms + ([st.session_state.get("last_lookup")]
                                       if st.session_state.get("last_lookup") else [])))
            # also include any symbol that has a stored snapshot
            pick = st.selectbox("Ticker (must be analyzed first — use 🔎 Search "
                                "above for non-watchlist names)", avail)
            snap = SS.load_snapshot(pick) if pick else None
            if not snap or not (snap.get("result") or snap.get("trading")):
                st.info(f"No analysis stored for {pick}. Run ⟳ Update all, or use "
                        "🔎 Search at the top to analyze it on demand.")
            else:
                d = RE.deep_dive(snap, macro)
                md = RE.deep_dive_markdown(d)
                html = RE.deep_dive_html(d)
                dl1, dl2 = st.columns(2)
                dl1.download_button("⬇ Download HTML", data=html,
                                    file_name=f"{d['symbol']}_report.html",
                                    mime="text/html", use_container_width=True)
                dl2.download_button("⬇ Download Markdown", data=md,
                                    file_name=f"{d['symbol']}_report.md",
                                    mime="text/markdown", use_container_width=True)
                st.markdown("---")
                st.markdown(md)

        else:
            f1, f2, f3 = st.columns(3)
            direction = f1.selectbox("Direction", ["both", "long", "short"])
            timeframe = f2.selectbox("Timeframe", ["swing", "day"])
            min_sc = f3.slider("Min setup score", 0, 100, 55,
                               help="50 = neutral. The score favors leading "
                                    "evidence (RS, valuation gap, bootstrap odds, "
                                    "sentiment, regime), entry positioning and the "
                                    "ledger's measured hit-rates. Trend is only "
                                    "the gate.")
            g1, g2 = st.columns(2)
            prof_only = g1.checkbox("Only higher-quality names (composite ≥45)")
            min_up = g2.number_input("Min corridor upside % (longs)", value=0,
                                     step=5) or None

            import prediction_tracker as _PT
            _lstats = (_PT.load_ledger(get_cloud_url()) or {}).get("stats") or {}
            snaps = {s: SS.load_snapshot(s) for s in syms}
            snaps = {k: v for k, v in snaps.items() if v}
            scan = RE.scan_ideas(snaps, macro, direction=direction,
                                 timeframe=timeframe, min_score=min_sc,
                                 profitable_only=prof_only, min_upside=min_up,
                                 ledger_stats=_lstats)

            smd = RE.scan_markdown(scan)
            st.download_button("⬇ Download scan (Markdown)", data=smd,
                               file_name=f"trading_ideas_{timeframe}.md",
                               mime="text/markdown")

            st.info(scan["note"])
            if "longs" in scan:
                st.markdown(f"### 🟢 LONG candidates ({len(scan['longs'])})")
                if scan["longs"]:
                    st.dataframe([{"Ticker": fav_mark(r["symbol"]) + r["symbol"], "Setup": r["setup_score"],
                                   "Price": r.get("price"),
                                   "P(up 21d)": r.get("p_up"),
                                   "Upside %": r.get("upside"),
                                   "RS vs SPY": r.get("rel_strength"),
                                   "Conv %": r["conviction"],
                                   "Composite": r.get("composite")}
                                  for r in scan["longs"]],
                                 use_container_width=True, hide_index=True)
                    for r in scan["longs"][:6]:
                        with st.expander(f"{fav_mark(r['symbol'])}{r['symbol']} — setup {r['setup_score']} (why)"):
                            for e in r["evidence"]:
                                st.markdown(f"`{e['pts']:+6.1f}` **{e['factor']}** "
                                            f"— {e['note']} · *{e['tag']}*")
                else:
                    st.caption("None currently match these filters.")
            if "shorts" in scan:
                st.markdown(f"### 🔴 SHORT candidates ({len(scan['shorts'])})")
                if scan["shorts"]:
                    st.dataframe([{"Ticker": fav_mark(r["symbol"]) + r["symbol"], "Setup": r["setup_score"],
                                   "Price": r.get("price"),
                                   "P(up 21d)": r.get("p_up"),
                                   "Upside %": r.get("upside"),
                                   "RS vs SPY": r.get("rel_strength"),
                                   "Conv %": r["conviction"]}
                                  for r in scan["shorts"]],
                                 use_container_width=True, hide_index=True)
                    for r in scan["shorts"][:6]:
                        with st.expander(f"{fav_mark(r['symbol'])}{r['symbol']} — setup {r['setup_score']} (why)"):
                            for e in r["evidence"]:
                                st.markdown(f"`{e['pts']:+6.1f}` **{e['factor']}** "
                                            f"— {e['note']} · *{e['tag']}*")
                else:
                    st.caption("None currently match these filters.")

if tab_macro is not None:
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

if tab_bt is not None:
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

if tab_learn is not None:
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

if tab2 is not None:
    with tab2:
        st.caption("Original corridor chart. Loads on demand to keep the app fast.")
        if st.checkbox("Load corridor chart"):
            html = Path(__file__).parent.joinpath("corridor.html").read_text(encoding="utf-8")
            inject = (f"window.__FMP_KEY__={json.dumps(key)};"
                      f"window.__RUGPULL_WATCHLIST__={json.dumps(st.session_state.watchlist)};")
            html = html.replace("/*__RUGPULL_INJECT__*/", inject)
            st.components.v1.html(html, height=1000, scrolling=True)


if tab_map is not None:
    with tab_map:
        import quadrant_map as QM
        st.caption("Every ticker on one map: valuation (Y) × directional "
                   "outlook (X). Bottom-right = inexpensive with upside (the "
                   "sweet spot) · top-left = expensive with downside. Hover a "
                   "dot for detail. Quadrants are a reading aid, not a rating.")

        m1, m2, m3 = st.columns([2, 2, 2])
        tf_pick = m1.radio("Timeframe", ["Short (~2 wks)", "Medium (~1–3 mo)",
                                         "Long (~6–12 mo)"],
                           index=1, horizontal=False)
        tf = ("short" if tf_pick.startswith("Short")
              else "medium" if tf_pick.startswith("Medium") else "long")
        min_comp = m2.slider("Min composite quality", 0, 80, 0, step=5)
        m3.markdown("**Legend**")
        m3.caption("🟢 LONG bias · 🔴 SHORT bias · ⚪ WAIT — color is the "
                   "current swing signal, position is valuation × outlook")

        snaps = {s: SS.load_snapshot(s) for s in syms}
        snaps = {k: v for k, v in snaps.items() if v}
        import inspect as _insp
        if "favorites" in _insp.signature(QM.build_points).parameters:
            qd = QM.build_points(snaps, timeframe=tf, min_composite=min_comp,
                                 favorites=set(st.session_state.favorites))
        else:
            st.error("⚠ STALE FILE: the deployed quadrant_map.py is an older "
                     "version without favorites support. Re-upload the latest "
                     "quadrant_map.py, reboot the app, hard-refresh. "
                     "Rendering without favorite glow for now.")
            qd = QM.build_points(snaps, timeframe=tf, min_composite=min_comp)
        st.components.v1.html(
            '<meta charset="utf-8">' + QM.render_quadrant_html(qd), height=500)

        _x_expl = {"short": "X = weighted trend votes (learned weights, TD-"
                            "nudged) — mostly LAGGING agreement, days–2 weeks.",
                   "medium": "X = bootstrap P(up in 21d) from each ticker's own "
                             "return history — distribution odds, not a forecast.",
                   "long": "X = 6-month momentum + position vs SMA200 — the "
                           "persistence factor; it continues until it doesn't."}
        st.caption(_x_expl[tf] + " · Y = price vs corridor fair value on all "
                   "timeframes. " + qd["note"])
        _n_arrows = sum(1 for p in qd["points"] if p.get("rev"))
        if _n_arrows:
            st.caption(f"▲▼ Reversal arrows active on {_n_arrows} of "
                       f"{len(qd['points'])} tickers (TD exhaustion / >1.5σ "
                       f"stretch within the last 5 bars).")
        else:
            st.caption("▲▼ No reversal arrows right now — none of your tickers "
                       "currently show a fresh TD Setup 9, a late Countdown, or "
                       ">1.5σ stretch. That's the normal state most days; these "
                       "flags are rare by design. (TD data needs a recent "
                       "⟳ Update trading data.)")
        if qd["excluded"]:
            st.caption(f"Not plottable (no corridor fair value / insufficient "
                       f"history): {', '.join(qd['excluded'])} — listed rather "
                       f"than faked.")


if tab_cvd is not None:
    with tab_cvd:
        try:
            import cvd_analysis as CVA
            import cvd_chart as CVC
        except ImportError as _ie:
            CVA = None
            st.error(f"CVD module missing from the repo ({_ie}) — upload "
                     "cvd_analysis.py and cvd_chart.py. The rest of the app "
                     "is unaffected.")
        if CVA is not None:
            st.caption("Cumulative Volume Delta — who has been in control, "
                       "bar by bar. **Selection-only:** analyze one ticker at "
                       "a time, on demand (intraday fetches are heavy). "
                       "Approximate CVD from OHLCV bar location, not true "
                       "bid/ask tape — treat levels as context, divergences "
                       "as questions, never as signals.")

            _c1, _c2, _c3, _c4 = st.columns([2.4, 1.6, 1.4, 1.6])
            _cvd_univ = sorted(set(syms_all)
                               | {s for s in [st.session_state.get("last_lookup")]
                                  if s})
            _csym = _c1.selectbox(
                "Ticker", _cvd_univ,
                format_func=lambda s: f"{fav_mark(s)}{s}", key="cvd_sym")
            _civ = _c2.selectbox("Bar size", ["5min", "15min", "30min",
                                              "1hour", "1min"], key="cvd_iv",
                                 help="1min needs the top FMP plan; the fetch "
                                      "auto-falls back to coarser bars if a "
                                      "size is gated.")
            _cdays = _c3.slider("Days", 2, 10, 5, key="cvd_days")
            _cmeth = _c4.selectbox("Delta method", ["location", "direction"],
                                   key="cvd_meth",
                                   help="location: close's position in the "
                                        "bar range scales the volume "
                                        "(richer). direction: whole bar "
                                        "volume signed by up/down close "
                                        "(cruder, common on charting sites).")

            if st.button("⟳ Analyze flow", type="primary",
                         key="cvd_go"):
                with st.spinner(f"Fetching {_csym} intraday bars…"):
                    try:
                        _out = CVA.fetch_cvd(client, _csym, interval=_civ,
                                             days_back=_cdays, method=_cmeth)
                        if _out.get("ok"):
                            _sc = getattr(SS, "save_cvd", None)
                            if _sc:
                                _sc(_csym, _out)
                            else:
                                # legacy snapshot_store deployed - write the
                                # cvd slice directly, preserving the rest
                                import json as _json
                                _snapf = SS.load_snapshot(_csym) or {
                                    "symbol": _csym.upper()}
                                _snapf["cvd"] = _out
                                with open(SS._path(_csym), "w") as _fh:
                                    _json.dump(_snapf, _fh)
                            st.rerun()
                        else:
                            st.error(f"{_csym}: {_out.get('note', 'failed')} "
                                     f"(tried {', '.join(_out.get('tried', []))})")
                    except Exception as _e:
                        st.error(f"Fetch failed: {_e}")

            _snap_c = SS.load_snapshot(_csym) or {}
            _cvd = _snap_c.get("cvd")
            if not _cvd or not _cvd.get("ok"):
                st.info(f"No stored flow analysis for {_csym} yet — hit "
                        f"**⟳ Analyze flow** above. It persists to "
                        f"the snapshot, so it's here until you refresh it.")
            else:
                _age_s = ""
                try:
                    _age_h = (dt.datetime.now(dt.timezone.utc).timestamp()
                              - _cvd.get("fetched_at", 0)) / 3600
                    _age_s = (f" · analyzed {_age_h:.1f}h ago"
                              if _age_h < 48 else
                              f" · analyzed {_age_h/24:.0f}d ago — stale, "
                              f"consider refreshing")
                except Exception:
                    pass
                st.markdown(f"**{fav_mark(_csym)}{_csym}** — "
                            f"{_cvd['bars']} × {_cvd.get('interval_used')} bars"
                            + (f" (fell back from {_cvd.get('interval_requested')})"
                               if _cvd.get("fallback") else "")
                            + f" · {_cvd.get('method')} delta{_age_s}")

                st.components.v1.html(
                    "<meta charset='utf-8'>" + CVC.render_cvd_html(_cvd),
                    height=560)

                for _rd in _cvd.get("synthesis", []):
                    st.markdown(f"- {_rd}")

                _dv = _cvd.get("divergence") or {}
                if _dv.get("kind") not in (None, "none"):
                    (st.warning if _dv["kind"] == "bearish" else st.success)(
                        f"{'▼' if _dv['kind'] == 'bearish' else '▲'} "
                        f"{_dv['kind'].title()} divergence: {_dv['note']}")

                _ss = _cvd.get("sessions") or []
                if _ss:
                    st.markdown("**Session ledger** — did price agree with "
                                "the flow each day?")
                    st.dataframe(
                        [{"Session": s["date"], "Bars": s["bars"],
                          "Net delta": f"{s['net_delta']:+,}",
                          "Session CVD close": f"{s['cvd_close']:+,}",
                          "Price": (f"{s['price_chg_pct']:+.2f}%"
                                    if s.get("price_chg_pct") is not None else "—"),
                          "Flow vs price": "✓ agree" if s["agree"] else "✗ DIVERGED"}
                         for s in _ss],
                        use_container_width=True, hide_index=True)
                st.caption(_cvd.get("note", ""))

if tab_seas is not None:
    with tab_seas:
        try:
            import seasonality as SEA
        except ImportError:
            SEA = None
            st.error("seasonality.py is missing from the repo — upload it to "
                     "enable this tab. The rest of the app is unaffected.")
        if SEA is not None:
            st.caption("Monthly-return seasonality, five complete years + "
                       "current (month-to-date), with the 5-year average per "
                       "month. **Honest framing:** this is price seasonality "
                       "of the index ETFs (n=5 per cell — a weak, noisy "
                       "tendency), context for timing and sizing, never a "
                       "signal on its own.")

            @st.cache_data(ttl=21600, show_spinner="Loading index history…")
            def _seas_cached(sym: str, day_key: str):
                rows = SEA.fetch_daily(client, sym, years=7)
                return SEA.monthly_grid(rows, n_prior_years=5)

            _dk = dt.date.today().isoformat()
            for _sym, _label in (
                    ("SPY", "S&P 500 (SPY) — Monthly Return Seasonality"),
                    ("QQQ", "Nasdaq-100 (QQQ) — Monthly Return Seasonality")):
                try:
                    _g = _seas_cached(_sym, _dk)
                    st.components.v1.html(
                        "<meta charset='utf-8'>"
                        + SEA.render_heatmap_html(_label, _g), height=360)
                except Exception as _e:
                    st.warning(f"{_sym}: couldn't load seasonality — {_e}")
            st.caption("Reading the current month: check the boxed column's "
                       "5-yr average and the spread of the five cells beneath "
                       "it — a consistent sign across years means more than "
                       "the average; five mixed signs means the average is "
                       "noise.")

if tab_set is not None:
    with tab_set:
        st.subheader("⚙ Settings")
        st.caption("Layout and ticker controls. Saved to your Sheet — they "
                   "follow you across devices.")

        st.markdown("**Visible tabs** — hide what you don't use (nothing is "
                    "deleted; ⚙ Settings always stays).")
        _hideable = [n for n, _ in ALL_TABS if n != "⚙ Settings"]
        _cur_tabs = [n for n in (st.session_state.app_settings.get("visible_tabs")
                                 or _hideable) if n != "⚙ Settings"]
        _sel_tabs = st.multiselect("Tabs", _hideable, default=_cur_tabs,
                                   label_visibility="collapsed")

        st.markdown("**Active tickers** — deselect to ⏸ pause: the ticker "
                    "stays in your watchlist with its history, but is skipped "
                    "by updates, rankings, scans, the ledger, and the paper "
                    "portfolio until re-enabled.")
        _cur_active = [s for s in syms_all if s not in _inactive]
        _sel_active = st.multiselect("Tickers", syms_all, default=_cur_active,
                                     label_visibility="collapsed")

        c_a, c_b = st.columns([1, 3])
        if c_a.button("💾 Apply & save", use_container_width=True,
                      type="primary"):
            st.session_state.app_settings["visible_tabs"] = \
                (_sel_tabs + ["⚙ Settings"]) if _sel_tabs else None
            st.session_state.app_settings["inactive"] = [
                s for s in syms_all if s not in _sel_active]
            save_settings()
            st.rerun()
        _n_hidden = len(_hideable) - len(_sel_tabs)
        _n_paused = len(syms_all) - len(_sel_active)
        c_b.caption(f"{_n_hidden} tab(s) will be hidden · {_n_paused} ticker(s) "
                    f"will be paused. Changes take effect on Apply.")

        _paused_now = sorted(_inactive)
        if _paused_now:
            st.caption("Currently paused: " + ", ".join(_paused_now))
