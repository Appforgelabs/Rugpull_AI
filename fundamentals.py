"""
fundamentals.py — the part of the system you can actually trust.

Everything here is transparent and inspectable. No black boxes. Three things:
  1. quality_score()  — is this a financially healthy business?
  2. value_score()    — is it cheap relative to its own history / cash flows?
  3. simple_dcf()     — a back-of-envelope intrinsic value with VISIBLE assumptions.

A DCF is not a price oracle. It is a way to make your assumptions explicit so
you can argue with them. The output is only as good as the growth/discount
inputs, which is exactly why they're function arguments, not hidden constants.
"""

from __future__ import annotations
import numpy as np


def _safe(d: dict, *keys, default=np.nan):
    """Pull first present key from an FMP record (field names drift across routes)."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", 0) or (v == 0 and k in keys[:1]):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def quality_score(income: list, balance: list, cashflow: list, ratios_ttm: dict) -> dict:
    """
    0–100 composite of business quality. Each sub-factor is clamped to [0,1]
    then weighted, so you can see WHICH factor drives the score.
    """
    inc = income[0] if income else {}
    bal = balance[0] if balance else {}

    gross_margin   = _safe(ratios_ttm, "grossProfitMarginTTM", "grossProfitMargin")
    net_margin     = _safe(ratios_ttm, "netProfitMarginTTM", "netProfitMargin")
    roe            = _safe(ratios_ttm, "returnOnEquityTTM", "returnOnEquity")
    current_ratio  = _safe(ratios_ttm, "currentRatioTTM", "currentRatio")
    debt_to_equity = _safe(ratios_ttm, "debtEquityRatioTTM", "debtToEquityRatio",
                           "debtEquityRatio")

    # revenue growth (CAGR over available annual statements)
    rev = [_safe(x, "revenue") for x in income if _safe(x, "revenue") == _safe(x, "revenue")]
    rev = [r for r in rev if r and r == r]
    rev_cagr = np.nan
    if len(rev) >= 2 and rev[-1] > 0:
        yrs = len(rev) - 1
        rev_cagr = (rev[0] / rev[-1]) ** (1 / yrs) - 1  # rev[0]=newest

    factors = {
        "gross_margin":  _clamp(gross_margin / 0.6),       # 60% margin -> full marks
        "net_margin":    _clamp(net_margin / 0.25),        # 25% -> full marks
        "roe":           _clamp(roe / 0.25),               # 25% ROE -> full marks
        "liquidity":     _clamp((current_ratio - 0.8) / 1.2),
        "leverage":      _clamp(1 - (debt_to_equity / 2.0)),  # lower is better
        "rev_growth":    _clamp((rev_cagr + 0.05) / 0.30) if rev_cagr == rev_cagr else 0.5,
    }
    weights = {"gross_margin": .15, "net_margin": .20, "roe": .20,
               "liquidity": .10, "leverage": .15, "rev_growth": .20}

    score = sum(factors[k] * weights[k] for k in weights) * 100
    return {"score": round(score, 1), "factors": {k: round(v, 2) for k, v in factors.items()},
            "rev_cagr": None if rev_cagr != rev_cagr else round(rev_cagr, 3)}


def value_score(ratios_hist: list, ratios_ttm: dict) -> dict:
    """
    Is it cheap vs its OWN history? Compares current P/E and P/FCF to the
    median of prior years. Cross-company P/E comparisons are misleading;
    a stock vs its own past is more honest.
    """
    pe_now  = _safe(ratios_ttm, "priceEarningsRatioTTM", "priceToEarningsRatioTTM",
                    "peRatioTTM")
    pfcf_now = _safe(ratios_ttm, "priceToFreeCashFlowsRatioTTM", "pfcfRatioTTM",
                     "priceToFreeCashFlowRatioTTM")

    pe_hist = [_safe(x, "priceEarningsRatio", "peRatio") for x in ratios_hist]
    pe_hist = [x for x in pe_hist if x == x and x > 0]
    pe_med = np.median(pe_hist) if pe_hist else np.nan

    discount = np.nan
    if pe_now == pe_now and pe_med == pe_med and pe_now > 0:
        discount = (pe_med - pe_now) / pe_med   # +ve = cheaper than history

    score = _clamp((discount + 0.3) / 0.6) * 100 if discount == discount else 50.0
    return {"score": round(score, 1),
            "pe_now": _r(pe_now), "pe_median_hist": _r(pe_med),
            "pfcf_now": _r(pfcf_now),
            "discount_vs_history": None if discount != discount else round(discount, 3)}


def simple_dcf(cashflow: list, shares_out: float, price: float,
               growth: float = 0.08, terminal_growth: float = 0.025,
               discount_rate: float = 0.09, years: int = 5) -> dict:
    """
    Two-stage FCF DCF. ALL assumptions are arguments — change them and watch
    the answer move. That sensitivity IS the point.
    """
    fcfs = [_safe(x, "freeCashFlow") for x in cashflow]
    fcfs = [f for f in fcfs if f == f]
    if not fcfs or not shares_out or shares_out <= 0:
        return {"intrinsic_value": None, "note": "insufficient FCF/share data"}

    base_fcf = fcfs[0]  # newest
    pv = 0.0
    proj = base_fcf
    for t in range(1, years + 1):
        proj *= (1 + growth)
        pv += proj / (1 + discount_rate) ** t
    terminal = proj * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv += terminal / (1 + discount_rate) ** years

    iv_per_share = pv / shares_out
    margin = (iv_per_share - price) / price if price else np.nan
    return {
        "intrinsic_value": round(iv_per_share, 2),
        "current_price": price,
        "margin_of_safety": None if margin != margin else round(margin, 3),
        "assumptions": {"growth": growth, "terminal_growth": terminal_growth,
                        "discount_rate": discount_rate, "years": years},
    }


def valuation_multiples(ratios_ttm: dict, key_metrics: list) -> dict:
    """Pull the multiples you want on screen: P/E, P/S, P/FCF, P/B, EV/EBITDA,
    dividend yield. Field names drift across FMP routes, so each tries aliases."""
    km = key_metrics[0] if key_metrics else {}
    return {
        "P/E":        _r(_safe(ratios_ttm, "priceEarningsRatioTTM",
                               "priceToEarningsRatioTTM", "peRatioTTM")),
        "P/S":        _r(_safe(ratios_ttm, "priceToSalesRatioTTM",
                               "priceSalesRatioTTM")),
        "P/FCF":      _r(_safe(ratios_ttm, "priceToFreeCashFlowsRatioTTM",
                               "pfcfRatioTTM", "priceToFreeCashFlowRatioTTM")),
        "P/B":        _r(_safe(ratios_ttm, "priceToBookRatioTTM", "pbRatioTTM")),
        "EV/EBITDA":  _r(_safe(ratios_ttm, "enterpriseValueOverEBITDATTM",
                               "evToEbitdaTTM",
                               default=_safe(km, "enterpriseValueOverEBITDA"))),
        "PEG":        _r(_safe(ratios_ttm, "priceEarningsToGrowthRatioTTM",
                               "pegRatioTTM")),
        "Div Yield":  _r(_safe(ratios_ttm, "dividendYieldTTM",
                               "dividendYielPercentageTTM")),
        "ROE":        _r(_safe(ratios_ttm, "returnOnEquityTTM", "returnOnEquity")),
        "Net Margin": _r(_safe(ratios_ttm, "netProfitMarginTTM", "netProfitMargin")),
    }


def pe_distribution(ratios_hist: list) -> dict:
    """Median + sigma of historical P/E — feeds the valuation corridor bands."""
    import numpy as _np
    pes = [_safe(x, "priceEarningsRatio", "peRatio") for x in ratios_hist]
    pes = [p for p in pes if p == p and p > 0]
    if not pes:
        return {"median": None, "sigma": None, "n": 0}
    return {"median": round(float(_np.median(pes)), 2),
            "sigma": round(float(_np.std(pes, ddof=1)) if len(pes) > 1 else 0.0, 2),
            "n": len(pes)}


def ntm_eps_from_estimates(estimates: list, as_of_ms: float | None = None) -> dict:
    """
    Next-twelve-month EPS = sum of the next 4 quarterly analyst EPS estimates
    whose period is in the future. Mirrors Engine.ntmEps() in corridor.html.

    FMP field names drift: stable route uses 'epsAvg'/'epsLow'/'epsHigh',
    some responses use 'estimatedEpsAvg' etc. Both are handled. Returns the
    consensus NTM EPS plus low/high so the corridor can show an estimate range.
    """
    import time as _time
    if not estimates:
        return {"ntm_eps": None, "n": 0, "note": "no analyst estimates"}

    now_ms = as_of_ms if as_of_ms is not None else _time.time() * 1000

    def _row_ms(r):
        d = r.get("date") or r.get("estimatedDate") or ""
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(str(d)[:10]).timestamp() * 1000
        except Exception:
            return None

    def _eps(r, which):
        # which in {"Avg","Low","High"}
        return _safe(r, f"eps{which}", f"estimatedEps{which}",
                     f"estimatedEPS{which}")

    fwd = []
    for r in estimates:
        ms = _row_ms(r)
        if ms is None or ms <= now_ms:
            continue
        avg = _eps(r, "Avg")
        if avg == avg:  # not NaN
            fwd.append((ms, avg, _eps(r, "Low"), _eps(r, "High")))
    fwd.sort(key=lambda x: x[0])
    nxt = fwd[:4]

    if len(nxt) < 4:
        return {"ntm_eps": None, "n": len(nxt),
                "note": "fewer than 4 forward quarters available"}

    ntm = sum(x[1] for x in nxt)
    lows = [x[2] for x in nxt if x[2] == x[2]]
    highs = [x[3] for x in nxt if x[3] == x[3]]
    return {
        "ntm_eps": round(ntm, 2),
        "ntm_eps_low": round(sum(lows), 2) if len(lows) == 4 else None,
        "ntm_eps_high": round(sum(highs), 2) if len(highs) == 4 else None,
        "n": 4, "source": "analyst_estimates",
    }


def _clamp(x, lo=0.0, hi=1.0):
    if x != x:  # NaN
        return 0.5
    return max(lo, min(hi, x))


def _r(x, n=2):
    return None if x != x else round(x, n)


def pe_distribution_from_prices(price_hist, earnings, lookback_days=900) -> dict:
    """Compute the historical P/E median+sigma the way the corridor chart does:
    daily close (from /stable/historical-price-eod) divided by trailing-12-month
    EPS (summed from /stable/earnings), instead of relying on the ratios
    endpoint. Robust on plans where /stable/ratios returns no history.

    price_hist: normalized history list of {date, close}.
    earnings: list of {date, eps/epsActual/...} quarterly rows.
    """
    import numpy as _np
    import datetime as _dt

    if not price_hist or not earnings:
        return {"median": None, "sigma": None, "n": 0, "source": "none"}

    # build sorted (date, eps) quarters
    q = []
    for e in earnings:
        d = e.get("date")
        eps = None
        for k in ("eps", "epsActual", "epsActualEstimate", "epsdiluted",
                  "epsActualReported"):
            if e.get(k) is not None:
                try:
                    eps = float(e[k]); break
                except (TypeError, ValueError):
                    pass
        if d and eps is not None:
            try:
                q.append((_dt.date.fromisoformat(str(d)[:10]), eps))
            except ValueError:
                pass
    q.sort()
    if len(q) < 4:
        return {"median": None, "sigma": None, "n": 0, "source": "none"}

    def ttm_eps_asof(day):
        # sum the 4 most recent quarterly EPS on/before `day`
        prior = [eps for (dt, eps) in q if dt <= day]
        if len(prior) < 4:
            return None
        return sum(prior[-4:])

    pes = []
    cutoff = _dt.date.today() - _dt.timedelta(days=lookback_days)
    for row in price_hist:
        ds = row.get("date")
        c = row.get("close")
        if not ds or c is None:
            continue
        try:
            day = _dt.date.fromisoformat(str(ds)[:10])
        except ValueError:
            continue
        if day < cutoff:
            continue
        ttm = ttm_eps_asof(day)
        if ttm and ttm > 0:
            pe = float(c) / ttm
            if 0 < pe < 500:           # drop nonsense
                pes.append(pe)

    if len(pes) < 20:
        return {"median": None, "sigma": None, "n": len(pes), "source": "none"}
    arr = _np.array(pes)
    return {"median": round(float(_np.median(arr)), 2),
            "sigma": round(float(arr.std(ddof=1)), 2),
            "n": len(pes), "source": "price/earnings"}
