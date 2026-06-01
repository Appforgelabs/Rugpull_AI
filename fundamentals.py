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


def _clamp(x, lo=0.0, hi=1.0):
    if x != x:  # NaN
        return 0.5
    return max(lo, min(hi, x))


def _r(x, n=2):
    return None if x != x else round(x, n)
