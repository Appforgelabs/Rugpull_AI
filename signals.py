"""
signals.py — macro regime + sentiment, as CONTEXT not as a crystal ball.

Macro is used as a regime filter: in a hostile rate/curve regime you demand a
bigger margin of safety, you don't flip your whole thesis. Sentiment is a
contrarian-leaning tilt, intentionally small-weighted because it's noisy.

Sentiment providers are pluggable:
  - FMPNewsSentiment   -> uses news you already pay FMP for (default, free-ish)
  - XSentiment         -> optional, OFF by default. X reads are pay-per-use
                          ($0.005/read, 2M/mo cap) so only enable if it earns
                          its cost. Plug in your own provider via .score(sym).
"""

from __future__ import annotations
import numpy as np


# ---------------------------------------------------------------- macro ----
def macro_regime(client) -> dict:
    """
    Crude but useful regime read from the yield curve. Inverted curve / high
    short rates => 'risk-off' tilt that should make you stricter on entries.
    Returns a multiplier you apply to required margin of safety.
    """
    try:
        rates = client.fetch("treasury", "")  # treasury endpoint ignores sym
        latest = rates[0] if isinstance(rates, list) and rates else {}
        y2  = float(latest.get("year2", latest.get("month2", "nan")) or "nan")
        y10 = float(latest.get("year10", "nan") or "nan")
    except Exception:
        return {"regime": "unknown", "tilt": 1.0, "note": "treasury data unavailable"}

    if y2 != y2 or y10 != y10:
        return {"regime": "unknown", "tilt": 1.0}

    spread = y10 - y2
    if spread < -0.2:
        regime, tilt = "inverted / late-cycle caution", 1.25
    elif spread < 0.3:
        regime, tilt = "flat / neutral", 1.05
    else:
        regime, tilt = "steep / risk-on friendly", 0.95
    return {"regime": regime, "tilt": tilt,
            "10y": round(y10, 2), "2y": round(y2, 2),
            "spread_10y_2y": round(spread, 2),
            "note": "tilt multiplies the margin of safety you demand at entry"}


# ----------------------------------------------------------- sentiment ----
class SentimentProvider:
    """Interface: return {'score': 0..100, 'n': int, 'source': str}."""
    def score(self, sym: str) -> dict:
        raise NotImplementedError


class FMPNewsSentiment(SentimentProvider):
    """
    Uses FMP's stock news feed. Some FMP plans return a 'sentiment' field; if
    absent, we fall back to a tiny lexicon on headlines. It's rough — that's
    why sentiment carries small weight in the composite.
    """
    POS = {"beat", "beats", "surge", "soar", "record", "upgrade", "bullish",
           "growth", "strong", "raises", "outperform", "rally", "wins", "tops"}
    NEG = {"miss", "misses", "plunge", "drop", "downgrade", "bearish", "weak",
           "cuts", "lawsuit", "probe", "recall", "falls", "slump", "warns"}

    def __init__(self, client):
        self.client = client

    def score(self, sym: str) -> dict:
        try:
            items = self.client.news(sym) or []
        except Exception:
            return {"score": 50.0, "n": 0, "source": "fmp_news (unavailable)"}
        if not isinstance(items, list) or not items:
            return {"score": 50.0, "n": 0, "source": "fmp_news (empty)"}

        vals = []
        for it in items[:50]:
            s = it.get("sentiment")
            if isinstance(s, (int, float)):
                vals.append(_norm_sentiment(float(s)))
            else:
                text = f"{it.get('title','')} {it.get('text','')}".lower()
                p = sum(w in text for w in self.POS)
                n = sum(w in text for w in self.NEG)
                if p or n:
                    vals.append((p - n) / (p + n))
        if not vals:
            return {"score": 50.0, "n": len(items), "source": "fmp_news (no signal)"}
        mean = float(np.mean(vals))
        return {"score": round((mean + 1) / 2 * 100, 1), "n": len(vals),
                "source": "fmp_news"}


class XSentiment(SentimentProvider):
    """
    OPTIONAL. Off by default. X API is pay-per-use now (~$0.005/read, 2M/mo
    cap), so wire in your provider (official or a cheaper reseller) here and
    only enable it when you've decided the signal beats the cost.
    """
    def __init__(self, fetch_fn=None):
        # fetch_fn(sym) -> list of post texts. You supply it.
        self.fetch_fn = fetch_fn

    def score(self, sym: str) -> dict:
        if self.fetch_fn is None:
            return {"score": 50.0, "n": 0, "source": "x (disabled)"}
        posts = self.fetch_fn(sym) or []
        lex = FMPNewsSentiment(None)
        vals = []
        for t in posts:
            t = t.lower()
            p = sum(w in t for w in lex.POS)
            n = sum(w in t for w in lex.NEG)
            if p or n:
                vals.append((p - n) / (p + n))
        if not vals:
            return {"score": 50.0, "n": len(posts), "source": "x (no signal)"}
        return {"score": round((float(np.mean(vals)) + 1) / 2 * 100, 1),
                "n": len(vals), "source": "x"}


def _norm_sentiment(s: float) -> float:
    """Map common -1..1 or 0..1 sentiment scales to -1..1."""
    if 0 <= s <= 1:
        return s * 2 - 1
    return max(-1.0, min(1.0, s))
