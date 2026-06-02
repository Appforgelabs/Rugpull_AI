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
tab1, tab2 = st.tabs(["Analyzer", "Corridor Chart"])

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
            with st.expander(f"{sym} — {r.get('company', sym)}  ·  "
                             f"{r.get('composite_score')}/100  ·  "
                             f"{A._verdict(r.get('composite_score', 0))}"):
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
                svg = ZC.render_zone_svg(r.get("series", []), r.get("zones", {}))
                st.markdown(svg, unsafe_allow_html=True)

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
                        st.rerun()
                    except Exception as ex:
                        st.warning(f"{sym}: {ex}")

with tab2:
    st.caption("Original corridor chart. Loads on demand to keep the app fast.")
    if st.checkbox("Load corridor chart"):
        html = Path(__file__).parent.joinpath("corridor.html").read_text(encoding="utf-8")
        inject = (f"window.__FMP_KEY__={json.dumps(key)};"
                  f"window.__RUGPULL_WATCHLIST__={json.dumps(st.session_state.watchlist)};")
        html = html.replace("/*__RUGPULL_INJECT__*/", inject)
        st.components.v1.html(html, height=1000, scrolling=True)
