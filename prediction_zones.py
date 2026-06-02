"""
prediction_zones.py — green/red probability zones, NOT a forecast.

Two overlaid methods, both using the standard-deviation approach from your
corridor chart:

1. VOLATILITY CONE (price-based)
   Projects forward from today using daily log-return volatility. The bands are
   ±1σ and ±2σ of where a random walk could drift. Green = the ±1σ "normal"
   zone; red = the ±2σ "stretched" edges. This is a probability range, not a
   target. The WIDTH is the message: it shows how uncertain the future is.

2. VALUATION CORRIDOR (earnings-based, like your corridor.html)
   If you supply forward EPS + a P/E distribution, projects price = NTM_EPS ×
   (median P/E ± σ). Same math as Engine.corridor() in your HTML.

Neither predicts direction. They frame the range of outcomes so you size
positions and set stops with clear eyes.
"""

from __future__ import annotations
import math


def _mean(a):
    return sum(a) / len(a) if a else 0.0


def _stdev(a):
    n = len(a)
    if n < 2:
        return 0.0
    m = _mean(a)
    return math.sqrt(sum((x - m) ** 2 for x in a) / (n - 1))


def volatility_cone(closes: list[float], horizon_days: int = 63,
                    steps: int = 12) -> dict:
    """
    closes: chronological close prices (oldest -> newest).
    Returns band points from today out to horizon, at `steps` intervals.
    Uses last ~126 sessions of daily log returns for the vol estimate.
    """
    if not closes or len(closes) < 30:
        return {"ok": False, "note": "need >=30 closes"}

    logrets = []
    window = closes[-127:]
    for i in range(1, len(window)):
        if window[i - 1] > 0 and window[i] > 0:
            logrets.append(math.log(window[i] / window[i - 1]))
    if len(logrets) < 20:
        return {"ok": False, "note": "insufficient returns"}

    mu = _mean(logrets)
    sigma = _stdev(logrets)
    spot = closes[-1]

    pts = []
    for s in range(0, steps + 1):
        t = round(horizon_days * s / steps)
        center = spot * math.exp(mu * t)
        sig_t = sigma * math.sqrt(t)
        pts.append({
            "day": t,
            "center": round(center, 2),
            "p1_up": round(center * math.exp(+1 * sig_t), 2),   # +1σ
            "p1_dn": round(center * math.exp(-1 * sig_t), 2),   # -1σ
            "p2_up": round(center * math.exp(+2 * sig_t), 2),   # +2σ
            "p2_dn": round(center * math.exp(-2 * sig_t), 2),   # -2σ
        })

    return {
        "ok": True, "method": "volatility_cone", "spot": round(spot, 2),
        "daily_vol": round(sigma, 4), "horizon_days": horizon_days,
        "points": pts,
        "note": "Probability zone, not a forecast. Green=±1σ normal range, "
                "red edges=±2σ. Width grows with time = rising uncertainty.",
    }


def valuation_corridor(ntm_eps: float | None, pe_median: float | None,
                       pe_sigma: float | None, spot: float | None) -> dict:
    """
    Earnings-anchored bands, mirroring corridor.html's Engine.corridor():
        price = NTM_EPS * (median_PE ± k*sigma)
    Needs your forward EPS + P/E distribution. Returns None-ish if unavailable.
    """
    if not (ntm_eps and pe_median and pe_median > 0):
        return {"ok": False, "note": "needs ntm_eps + pe_median"}
    sd = pe_sigma or 0.0
    return {
        "ok": True, "method": "valuation_corridor",
        "ntm_eps": round(ntm_eps, 2), "pe_median": round(pe_median, 2),
        "pe_sigma": round(sd, 2),
        "fair": round(ntm_eps * pe_median, 2),
        "p1_up": round(ntm_eps * (pe_median + sd), 2),
        "p1_dn": round(ntm_eps * (pe_median - sd), 2),
        "p2_up": round(ntm_eps * (pe_median + 1.5 * sd), 2),
        "p2_dn": round(ntm_eps * (pe_median - 1.5 * sd), 2),
        "spot": round(spot, 2) if spot else None,
        "note": "Where price sits if it trades at its historical P/E range "
                "on forward earnings. Same method as your corridor chart.",
    }


def build_zones(closes: list[float], ntm_eps=None, pe_median=None,
                pe_sigma=None) -> dict:
    """Convenience: build both overlays from what's available."""
    spot = closes[-1] if closes else None
    return {
        "cone": volatility_cone(closes),
        "corridor": valuation_corridor(ntm_eps, pe_median, pe_sigma, spot),
    }
