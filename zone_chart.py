"""
zone_chart.py — a fast, dependency-free SVG chart.

Renders price history plus the green (±1σ) and red (±2σ) projection zones from
prediction_zones, as a single inline SVG string. No Chart.js, no iframe, no
network — so it draws instantly and doesn't lag like the embedded corridor.

Green zone  = ±1σ "normal" range.  Red zone = ±1σ..±2σ "stretched" edges.
A dashed center line marks the random-walk drift. This is a probability range,
not a price prediction.
"""

from __future__ import annotations


def _scale(v, lo, hi, a, b):
    if hi == lo:
        return (a + b) / 2
    return a + (v - lo) * (b - a) / (hi - lo)


def render_zone_svg(series: list, zones: dict, width=720, height=300) -> str:
    """series: [{'d':date,'c':close}]  zones: build_zones() output."""
    cone = (zones or {}).get("cone", {})
    if not series or not cone.get("ok"):
        return ("<div style='color:#8899aa;padding:24px;"
                "font:14px system-ui'>No chart data — hit Update for this "
                "ticker.</div>")

    pad_l, pad_r, pad_t, pad_b = 8, 8, 12, 20
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    hist = [p["c"] for p in series]
    n_hist = len(hist)
    pts = cone["points"]
    horizon = pts[-1]["day"]

    # x axis: history occupies left ~62%, projection the rest
    hist_frac = 0.62
    def hx(i):  # history index -> x
        return pad_l + _scale(i, 0, max(n_hist - 1, 1), 0, plot_w * hist_frac)
    def px(day):  # projection day -> x
        return pad_l + plot_w * hist_frac + _scale(day, 0, horizon, 0,
                                                   plot_w * (1 - hist_frac))

    # y range spans history + widest projection band
    ylo = min(min(hist), min(p["p2_dn"] for p in pts))
    yhi = max(max(hist), max(p["p2_up"] for p in pts))
    margin = (yhi - ylo) * 0.06 or 1
    ylo -= margin; yhi += margin
    def Y(v):
        return pad_t + plot_h - _scale(v, ylo, yhi, 0, plot_h)

    spot = hist[-1]
    x0 = hx(n_hist - 1)

    # build red zone polygon (outer ±2σ envelope)
    up2 = [(px(p["day"]), Y(p["p2_up"])) for p in pts]
    dn2 = [(px(p["day"]), Y(p["p2_dn"])) for p in pts]
    red_poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in ([(x0, Y(spot))] + up2 + dn2[::-1]))

    # green zone polygon (inner ±1σ)
    up1 = [(px(p["day"]), Y(p["p1_up"])) for p in pts]
    dn1 = [(px(p["day"]), Y(p["p1_dn"])) for p in pts]
    green_poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in ([(x0, Y(spot))] + up1 + dn1[::-1]))

    # center (drift) dashed line
    center_line = f"M {x0:.1f} {Y(spot):.1f} " + " ".join(
        f"L {px(p['day']):.1f} {Y(p['center']):.1f}" for p in pts)

    # history price path
    hist_path = "M " + " L ".join(f"{hx(i):.1f} {Y(v):.1f}" for i, v in enumerate(hist))

    # optional corridor (valuation) lines as horizontal markers at right edge
    corr = (zones or {}).get("corridor", {})
    corr_svg = ""
    if corr.get("ok"):
        xr = pad_l + plot_w
        for key, col, lab in [("fair", "#46d6c8", "fair"),
                              ("p1_up", "#e6a23c", ""), ("p1_dn", "#e6a23c", ""),
                              ("p2_up", "#d65a5a", ""), ("p2_dn", "#d65a5a", "")]:
            val = corr.get(key)
            if val is not None and ylo <= val <= yhi:
                yy = Y(val)
                corr_svg += (f"<line x1='{x0:.1f}' y1='{yy:.1f}' x2='{xr:.1f}' "
                             f"y2='{yy:.1f}' stroke='{col}' stroke-width='1' "
                             f"stroke-dasharray='2,3' opacity='0.5'/>")

    return (
        f'<div style="width:100%;max-width:{width}px">'
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="system-ui">'
        f'<polygon points="{red_poly}" fill="#d65a5a" opacity="0.16"/>'
        f'<polygon points="{green_poly}" fill="#3fb37f" opacity="0.20"/>'
        f'{corr_svg}'
        f'<path d="{center_line}" fill="none" stroke="#8899aa" '
        f'stroke-width="1.2" stroke-dasharray="4,4" opacity="0.8"/>'
        f'<path d="{hist_path}" fill="none" stroke="#46d6c8" stroke-width="1.8"/>'
        f'<line x1="{x0:.1f}" y1="{pad_t}" x2="{x0:.1f}" y2="{pad_t+plot_h}" '
        f'stroke="#8899aa" stroke-width="0.8" opacity="0.4"/>'
        f'<circle cx="{x0:.1f}" cy="{Y(spot):.1f}" r="3" fill="#46d6c8"/>'
        f'<text x="{pad_l}" y="{pad_t+10}" fill="#8899aa" font-size="11">{yhi:.0f}</text>'
        f'<text x="{pad_l}" y="{pad_t+plot_h}" fill="#8899aa" font-size="11">{ylo:.0f}</text>'
        f'</svg></div>'
    )
