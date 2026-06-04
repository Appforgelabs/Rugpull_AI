"""
dashboard.py — the command center.

Synthesizes each ticker's analyzer score + trading signals into three clear
action lanes — INVEST (long-term), SWING (days-weeks), DAY (intraday) — so the
good setups surface instead of getting buried in tables.

Rendered as a self-contained Anduril-styled HTML console: near-black ground,
single amber accent, monospace technical type, thin precise borders, uppercase
micro-labels. No external libraries.

Nothing here is advice. Each lane shows WHY (the basis) and the rules score, so
you act on the reasoning, not the color.
"""

from __future__ import annotations
import json
import html as _html


# Anduril-ish tokens
THEME = {
    "bg": "#0a0c0f", "panel": "#0f1318", "edge": "#1d242c",
    "ink": "#c9d2da", "dim": "#5f6b76", "accent": "#ff6b1a",
    "long": "#3fb37f", "short": "#d6504f", "wait": "#6b7682",
    "mono": "'JetBrains Mono','SF Mono',ui-monospace,Menlo,monospace",
}


def _badge(bias: str) -> str:
    c = {"LONG": THEME["long"], "SHORT": THEME["short"]}.get(bias, THEME["wait"])
    return (f'<span style="color:{c};border:1px solid {c};border-radius:2px;'
            f'padding:1px 7px;font-size:10px;letter-spacing:.12em;'
            f'font-weight:600">{_html.escape(bias)}</span>')


def _conviction_bar(score, mx) -> str:
    score = score or 0
    mx = mx or 1
    pct = max(0, min(100, score / mx * 100))
    return (f'<div style="height:3px;background:{THEME["edge"]};border-radius:2px;'
            f'margin-top:5px"><div style="width:{pct}%;height:100%;'
            f'background:{THEME["accent"]};border-radius:2px"></div></div>')


def _lane_cell(title, bias, score, mx, basis, setup) -> str:
    rows = ""
    if setup:
        rows = (
            f'<div style="font-size:11px;color:{THEME["dim"]};margin-top:6px;'
            f'font-family:{THEME["mono"]}">'
            f'E <span style="color:{THEME["ink"]}">{setup.get("entry")}</span> &middot; '
            f'S <span style="color:{THEME["short"]}">{setup.get("stop")}</span> &middot; '
            f'T <span style="color:{THEME["long"]}">{setup.get("target")}</span>'
            f' &middot; {setup.get("rr","")}</div>'
        )
    return (
        f'<td style="padding:10px 14px;border-left:1px solid {THEME["edge"]};'
        f'vertical-align:top">'
        f'<div style="font-size:9px;letter-spacing:.18em;color:{THEME["dim"]};'
        f'margin-bottom:5px">{title}</div>'
        f'{_badge(bias)}'
        f'<div style="font-size:10px;color:{THEME["dim"]};margin-top:5px">'
        f'{score}/{mx} signals</div>'
        f'{_conviction_bar(score, mx)}'
        f'<div style="font-size:9px;color:{THEME["dim"]};margin-top:5px;'
        f'font-style:italic">{_html.escape(basis or "")}</div>'
        f'{rows}</td>'
    )


def _invest_lane(result: dict) -> str:
    """Long-term lane from the analyzer composite + verdict."""
    score = result.get("composite_score", 0) if result else 0
    if score >= 70: bias = "LONG"
    elif score <= 40: bias = "SHORT"
    else: bias = "WAIT"
    basis = "fundamentals composite"
    return _lane_cell("INVEST &middot; LONG-TERM", bias, round(score / 10) if score else 0,
                      10, basis, None)


def render_dashboard(items: list, generated_at: str) -> str:
    """
    items: list of {symbol, price, result(analyzer dict|None), trading(dict|None)}.
    Sorted into a priority feed: strongest aligned setups first.
    """
    T = THEME

    def priority(it):
        tr = it.get("trading") or {}
        sg = tr.get("signal", {})
        prob = sg.get("probability", 50) or 50
        # alignment bonus when invest+swing+day agree
        biases = {
            "inv": _invest_bias(it.get("result")),
            "sw": sg.get("swing", {}).get("bias"),
            "dy": sg.get("day", {}).get("bias"),
        }
        longs = sum(1 for b in biases.values() if b == "LONG")
        return (longs, prob)

    items = sorted(items, key=priority, reverse=True)

    cards = ""
    for it in items:
        sym = _html.escape(it.get("symbol", "—"))
        price = it.get("price")
        result = it.get("result")
        tr = it.get("trading") or {}
        sg = tr.get("signal", {})

        if not sg:
            cards += _empty_card(sym, price)
            continue

        st = tr.get("supertrend", {})
        st_dir = st.get("dir", 0)
        st_txt = ("&#9650; UP" if st_dir > 0 else "&#9660; DN" if st_dir < 0 else "—")
        st_col = T["long"] if st_dir > 0 else T["short"] if st_dir < 0 else T["dim"]

        prob = sg.get("probability", 50)
        rsi_d = tr.get("rsi_D")
        adx = sg.get("adx")

        invest = _invest_lane(result) if result else _lane_cell(
            "INVEST &middot; LONG-TERM", "NO DATA", 0, 10, "run Analyzer update", None)
        swing = sg.get("swing", {})
        day = sg.get("day", {})

        cards += (
            f'<div style="background:{T["panel"]};border:1px solid {T["edge"]};'
            f'border-radius:4px;margin-bottom:10px;overflow:hidden">'
            # header strip
            f'<table style="width:100%;border-collapse:collapse"><tr>'
            f'<td style="padding:12px 16px;width:200px">'
            f'<div style="font-family:{T["mono"]};font-size:20px;font-weight:700;'
            f'color:{T["ink"]};letter-spacing:.04em">{sym}</div>'
            f'<div style="font-family:{T["mono"]};font-size:12px;color:{T["dim"]}">'
            f'${price if price is not None else "—"}</div>'
            f'<div style="margin-top:7px;font-size:10px;color:{st_col};'
            f'font-family:{T["mono"]}">SUPERTREND {st_txt}</div>'
            f'<div style="font-size:9px;color:{T["dim"]};margin-top:4px">'
            f'RSI(D) {rsi_d if rsi_d is not None else "—"} &middot; ADX {adx if adx is not None else "—"}'
            f' &middot; conv {prob}%</div>'
            f'</td>'
            + _invest_lane_td(invest)
            + _lane_cell("SWING &middot; DAYS&ndash;WKS", swing.get("bias", "—"),
                         swing.get("score"), swing.get("max"),
                         swing.get("basis"), swing.get("setup"))
            + _lane_cell("DAY &middot; INTRADAY", day.get("bias", "—"),
                         day.get("score"), day.get("max"),
                         day.get("basis"), day.get("setup"))
            + '</tr></table></div>'
        )

    return f"""
<div style="background:{T['bg']};padding:18px 20px;border-radius:6px;
     font-family:system-ui;color:{T['ink']}">
  <div style="display:flex;justify-content:space-between;align-items:baseline;
       border-bottom:1px solid {T['accent']};padding-bottom:10px;margin-bottom:16px">
    <div>
      <span style="font-family:{T['mono']};font-size:15px;letter-spacing:.22em;
            color:{T['accent']};font-weight:700">RUGPULL_AI</span>
      <span style="font-family:{T['mono']};font-size:11px;letter-spacing:.18em;
            color:{T['dim']};margin-left:10px">// TACTICAL FEED</span>
    </div>
    <span style="font-family:{T['mono']};font-size:10px;color:{T['dim']};
          letter-spacing:.1em">SNAPSHOT {_html.escape(generated_at)}</span>
  </div>
  <div style="display:flex;gap:18px;margin-bottom:14px;font-family:{T['mono']};
       font-size:10px;letter-spacing:.1em;color:{T['dim']}">
    <span>&#9632; <span style="color:{T['long']}">LONG</span></span>
    <span>&#9632; <span style="color:{T['short']}">SHORT</span></span>
    <span>&#9632; <span style="color:{T['wait']}">WAIT / NO-DATA</span></span>
    <span style="margin-left:auto">SORTED BY ALIGNMENT &times; CONVICTION</span>
  </div>
  {cards or _no_data()}
  <div style="margin-top:14px;font-size:10px;color:{T['dim']};
       font-family:{T['mono']};line-height:1.6;border-top:1px solid {T['edge']};
       padding-top:10px">
    CONVICTION = INDICATOR AGREEMENT, NOT A WIN-RATE. CAPPED 72%.<br>
    E/S/T = ENTRY / STOP / TARGET (ATR &amp; SUPERTREND DERIVED). NOT ADVICE.
  </div>
</div>"""


# the invest lane returns a full <td> already-rendered via _lane_cell, so just pass through
def _invest_lane_td(cell_html: str) -> str:
    return cell_html


def _invest_bias(result: dict) -> str:
    if not result:
        return "NO DATA"
    s = result.get("composite_score", 0)
    return "LONG" if s >= 70 else "SHORT" if s <= 40 else "WAIT"


def _empty_card(sym: str, price) -> str:
    T = THEME
    return (
        f'<div style="background:{T["panel"]};border:1px solid {T["edge"]};'
        f'border-radius:4px;margin-bottom:10px;padding:14px 16px">'
        f'<span style="font-family:{T["mono"]};font-size:18px;font-weight:700;'
        f'color:{T["ink"]}">{sym}</span>'
        f'<span style="font-size:11px;color:{T["dim"]};margin-left:12px">'
        f'no trading data — run UPDATE TRADING DATA</span></div>'
    )


def _no_data() -> str:
    return (f'<div style="color:{THEME["dim"]};font-family:{THEME["mono"]};'
            f'padding:30px;text-align:center;font-size:12px">'
            f'NO SNAPSHOTS // RUN UPDATE ALL + UPDATE TRADING DATA</div>')
