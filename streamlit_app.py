"""
streamlit_app.py — web front-end for the stock_analyzer engine.

Deploy free on Streamlit Community Cloud straight from your GitHub repo. The
FMP key is read from st.secrets (set in the deploy dialog) or the FMP_API_KEY
env var — it is NEVER committed to the repo.

Run locally:
    streamlit run streamlit_app.py
"""

from __future__ import annotations
import os
import streamlit as st

from fmp_client import FMPClient, FMPError
import analyze as A
import signals as S

st.set_page_config(page_title="Stock Analyzer", page_icon="📈", layout="wide")


def get_key() -> str | None:
    # st.secrets first (Community Cloud), then env var (local/Codespaces)
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
    sp = S.FMPNewsSentiment(_client)
    return A.analyze(_client, sym, sp)


# ---------------------------------------------------------------- UI -------
st.title("📈 Stock Analyzer")
st.caption("Transparent decision-support scoring — not financial advice, and "
           "not a price oracle. Every leg is shown so you can disagree with it.")

key = get_key()
if not key:
    st.error("No FMP API key found. On Streamlit Cloud, add it under "
             "**Settings → Secrets** as `FMP_API_KEY = \"your_key\"`. "
             "Locally, set the `FMP_API_KEY` env var.")
    st.stop()

client = get_client(key)

with st.sidebar:
    st.header("Weights")
    st.caption("Tune the composite blend. They sum-normalize automatically.")
    w = {}
    for leg, default in A.WEIGHTS.items():
        w[leg] = st.slider(leg, 0.0, 1.0, float(default), 0.05)
    tot = sum(w.values()) or 1.0
    A.WEIGHTS = {k: v / tot for k, v in w.items()}  # live override

    st.divider()
    macro = S.macro_regime(client)
    st.subheader("Macro regime")
    st.metric("10y–2y spread", macro.get("spread_10y_2y", "n/a"))
    st.write(f"**{macro.get('regime','unknown')}** · strictness tilt "
             f"×{macro.get('tilt', 1.0)}")

tickers = st.text_input("Tickers (comma or space separated)",
                        value="NVDA, AMD, INTC, TSLA")
go = st.button("Analyze", type="primary")

if go:
    syms = [s.strip().upper() for s in tickers.replace(",", " ").split() if s.strip()]
    results = []
    prog = st.progress(0.0)
    for i, sym in enumerate(syms):
        try:
            results.append(run_one(client, sym))
        except FMPError as e:
            st.warning(f"{sym}: {e}")
        except Exception as e:
            st.warning(f"{sym}: {e}")
        prog.progress((i + 1) / max(len(syms), 1))
    prog.empty()

    if results:
        results.sort(key=lambda r: r["composite_score"], reverse=True)

        st.subheader("Ranking")
        st.dataframe(
            [{"Symbol": r["symbol"], "Company": r["company"],
              "Price": r["price"], "Composite": r["composite_score"],
              "Verdict": A._verdict(r["composite_score"])} for r in results],
            use_container_width=True, hide_index=True,
        )

        for r in results:
            with st.expander(f"{r['symbol']} — {r['company']}  ·  "
                             f"{r['composite_score']}/100", expanded=False):
                c1, c2, c3 = st.columns(3)
                c1.metric("Composite", f"{r['composite_score']}/100")
                c2.metric("Price", r["price"])
                c3.metric("Verdict", A._verdict(r["composite_score"]))

                st.write("**Component scores** (each 0–100)")
                st.bar_chart(r["legs"])

                v, d, m = r["value"], r["dcf"], r["momentum"]
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.write("**Valuation**")
                    st.write(f"P/E now {v.get('pe_now')} vs hist median "
                             f"{v.get('pe_median_hist')}")
                    st.write(f"Discount vs history: {v.get('discount_vs_history')}")
                    if d.get("intrinsic_value") is not None:
                        st.write(f"**DCF intrinsic:** {d['intrinsic_value']} "
                                 f"(margin of safety {d.get('margin_of_safety')})")
                        st.caption(f"assumes {d['assumptions']}")
                with cc2:
                    st.write("**Momentum / structure**")
                    st.write(f"{'Above' if m.get('above_200dma') else 'Below'} 200dma · "
                             f"{'golden cross' if m.get('golden_cross') else 'no golden cross'}")
                    st.write(f"3-month return: {m.get('ret_3m')}")
                    s = r["sentiment"]
                    st.write(f"Sentiment {s['score']}/100 ({s['n']} items, {s['source']})")

                ee = r["entry_exit"]
                if ee:
                    st.write("**Entry / exit levels** (rule-based, not predictions)")
                    e1, e2, e3, e4 = st.columns(4)
                    e1.metric("Support", ee.get("support"))
                    e2.metric("Resistance", ee.get("resistance"))
                    e3.metric("VWAP band", f"{ee.get('vwap_lower_band')}–{ee.get('vwap_upper_band')}")
                    e4.metric("Stop (2·ATR)", ee.get("suggested_stop_2atr"))

                f = r["naive_forecast"]
                if f.get("central_projection"):
                    st.caption(f"21-day cone: {f['ci95_low']} … "
                               f"{f['central_projection']} … {f['ci95_high']}. "
                               f"{f['warning']}")
