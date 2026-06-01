"""
streamlit_app.py — Rugpull_AI web UI.

Two tabs over one shared watchlist (tickers.json):
  • Analyzer       — composite scoring from the Python engine
  • Corridor Chart — the embedded valuation chart, seeded from the same list

The FMP key comes from st.secrets (set in the deploy dialog) or the FMP_API_KEY
env var. It is never committed to the repo.
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

st.set_page_config(page_title="Rugpull_AI", page_icon="📈", layout="wide")


def get_key() -> str | None:
    try:
        if "FMP_API_KEY" in st.secrets:
            return st.secrets["FMP_API_KEY"]
    except Exception:
        pass
    return os.environ.get("FMP_API_KEY")


@st.cache_resource
def get_client(key: str) -> FMPClient:
    return FMPClient(api_key=key)


@st.cache_data(ttl=3600, show_spinner=False)
def run_one(_client: FMPClient, sym: str) -> dict:
    return A.analyze(_client, sym, S.FMPNewsSentiment(_client))


# ---- session watchlist (loaded once from tickers.json) --------------------
if "watchlist" not in st.session_state:
    st.session_state.watchlist = W.load_watchlist()

st.title("📈 Rugpull_AI")
st.caption("Transparent decision-support scoring + valuation corridor. "
           "Not financial advice, not a price oracle.")

key = get_key()
if not key:
    st.error("No FMP API key found. On Streamlit Cloud: **Settings → Secrets** → "
             "`FMP_API_KEY = \"your_key\"`. Locally: set the `FMP_API_KEY` env var.")
    st.stop()

client = get_client(key)

# ---- sidebar: watchlist manager + weights + macro -------------------------
with st.sidebar:
    st.header("Watchlist")
    st.caption("Shared by both tabs. Edits persist locally; on Streamlit Cloud "
               "they last for the session — download and commit tickers.json to "
               "make them permanent.")

    new = st.text_input("Add ticker", placeholder="e.g. PLTR").upper().strip()
    if st.button("Add", use_container_width=True) and new:
        if new not in [t["symbol"] for t in st.session_state.watchlist]:
            st.session_state.watchlist.append({"symbol": new, "name": new})

    for i, t in enumerate(list(st.session_state.watchlist)):
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{t['symbol']}** — {t['name']}")
        if c2.button("✕", key=f"del_{i}"):
            st.session_state.watchlist.pop(i)
            st.rerun()

    ca, cb = st.columns(2)
    if ca.button("Save", use_container_width=True):
        W.save_watchlist(st.session_state.watchlist)
        st.success("Saved to tickers.json")
    cb.download_button(
        "Download", use_container_width=True,
        data=json.dumps({"watchlist": st.session_state.watchlist}, indent=2),
        file_name="tickers.json", mime="application/json",
    )

    st.divider()
    st.subheader("Weights")
    w = {}
    for leg, default in A.WEIGHTS.items():
        w[leg] = st.slider(leg, 0.0, 1.0, float(default), 0.05)
    tot = sum(w.values()) or 1.0
    A.WEIGHTS = {k: v / tot for k, v in w.items()}

    st.divider()
    macro = S.macro_regime(client)
    st.subheader("Macro regime")
    st.metric("10y–2y spread", macro.get("spread_10y_2y", "n/a"))
    st.write(f"**{macro.get('regime','unknown')}** · tilt ×{macro.get('tilt', 1.0)}")

syms = [t["symbol"] for t in st.session_state.watchlist]

# ---- tabs -----------------------------------------------------------------
tab_analyze, tab_chart = st.tabs(["🧮 Analyzer", "📉 Corridor Chart"])

with tab_analyze:
    if st.button("Analyze watchlist", type="primary"):
        results, prog = [], st.progress(0.0)
        for i, sym in enumerate(syms):
            try:
                results.append(run_one(client, sym))
            except Exception as e:
                st.warning(f"{sym}: {e}")
            prog.progress((i + 1) / max(len(syms), 1))
        prog.empty()

        if results:
            results.sort(key=lambda r: r["composite_score"], reverse=True)
            st.subheader("Ranking")
            st.dataframe(
                [{"Symbol": r["symbol"], "Company": r["company"], "Price": r["price"],
                  "Composite": r["composite_score"],
                  "Verdict": A._verdict(r["composite_score"])} for r in results],
                use_container_width=True, hide_index=True,
            )
            for r in results:
                with st.expander(f"{r['symbol']} — {r['company']} · "
                                 f"{r['composite_score']}/100"):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Composite", f"{r['composite_score']}/100")
                    c2.metric("Price", r["price"])
                    c3.metric("Verdict", A._verdict(r["composite_score"]))
                    st.bar_chart(r["legs"])
                    v, d, m = r["value"], r["dcf"], r["momentum"]
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        st.write("**Valuation**")
                        st.write(f"P/E now {v.get('pe_now')} vs hist median "
                                 f"{v.get('pe_median_hist')}")
                        if d.get("intrinsic_value") is not None:
                            st.write(f"**DCF:** {d['intrinsic_value']} "
                                     f"(margin {d.get('margin_of_safety')})")
                            st.caption(f"assumes {d['assumptions']}")
                    with cc2:
                        st.write("**Momentum / structure**")
                        st.write(f"{'Above' if m.get('above_200dma') else 'Below'} "
                                 f"200dma · {'golden cross' if m.get('golden_cross') else 'no golden cross'}")
                        s = r["sentiment"]
                        st.write(f"Sentiment {s['score']}/100 ({s['n']} items)")
                    ee = r["entry_exit"]
                    if ee:
                        st.write("**Entry / exit (rule-based)**")
                        e1, e2, e3, e4 = st.columns(4)
                        e1.metric("Support", ee.get("support"))
                        e2.metric("Resistance", ee.get("resistance"))
                        e3.metric("VWAP band",
                                  f"{ee.get('vwap_lower_band')}–{ee.get('vwap_upper_band')}")
                        e4.metric("Stop 2·ATR", ee.get("suggested_stop_2atr"))
    else:
        st.info(f"Watchlist: {', '.join(syms) if syms else '(empty)'} — "
                "click **Analyze watchlist**.")

with tab_chart:
    st.caption("Same FMP data source. Dropdown is seeded from your watchlist. "
               "Use the ⚙/＋ controls in the chart to pull FMP data per ticker.")
    html = Path(__file__).parent.joinpath("corridor.html").read_text(encoding="utf-8")
    inject = (f"window.__FMP_KEY__={json.dumps(key)};"
              f"window.__RUGPULL_WATCHLIST__={json.dumps(st.session_state.watchlist)};")
    html = html.replace("/*__RUGPULL_INJECT__*/", inject)
    st.components.v1.html(html, height=1000, scrolling=True)
