"""
research_screener.py — AlphaScope-style upside rankings from YOUR real data.

The original AlphaScope used hardcoded analyst targets. This builds the same
idea — rank tickers by upside to a price target — but from numbers the app
already computes:

  • CORRIDOR UPSIDE: (corridor fair value − price) / price, where fair value =
    NTM EPS × historical-median P/E (your corridor's own method). This is the
    primary, valuation-based target.

  • ADJUSTED UPSIDE: corridor upside nudged by regime + sentiment. In risk-off
    the upside is discounted (tide is against it); strong news sentiment nudges
    it up, weak nudges down. Bounded so the tilt can't dominate the valuation.

Reads only from stored snapshots — no network. Tickers missing a corridor fair
value (e.g. unprofitable names with no positive NTM EPS) are flagged, not faked.
"""

from __future__ import annotations


def _corridor_fair(result: dict) -> float | None:
    corr = ((result or {}).get("zones") or {}).get("corridor") or {}
    if corr.get("ok") and corr.get("fair"):
        return float(corr["fair"])
    return None


def _sentiment_score(result: dict) -> float | None:
    s = (result or {}).get("sentiment") or {}
    v = s.get("score")
    return float(v) if isinstance(v, (int, float)) else None


def build_rankings(snapshots: dict, macro: dict | None) -> dict:
    """snapshots: {SYM: snapshot}. macro: build_macro() dict or None.
    Returns rows + summary KPIs."""
    mult = (macro or {}).get("risk_multiplier", 1.0)
    regime = (macro or {}).get("regime", "—")

    rows = []
    missing = []
    for sym, snap in snapshots.items():
        result = (snap or {}).get("result")
        if not result:
            missing.append(sym)
            continue
        price = result.get("price")
        fair = _corridor_fair(result)
        if not price or not fair:
            missing.append(sym)
            continue

        corridor_upside = (fair - price) / price * 100.0

        # adjustment: regime tilt on the upside magnitude + sentiment nudge
        sent = _sentiment_score(result)          # 0..100, 50 neutral
        sent_tilt = ((sent - 50) / 50 * 8.0) if sent is not None else 0.0  # ±8pts max
        # regime multiplies the upside (risk-off shrinks conviction in upside)
        adj = corridor_upside * mult + sent_tilt

        composite = result.get("composite_score")
        rows.append({
            "ticker": sym,
            "company": result.get("company", sym),
            "price": round(price, 2),
            "fair_value": round(fair, 2),
            "corridor_upside": round(corridor_upside, 1),
            "adjusted_upside": round(adj, 1),
            "sentiment": round(sent, 0) if sent is not None else None,
            "composite": composite,
        })

    by_corr = sorted(rows, key=lambda r: r["corridor_upside"], reverse=True)
    for i, r in enumerate(by_corr, 1):
        r["rank_corridor"] = i
    by_adj = sorted(rows, key=lambda r: r["adjusted_upside"], reverse=True)
    for i, r in enumerate(by_adj, 1):
        r["rank_adjusted"] = i

    avg_corr = (sum(r["corridor_upside"] for r in rows) / len(rows)) if rows else 0
    n_under = sum(1 for r in rows if r["corridor_upside"] > 0)   # trading below fair
    return {
        "ok": bool(rows),
        "rows": rows,
        "by_corridor": by_corr,
        "by_adjusted": by_adj,
        "regime": regime, "risk_multiplier": mult,
        "avg_corridor_upside": round(avg_corr, 1),
        "n_below_fair": n_under, "n_total": len(rows),
        "missing": missing,
        "note": "Upside = distance to corridor fair value (NTM EPS × historical "
                "P/E). Adjusted = regime-tilted + sentiment-nudged. A target is "
                "a model output, not a promise; unprofitable names without a "
                "positive NTM EPS are listed under 'no target', not invented.",
    }
