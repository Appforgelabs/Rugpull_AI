"""
analyze.py — orchestrator. Pulls data, runs every module, blends into a
transparent composite, and prints a report that always shows its work.

Usage:
    export FMP_API_KEY="your_key"
    python analyze.py NVDA AMD INTC TSLA

The composite is a weighted blend you can edit (WEIGHTS below). It is a
decision-support score, NOT a buy/sell command. The component breakdown is
printed so you can disagree with any single leg.
"""

from __future__ import annotations
import sys
import json

import numpy as np

from fmp_client import FMPClient, FMPError
import fundamentals as F
import technicals as T
import signals as S
import prediction_zones as PZ
import trade_signals as TS
import macro_engine as ME


def _clean(o):
    """Recursively cast numpy scalars to native types so results are JSON-safe."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o

# Edit these. They encode YOUR philosophy. Defaults lean fundamentals-first
# because that's the part with the most durable signal.
WEIGHTS = {
    "quality":   0.30,
    "value":     0.25,
    "momentum":  0.25,
    "sentiment": 0.10,
    "dcf":       0.10,
}


def build_trading(client: FMPClient, sym: str, intraday_interval="5min",
                  spy_closes: list | None = None) -> dict:
    """Trading-tab data for one ticker: ~2y daily + recent intraday, run through
    the TA engine. Separate from analyze() so the Analyzer tab stays fast.
    If spy_closes is passed, also computes relative strength vs the S&P."""
    import pandas as pd

    hist = client.history(sym)
    daily = TS.to_ohlcv(hist)

    intraday_df = None
    session_df = None
    try:
        bars = client.intraday(sym, interval=intraday_interval, days_back=7)
        intraday_df = TS.to_ohlcv(bars)
        if not intraday_df.empty:
            last_day = intraday_df["date"].dt.date.max()
            session_df = intraday_df[intraday_df["date"].dt.date == last_day]
    except Exception:
        intraday_df = None

    row = TS.build_trading_row(daily, intraday_df, session_df)
    row["symbol"] = sym.upper()
    row["intraday_available"] = bool(intraday_df is not None and not intraday_df.empty)

    # relative strength vs SPY (a leading-ish single-name tell)
    if spy_closes and not daily.empty:
        row["rel_strength"] = ME.relative_strength(daily["close"].tolist(), spy_closes)

    return _clean(row)


def analyze(client: FMPClient, sym: str, sentiment_provider) -> dict:
    profile = client.profile(sym)
    quote   = client.quote(sym)
    price   = float(quote.get("price") or profile.get("price") or 0) or None
    shares  = float(profile.get("sharesOutstanding") or quote.get("sharesOutstanding")
                    or 0) or None

    income   = client.income(sym)
    balance  = client.balance(sym)
    cashflow = client.cashflow(sym)
    rttm     = client.ratios_ttm(sym)
    rhist    = client.ratios(sym)
    hist     = client.history(sym)

    df = T.to_frame(hist)

    q = F.quality_score(income, balance, cashflow, rttm)
    v = F.value_score(rhist, rttm)
    dcf = F.simple_dcf(cashflow, shares, price) if (price and shares) else {
        "intrinsic_value": None, "note": "no price/shares"}
    mom = T.momentum_score(df) if not df.empty else {"score": 50.0}
    sent = sentiment_provider.score(sym)

    # dcf leg: reward margin of safety, clamp to 0..100
    mos = dcf.get("margin_of_safety")
    dcf_leg = 50.0 if mos is None else max(0, min(100, 50 + mos * 100))

    legs = {
        "quality":   q["score"],
        "value":     v["score"],
        "momentum":  mom["score"],
        "sentiment": sent["score"],
        "dcf":       round(dcf_leg, 1),
    }
    composite = round(sum(legs[k] * WEIGHTS[k] for k in WEIGHTS), 1)

    levels = T.entry_exit_levels(df) if not df.empty else {}
    forecast = T.naive_forecast(df) if not df.empty else {}

    # extra fundamentals on screen
    km = client.key_metrics(sym)
    multiples = F.valuation_multiples(rttm, km)
    pe_dist = F.pe_distribution(rhist)

    # prediction zones: volatility cone + valuation corridor (both sigma-based)
    closes = df["close"].tolist() if not df.empty else []

    # NTM EPS — prefer real analyst estimates (matches corridor.html), else proxy
    ntm_info = {"ntm_eps": None}
    try:
        ntm_info = F.ntm_eps_from_estimates(client.estimates(sym))
    except Exception:
        ntm_info = {"ntm_eps": None, "note": "estimates unavailable"}

    ntm_eps = ntm_info.get("ntm_eps")
    eps_source = ntm_info.get("source", "none")
    if not ntm_eps:
        # fallback: TTM EPS grown 8% (clearly labeled as a proxy)
        eps_ttm = None
        if income:
            ni = income[0].get("netIncome")
            if ni and shares:
                try:
                    eps_ttm = float(ni) / shares
                except (TypeError, ValueError):
                    eps_ttm = None
        ntm_eps = eps_ttm * 1.08 if eps_ttm else None
        eps_source = "proxy_ttm_x1.08" if ntm_eps else "none"

    zones = PZ.build_zones(closes, ntm_eps=ntm_eps,
                           pe_median=pe_dist.get("median"),
                           pe_sigma=pe_dist.get("sigma"))
    if zones.get("corridor", {}).get("ok"):
        zones["corridor"]["eps_source"] = eps_source
        zones["corridor"]["ntm_eps_low"] = ntm_info.get("ntm_eps_low")
        zones["corridor"]["ntm_eps_high"] = ntm_info.get("ntm_eps_high")

    # downsampled price series (date, close) for instant native charting.
    # keep ~1 year so the chart's 3M/6M/1Y toggle has data to slice.
    series = []
    if not df.empty:
        d = df[["date", "close"]].tail(260)
        series = [{"d": str(r.date.date()), "c": round(float(r.close), 2)}
                  for r in d.itertuples()]

    _result = {
        "symbol": sym.upper(),
        "company": profile.get("companyName", sym.upper()),
        "price": price,
        "composite_score": composite,
        "legs": legs, "weights": WEIGHTS,
        "quality": q, "value": v, "dcf": dcf,
        "momentum": mom, "sentiment": sent,
        "entry_exit": levels, "naive_forecast": forecast,
        "multiples": multiples, "pe_distribution": pe_dist,
        "zones": zones, "series": series,
    }
    return _clean(_result)


def _verdict(score: float) -> str:
    if score >= 70: return "Strong profile — warrants a closer look"
    if score >= 58: return "Constructive — conditions broadly favorable"
    if score >= 45: return "Mixed — no clear edge"
    if score >= 35: return "Weak — headwinds outweigh"
    return "Poor — multiple red flags"


def report(res: dict, macro: dict) -> str:
    L = res["legs"]
    out = []
    out.append(f"\n{'='*64}")
    out.append(f"  {res['symbol']}  —  {res['company']}")
    out.append(f"  Price: {res['price']}   Composite: {res['composite_score']}/100")
    out.append(f"  {_verdict(res['composite_score'])}")
    out.append(f"{'='*64}")
    out.append("  Component scores (each 0–100, with its weight):")
    for k in res["weights"]:
        out.append(f"    {k:<10} {L[k]:>6}   x{res['weights'][k]:.2f}")
    out.append("")
    q = res["quality"]
    out.append(f"  Quality factors: {q.get('factors')}")
    if q.get("rev_cagr") is not None:
        out.append(f"    revenue CAGR: {q['rev_cagr']:.1%}")
    v = res["value"]
    out.append(f"  Valuation: P/E now {v.get('pe_now')} vs hist median "
               f"{v.get('pe_median_hist')}  (discount {v.get('discount_vs_history')})")
    d = res["dcf"]
    if d.get("intrinsic_value") is not None:
        out.append(f"  DCF intrinsic: {d['intrinsic_value']}  "
                   f"margin of safety {d.get('margin_of_safety')}  "
                   f"(assumes {d['assumptions']})")
    m = res["momentum"]
    out.append(f"  Momentum: {'>200dma ' if m.get('above_200dma') else '<200dma '}"
               f"{'golden-cross' if m.get('golden_cross') else 'no golden-cross'}  "
               f"3m {m.get('ret_3m')}")
    s = res["sentiment"]
    out.append(f"  Sentiment: {s['score']}/100 from {s['n']} items ({s['source']})")

    ee = res["entry_exit"]
    if ee:
        # apply macro tilt to how strict we are: print as guidance
        tilt = macro.get("tilt", 1.0)
        out.append(f"\n  Entry/exit (rule-based; macro tilt x{tilt} on strictness):")
        out.append(f"    support {ee.get('support')} | resistance {ee.get('resistance')}")
        out.append(f"    vwap band {ee.get('vwap_lower_band')}–{ee.get('vwap_upper_band')}")
        out.append(f"    suggested stop (2*ATR): {ee.get('suggested_stop_2atr')}")
    f = res["naive_forecast"]
    if f.get("central_projection"):
        out.append(f"  21d cone (humble): {f['ci95_low']} … {f['central_projection']} "
                   f"… {f['ci95_high']}")
        out.append(f"    └ {f['warning']}")
    return "\n".join(out)


def main(symbols):
    try:
        client = FMPClient()
    except FMPError as e:
        print(e); sys.exit(1)

    macro = S.macro_regime(client)
    print(f"\nMacro regime: {macro.get('regime')}  (10y-2y spread "
          f"{macro.get('spread_10y_2y')})  -> strictness tilt x{macro.get('tilt')}")

    sp = S.FMPNewsSentiment(client)   # swap in S.XSentiment(fetch_fn=...) to add X
    results = []
    for sym in symbols:
        try:
            res = analyze(client, sym, sp)
            results.append(res)
            print(report(res, macro))
        except Exception as e:
            print(f"\n[{sym}] failed: {e}")

    # rank
    results.sort(key=lambda r: r["composite_score"], reverse=True)
    print(f"\n{'='*64}\n  RANKING\n{'='*64}")
    for r in results:
        print(f"  {r['composite_score']:>5}/100  {r['symbol']:<6} {r['company']}")
    print()


if __name__ == "__main__":
    args = sys.argv[1:] or ["AAPL"]
    main(args)
