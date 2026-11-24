#!/usr/bin/env python3
"""Chartist engine: reads a price chart the way a technical analyst does, by fixed
rules, and reports how each signal has actually worked on this security before.
Standard library only. Pure functions: bars in, analysis out. No network, no orders.

Bars: list of dicts {"t": "YYYY-MM-DD", "o", "h", "l", "c", "v"} oldest first.

Sections (each with the evidence behind it):
  trend        long (200-day), intermediate (50-day + swing structure), short (20-EMA,
               Supertrend); ADX strength; daily and weekly
  levels       support/resistance from clustered swing pivots (touches, last touch),
               52-week high/low, unfilled gaps
  momentum     RSI(14) zone + divergence, MACD(12,26,9), Stochastic(14,3)
  volatility   ATR(14) %, Bollinger(20,2) position and squeeze
  volume       volume vs 20-day average, OBV trend and divergence, up/down volume
  relative     return vs Nifty 50 over 3/6/12 months, RS-line trend
  candles      candlestick patterns on the last 3 bars, read with trend context
  patterns     double top/bottom, head & shoulders (+inverse), triangles, channels,
               range breakouts, flags — rule-based, each with status and target
  fibonacci    retracements of the last major swing
  signals      which standard signals are active now, and their TRACK RECORD on this
               security: forward 5/20/60-day returns after every past occurrence
               (non-overlapping) vs the security's ordinary returns
  verdict      bias from the weight of evidence, key levels, what would invalidate it

Definitions are textbook (Wilder RSI/ATR/ADX, Appel MACD, Bollinger, Granville OBV).
Pattern detection is rule-based and approximate: every pattern carries a status
(forming / confirmed / failed) and the exact pivots used, so it can be checked by eye.
This is analysis, not advice.
"""

import math
import statistics

# ---------------------------------------------------------------- basic series

def sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    out = [None] * len(x)
    if len(x) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(x[:n]) / n
    out[n - 1] = e
    for i in range(n, len(x)):
        e = x[i] * k + e * (1 - k)
        out[i] = e
    return out


def ema_opt(x, n):
    """EMA over a series with leading Nones."""
    start = next((i for i, v in enumerate(x) if v is not None), None)
    out = [None] * len(x)
    if start is None:
        return out
    sub = ema([v for v in x[start:]], n)
    out[start:] = sub
    return out


def wilder(x, n):
    """Wilder smoothing (RMA), seeded with the simple mean of the first n values."""
    out = [None] * len(x)
    if len(x) < n:
        return out
    r = sum(x[:n]) / n
    out[n - 1] = r
    for i in range(n, len(x)):
        r = (r * (n - 1) + x[i]) / n
        out[i] = r
    return out


def rsi(c, n=14):
    if len(c) <= n:
        return [None] * len(c)
    g = [0.0] + [max(0.0, c[i] - c[i - 1]) for i in range(1, len(c))]
    l = [0.0] + [max(0.0, c[i - 1] - c[i]) for i in range(1, len(c))]
    ag, al = wilder(g[1:], n), wilder(l[1:], n)
    out = [None]
    for a, b in zip(ag, al):
        if a is None:
            out.append(None)
        elif b == 0:
            out.append(100.0 if a > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + a / b))
    return out


def true_range(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return tr


def atr(h, l, c, n=14):
    return wilder(true_range(h, l, c), n)


def macd(c, fast=12, slow=26, sig=9):
    ef, es = ema(c, fast), ema(c, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    signal = ema_opt(line, sig)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, signal)]
    return line, signal, hist


def bollinger(c, n=20, k=2.0):
    mid, up, lo = sma(c, n), [None] * len(c), [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        sd = statistics.pstdev(w)
        up[i], lo[i] = mid[i] + k * sd, mid[i] - k * sd
    return mid, up, lo


def adx(h, l, c, n=14):
    pdm, ndm = [0.0], [0.0]
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
    tr = true_range(h, l, c)
    s_tr, s_p, s_n = wilder(tr[1:], n), wilder(pdm[1:], n), wilder(ndm[1:], n)
    pdi, ndi, dx = [None], [None], [None]
    for a, b, d in zip(s_tr, s_p, s_n):
        if a is None or a == 0:
            pdi.append(None), ndi.append(None), dx.append(None)
            continue
        p, q = 100 * b / a, 100 * d / a
        pdi.append(p), ndi.append(q)
        dx.append(100 * abs(p - q) / (p + q) if p + q else 0.0)
    first = next((i for i, v in enumerate(dx) if v is not None), None)
    out = [None] * len(c)
    if first is not None:
        sm = wilder([v for v in dx[first:]], n)
        out[first:] = sm
    return out, pdi, ndi


def stochastic(h, l, c, n=14, d=3):
    k = [None] * len(c)
    for i in range(n - 1, len(c)):
        hh, ll = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
        k[i] = 100 * (c[i] - ll) / (hh - ll) if hh > ll else 50.0
    kk = [v for v in k if v is not None]
    ds = sma(kk, d)
    dd = [None] * (len(c) - len(kk)) + ds
    return k, dd


def obv(c, v):
    out = [0.0]
    for i in range(1, len(c)):
        out.append(out[-1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0.0))
    return out


def supertrend(h, l, c, n=10, mult=3.0):
    a = atr(h, l, c, n)
    st, dirn = [None] * len(c), [None] * len(c)
    fu = fl = None
    for i in range(len(c)):
        if a[i] is None:
            continue
        mid = (h[i] + l[i]) / 2
        bu, bl = mid + mult * a[i], mid - mult * a[i]
        if fu is None:
            fu, fl, d = bu, bl, 1
        else:
            fu = bu if (bu < fu or c[i - 1] > fu) else fu
            fl = bl if (bl > fl or c[i - 1] < fl) else fl
            d = dirn[i - 1] or 1
            if d == 1 and c[i] < fl:
                d = -1
            elif d == -1 and c[i] > fu:
                d = 1
        dirn[i] = d
        st[i] = fl if d == 1 else fu
    return st, dirn


def slope_pct(x, n):
    """% change of a series over the last n points (None-safe)."""
    if len(x) <= n or x[-1] is None or x[-1 - n] is None or x[-1 - n] == 0:
        return None
    return (x[-1] / x[-1 - n] - 1) * 100


def linreg(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return b, my - b * mx


# ---------------------------------------------------------------- resampling

def weekly(bars):
    """Daily bars -> weekly bars (ISO week), dated by the week's last trading day."""
    import datetime
    out, cur, key = [], None, None
    for b in bars:
        d = datetime.date.fromisoformat(b["t"])
        k = d.isocalendar()[:2]
        if k != key:
            if cur:
                out.append(cur)
            cur, key = dict(b), k
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] = (cur["v"] or 0) + (b["v"] or 0)
            cur["t"] = b["t"]
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- pivots and levels

def pivots(h, l, k=5, min_move=None):
    """Swing highs/lows: a bar whose high (low) is the extreme of k bars each side.
    min_move: optional minimum price distance from the previous opposite pivot.
    Returns [(index, price, 'H'|'L')] alternating, oldest first."""
    raw = []
    n = len(h)
    for i in range(k, n - k):
        if h[i] == max(h[i - k:i + k + 1]) and h[i] > max(h[i - k:i] or [h[i] - 1]):
            raw.append((i, h[i], "H"))
        if l[i] == min(l[i - k:i + k + 1]) and l[i] < min(l[i - k:i] or [l[i] + 1]):
            raw.append((i, l[i], "L"))
    raw.sort()
    alt = []
    for p in raw:
        if alt and alt[-1][2] == p[2]:
            if (p[2] == "H" and p[1] > alt[-1][1]) or (p[2] == "L" and p[1] < alt[-1][1]):
                alt[-1] = p
            continue
        if min_move and alt and abs(p[1] - alt[-1][1]) < min_move:
            continue
        alt.append(p)
    return alt


def levels(bars, piv, atr_now, lookback=500, max_each=3):
    """Support/resistance zones from clustered pivots. A zone's strength = touches
    (pivots inside it), with recent touches weighted more."""
    n = len(bars)
    c = bars[-1]["c"]
    start = max(0, n - lookback)
    pts = [(i, p, t) for i, p, t in piv if i >= start]
    tol = max(atr_now * 0.6, c * 0.006)
    clusters = []
    for i, p, t in sorted(pts, key=lambda x: x[1]):
        if clusters and p - clusters[-1]["hi"] <= tol:
            cl = clusters[-1]
            cl["prices"].append(p), cl["idx"].append(i), cl["kinds"].append(t)
            cl["hi"] = max(cl["hi"], p)
        else:
            clusters.append({"prices": [p], "idx": [i], "kinds": [t], "hi": p})
    out = []
    for cl in clusters:
        lvl = statistics.mean(cl["prices"])
        recency = sum(0.5 + 0.5 * (i - start) / max(1, n - start) for i in cl["idx"])
        out.append({"price": round(lvl, 2), "touches": len(cl["prices"]), "strength": round(recency, 2),
                    "last_touch": bars[max(cl["idx"])]["t"], "zone": [round(min(cl["prices"]), 2), round(max(cl["prices"]), 2)]})
    sup = sorted([z for z in out if z["price"] < c], key=lambda z: -z["price"])
    res = sorted([z for z in out if z["price"] > c], key=lambda z: z["price"])
    sup = [z for z in sup if z["touches"] >= 2 or z is sup[0]][:max_each] if sup else []
    res = [z for z in res if z["touches"] >= 2 or z is res[0]][:max_each] if res else []
    return sup, res


def gaps(bars, lookback=120):
    """Unfilled gaps in the last `lookback` bars (later prices never re-entered the gap)."""
    out = []
    n = len(bars)
    for i in range(max(1, n - lookback), n):
        prev, cur = bars[i - 1], bars[i]
        if cur["l"] > prev["h"]:
            lo, hi, kind = prev["h"], cur["l"], "up"
        elif cur["h"] < prev["l"]:
            lo, hi, kind = cur["h"], prev["l"], "down"
        else:
            continue
        later = bars[i + 1:]
        filled = any(b["l"] <= lo for b in later) if kind == "up" else any(b["h"] >= hi for b in later)
        if not filled and (hi - lo) / prev["c"] >= 0.005:
            out.append({"date": cur["t"], "kind": kind, "from": round(lo, 2), "to": round(hi, 2)})
    return out[-5:]


# ---------------------------------------------------------------- candlesticks

def candles(bars, trend_dir):
    """Candlestick patterns on the last bars, read with the prevailing short trend
    (trend_dir: +1 up, -1 down, 0 none). Returns list of dicts."""
    out = []
    if len(bars) < 4:
        return out
    b3, b2, b1 = bars[-3], bars[-2], bars[-1]

    def body(b): return abs(b["c"] - b["o"])
    def rng(b): return max(1e-9, b["h"] - b["l"])
    def upper(b): return b["h"] - max(b["c"], b["o"])
    def lower(b): return min(b["c"], b["o"]) - b["l"]
    def bull(b): return b["c"] > b["o"]
    def bear(b): return b["c"] < b["o"]
    avg_body = statistics.mean(body(b) for b in bars[-15:]) or 1e-9

    def add(name, bias, note):
        out.append({"name": name, "bias": bias, "date": b1["t"], "note": note})

    if body(b1) <= 0.1 * rng(b1):
        add("Doji", "neutral", "Indecision; meaningful only at a level or after an extended move")
    if lower(b1) >= 2 * body(b1) and upper(b1) <= 0.3 * rng(b1) and body(b1) > 0.05 * rng(b1):
        if trend_dir < 0:
            add("Hammer", "bullish", "Long lower shadow after a decline: buyers rejected lower prices")
        elif trend_dir > 0:
            add("Hanging man", "bearish", "Hammer shape after an advance: a warning, needs confirmation")
    if upper(b1) >= 2 * body(b1) and lower(b1) <= 0.3 * rng(b1) and body(b1) > 0.05 * rng(b1):
        if trend_dir > 0:
            add("Shooting star", "bearish", "Long upper shadow after an advance: sellers rejected higher prices")
        elif trend_dir < 0:
            add("Inverted hammer", "bullish", "Long upper shadow after a decline: needs a strong next close")
    if bull(b1) and bear(b2) and b1["c"] >= b2["o"] and b1["o"] <= b2["c"] and body(b1) > body(b2):
        add("Bullish engulfing", "bullish" if trend_dir <= 0 else "neutral", "Today's body swallows yesterday's down body")
    if bear(b1) and bull(b2) and b1["c"] <= b2["o"] and b1["o"] >= b2["c"] and body(b1) > body(b2):
        add("Bearish engulfing", "bearish" if trend_dir >= 0 else "neutral", "Today's body swallows yesterday's up body")
    if bear(b3) and body(b3) > avg_body and body(b2) < 0.5 * avg_body and bull(b1) and b1["c"] > (b3["o"] + b3["c"]) / 2:
        add("Morning star", "bullish", "Three-bar bottom reversal")
    if bull(b3) and body(b3) > avg_body and body(b2) < 0.5 * avg_body and bear(b1) and b1["c"] < (b3["o"] + b3["c"]) / 2:
        add("Evening star", "bearish", "Three-bar top reversal")
    if all(bull(b) and body(b) > 0.6 * avg_body for b in (b3, b2, b1)) and b3["c"] < b2["c"] < b1["c"]:
        add("Three white soldiers", "bullish", "Three strong up closes in a row")
    if all(bear(b) and body(b) > 0.6 * avg_body for b in (b3, b2, b1)) and b3["c"] > b2["c"] > b1["c"]:
        add("Three black crows", "bearish", "Three strong down closes in a row")
    if b1["h"] < b2["h"] and b1["l"] > b2["l"]:
        add("Inside bar", "neutral", "Range inside yesterday's: a break of %.2f / %.2f sets direction" % (b2["h"], b2["l"]))
    if body(b1) >= 0.9 * rng(b1) and body(b1) > 1.5 * avg_body:
        add("Marubozu", "bullish" if bull(b1) else "bearish", "Full-body bar: one side in control all session")
    return out


# ---------------------------------------------------------------- chart patterns

def _close_to(a, b, tol):
    return abs(a - b) <= tol * max(a, b)


def chart_patterns(bars, piv, atr_now, window=180):
    """Rule-based classical patterns on recent pivots. Each: name, bias, status,
    pivots used, key line (neckline/trendlines), measured target."""
    n = len(bars)
    c = bars[-1]["c"]
    out = []
    P = [p for p in piv if p[0] >= n - window]
    H = [p for p in P if p[2] == "H"]
    L = [p for p in P if p[2] == "L"]
    tol = max(0.015, 1.0 * atr_now / c)

    def pt(p): return {"t": bars[p[0]]["t"], "price": round(p[1], 2)}

    # Double top / bottom: last two same-type pivots, similar price, a real trough/peak between
    for kind, S, O in (("top", H, L), ("bottom", L, H)):
        if len(S) >= 2:
            a, b = S[-2], S[-1]
            mid = [o for o in O if a[0] < o[0] < b[0]]
            if mid and b[0] - a[0] >= 10 and _close_to(a[1], b[1], tol):
                m = min(mid, key=lambda o: o[1]) if kind == "top" else max(mid, key=lambda o: o[1])
                depth = abs(max(a[1], b[1]) - m[1]) / m[1] if kind == "top" else abs(m[1] - min(a[1], b[1])) / m[1]
                if depth >= 0.03:
                    neck = m[1]
                    height = (max(a[1], b[1]) - neck) if kind == "top" else (neck - min(a[1], b[1]))
                    after = [x["c"] for x in bars[b[0] + 1:]]
                    if kind == "top":
                        status = "confirmed" if any(x < neck for x in after) else "forming"
                        if c > max(a[1], b[1]) * (1 + 0.5 * tol):
                            status = "failed"           # price went back above both peaks
                        target = neck - height
                    else:
                        status = "confirmed" if any(x > neck for x in after) else "forming"
                        if c < min(a[1], b[1]) * (1 - 0.5 * tol):
                            status = "failed"           # price went back below both bottoms
                        target = neck + height
                    out.append({"name": "Double " + kind, "bias": "bearish" if kind == "top" else "bullish",
                                "status": status, "points": [pt(a), pt(m), pt(b)],
                                "line": {"label": "neckline", "price": round(neck, 2)},
                                "target": round(target, 2),
                                "note": "Neckline %.2f; %s" % (neck, {"forming": "not yet broken", "confirmed": "closed beyond the neckline",
                                         "failed": "price has gone back beyond the %s, so the pattern no longer holds" % ("peaks" if kind == "top" else "bottoms")}[status])})

    # Head and shoulders (top) and inverse
    for inv, S, O in ((False, H, L), (True, L, H)):
        if len(S) >= 3:
            ls, hd, rs = S[-3], S[-2], S[-1]
            if inv:
                ok = hd[1] < ls[1] * (1 - 0.03) and hd[1] < rs[1] * (1 - 0.03) and _close_to(ls[1], rs[1], 0.04)
            else:
                ok = hd[1] > ls[1] * 1.03 and hd[1] > rs[1] * 1.03 and _close_to(ls[1], rs[1], 0.04)
            t1 = [o for o in O if ls[0] < o[0] < hd[0]]
            t2 = [o for o in O if hd[0] < o[0] < rs[0]]
            if ok and t1 and t2:
                n1 = (max if inv else min)(t1, key=lambda o: o[1])
                n2 = (max if inv else min)(t2, key=lambda o: o[1])
                slope = (n2[1] - n1[1]) / max(1, n2[0] - n1[0])
                neck_now = n2[1] + slope * (n - 1 - n2[0])
                height = abs(hd[1] - (n1[1] + slope * (hd[0] - n1[0])))
                after = bars[rs[0] + 1:]
                broke = any((x["c"] > n2[1] + slope * (j + rs[0] + 1 - n2[0])) if inv else
                            (x["c"] < n2[1] + slope * (j + rs[0] + 1 - n2[0])) for j, x in enumerate(after))
                status = "confirmed" if broke else "forming"
                if (not inv and c > hd[1]) or (inv and c < hd[1]):
                    status = "failed"                   # price went beyond the head
                out.append({"name": "Inverse head & shoulders" if inv else "Head & shoulders",
                            "bias": "bullish" if inv else "bearish", "status": status,
                            "points": [pt(ls), pt(n1), pt(hd), pt(n2), pt(rs)],
                            "line": {"label": "neckline", "from": pt(n1), "to": {"t": bars[-1]["t"], "price": round(neck_now, 2)}},
                            "target": round(neck_now + height if inv else neck_now - height, 2),
                            "note": "Neckline now about %.2f" % neck_now})

    # Triangles / channels from the last 3+ highs and lows over the window
    if len(H) >= 3 and len(L) >= 3:
        hh, ll = H[-4:], L[-4:]
        bh, ah = linreg([p[0] for p in hh], [p[1] for p in hh])
        bl, al = linreg([p[0] for p in ll], [p[1] for p in ll])
        span = max(hh[-1][0], ll[-1][0]) - min(hh[0][0], ll[0][0])
        flat = 0.0004 * c          # per-bar slope counted as flat (~0.04% of price a bar)
        up_now, lo_now = ah + bh * (n - 1), al + bl * (n - 1)
        name = None
        if abs(bh) <= flat and bl > flat:
            name, bias = "Ascending triangle", "bullish"
        elif bh < -flat and abs(bl) <= flat:
            name, bias = "Descending triangle", "bearish"
        elif bh < -flat and bl > flat:
            name, bias = "Symmetrical triangle", "neutral"
        elif bh > flat and bl > flat and abs(bh - bl) <= 0.5 * max(abs(bh), abs(bl)):
            name, bias = "Rising channel", "bullish"
        elif bh < -flat and bl < -flat and abs(bh - bl) <= 0.5 * max(abs(bh), abs(bl)):
            name, bias = "Falling channel", "bearish"
        elif abs(bh) <= flat and abs(bl) <= flat:
            name, bias = "Horizontal range", "neutral"
        if name and span >= 20 and up_now > lo_now:
            if c > up_now + 0.3 * atr_now:
                status, bias2 = "breakout up", "bullish"
            elif c < lo_now - 0.3 * atr_now:
                status, bias2 = "breakdown", "bearish"
            else:
                status, bias2 = "inside", bias
            height = (hh[0][1] - ll[0][1]) if "triangle" in name.lower() else (up_now - lo_now)
            target = (up_now + height) if status == "breakout up" else (lo_now - height) if status == "breakdown" else None
            x0 = min(hh[0][0], ll[0][0])
            out.append({"name": name, "bias": bias2, "status": status,
                        "points": [pt(p) for p in sorted(hh + ll)],
                        "lines": [{"label": "upper", "from": {"t": bars[x0]["t"], "price": round(ah + bh * x0, 2)},
                                   "to": {"t": bars[-1]["t"], "price": round(up_now, 2)}},
                                  {"label": "lower", "from": {"t": bars[x0]["t"], "price": round(al + bl * x0, 2)},
                                   "to": {"t": bars[-1]["t"], "price": round(lo_now, 2)}}],
                        "target": round(target, 2) if target else None,
                        "note": "Upper line %.2f, lower line %.2f today" % (up_now, lo_now)})

    # Flag: strong pole then tight counter-move
    if n > 40:
        for pole in (10, 15, 20):
            for flag in (5, 8, 12):
                s = n - 1 - flag - pole
                if s < 0:
                    continue
                p0, p1 = bars[s]["c"], bars[n - 1 - flag]["c"]
                move = (p1 - p0) / p0
                fh = max(b["h"] for b in bars[n - flag:])
                fl = min(b["l"] for b in bars[n - flag:])
                if abs(move) >= 0.10 and (fh - fl) <= 0.5 * abs(p1 - p0):
                    up = move > 0
                    drift = (bars[-1]["c"] - p1) / p1
                    if (up and -0.06 <= drift <= 0.01) or (not up and -0.01 <= drift <= 0.06):
                        out.append({"name": "Bull flag" if up else "Bear flag", "bias": "bullish" if up else "bearish",
                                    "status": "forming", "points": [{"t": bars[s]["t"], "price": round(p0, 2)},
                                                                     {"t": bars[n - 1 - flag]["t"], "price": round(p1, 2)}],
                                    "line": {"label": "flag high" if up else "flag low", "price": round(fh if up else fl, 2)},
                                    "target": round((fh + (p1 - p0)) if up else (fl - (p0 - p1)), 2),
                                    "note": "%+.0f%% pole over %d bars, %d-bar consolidation" % (move * 100, pole, flag)})
                        break
            else:
                continue
            break
    return out


# ---------------------------------------------------------------- fibonacci

def fibonacci(bars, piv, lookback=250):
    n = len(bars)
    P = [p for p in piv if p[0] >= n - lookback]
    if len(P) < 2:
        return None
    hi = max(P, key=lambda p: p[1])
    lo = min(P, key=lambda p: p[1])
    if hi[0] == lo[0]:
        return None
    up = lo[0] < hi[0]          # swing from low to high = retracing an advance
    a, b = (lo[1], hi[1]) if up else (hi[1], lo[1])
    lv = {}
    for r in (0.236, 0.382, 0.5, 0.618, 0.786):
        lv["%.1f%%" % (r * 100)] = round(b - (b - a) * r, 2)
    c = bars[-1]["c"]
    retr = (b - c) / (b - a) * 100 if b != a else 0
    return {"swing": "advance" if up else "decline", "from": {"t": bars[(lo if up else hi)[0]]["t"], "price": round(a, 2)},
            "to": {"t": bars[(hi if up else lo)[0]]["t"], "price": round(b, 2)}, "levels": lv,
            "retraced_pct": round(retr, 1)}


# ---------------------------------------------------------------- signal definitions (for track records)

def ichimoku(h, l, c, tk=9, kj=26, sb=52, shift=26):
    """Raw Ichimoku lines, NOT shifted: tenkan, kijun, span_a, span_b per bar.
    On a chart, span_a/span_b of bar i are drawn `shift` bars ahead; chikou is close i drawn `shift` back.
    The cloud under the CURRENT bar i is therefore span_a[i - shift], span_b[i - shift]."""
    n = len(c)

    def mid(k, i):
        if i < k - 1:
            return None
        return (max(h[i - k + 1:i + 1]) + min(l[i - k + 1:i + 1])) / 2.0
    ten = [mid(tk, i) for i in range(n)]
    kij = [mid(kj, i) for i in range(n)]
    spa = [(a + b) / 2.0 if a is not None and b is not None else None for a, b in zip(ten, kij)]
    spb = [mid(sb, i) for i in range(n)]
    return {"tenkan": ten, "kijun": kij, "span_a": spa, "span_b": spb, "shift": shift}


def cloud_at(ich, i):
    """(top, bottom) of the cloud under bar i, or None."""
    j = i - ich["shift"]
    if j < 0:
        return None
    a, b = ich["span_a"][j], ich["span_b"][j]
    if a is None or b is None:
        return None
    return max(a, b), min(a, b)


def ichimoku_block(bars, ich):
    """Plain reading of Ichimoku on the last bar. Reported on its own; it is NOT part of the
    verdict score (the verdict's calibration was measured without it)."""
    n = len(bars)
    c = bars[-1]["c"]
    ten, kij = ich["tenkan"][-1], ich["kijun"][-1]
    cl = cloud_at(ich, n - 1)
    ev, score = [], 0
    if cl is None:
        return {"position": "n/a", "tk": "n/a", "future_cloud": "n/a", "chikou": "n/a", "score": 0,
                "evidence": ["Not enough history for Ichimoku (needs about 78 bars)"]}
    top, bot = cl
    pos = "above" if c > top else "below" if c < bot else "inside"
    score += 1 if pos == "above" else -1 if pos == "below" else 0
    ev.append("Price %.2f is %s the cloud (%.2f to %.2f)" % (c, {"above": "above", "below": "below",
              "inside": "inside"}[pos], bot, top))
    tk = "n/a"
    if ten is not None and kij is not None:
        tk = "bullish" if ten > kij else "bearish" if ten < kij else "flat"
        score += 1 if tk == "bullish" else -1 if tk == "bearish" else 0
        last_x = None
        sgn = lambda i: (ich["tenkan"][i] > ich["kijun"][i]) - (ich["tenkan"][i] < ich["kijun"][i])
        now_s = sgn(n - 1)
        if now_s:
            for i in range(n - 2, -1, -1):          # walk back to the last bar on the other side
                if ich["tenkan"][i] is None or ich["kijun"][i] is None:
                    break
                if sgn(i) == -now_s:
                    last_x = (bars[i + 1]["t"], "bullish" if now_s > 0 else "bearish")
                    break
        ev.append("Tenkan (9) %.2f is %s Kijun (26) %.2f%s" % (ten, "above" if ten > kij else "below" if ten < kij else "level with",
                  kij, ("; last %s cross on %s" % (last_x[1], last_x[0])) if last_x else ""))
    fa, fb = ich["span_a"][-1], ich["span_b"][-1]
    fut = "n/a"
    if fa is not None and fb is not None:
        fut = "bullish" if fa > fb else "bearish" if fa < fb else "flat"
        ev.append("The cloud ahead (next %d bars) is %s: Span A %.2f vs Span B %.2f" % (
            ich["shift"], {"bullish": "bullish (A above B)", "bearish": "bearish (A below B)", "flat": "flat"}[fut], fa, fb))
    chik = "n/a"
    if n > ich["shift"]:
        past = bars[-1 - ich["shift"]]["c"]
        chik = "above" if c > past else "below"
        ev.append("Lagging line (today's close vs %d bars ago, %.2f) is %s" % (ich["shift"], past, chik))
    return {"position": pos, "tk": tk, "future_cloud": fut, "chikou": chik, "score": score,
            "cloud_top": round(top, 2), "cloud_bottom": round(bot, 2), "evidence": ev}


def future_dates(bars, k):
    """k future bar dates after the last bar (weekdays for daily bars, +7 days for weekly)."""
    import datetime as _dt
    ds = [_dt.date.fromisoformat(b["t"][:10]) for b in bars[-12:]]
    gaps = sorted((b - a).days for a, b in zip(ds, ds[1:])) or [1]
    weeklyish = gaps[len(gaps) // 2] >= 5
    d, out = ds[-1], []
    while len(out) < k:
        d += _dt.timedelta(days=7 if weeklyish else 1)
        if weeklyish or d.weekday() < 5:
            out.append(d.isoformat())
    return out


def signal_series(ind, bars):
    """{name: [bool per bar]} — the bar on which each signal FIRES."""
    c = [b["c"] for b in bars]
    n = len(c)
    s50, s200, r, (ml, msig, _), st = ind["sma50"], ind["sma200"], ind["rsi"], ind["macd"], ind["st_dir"]
    bbw = ind["bb_width"]
    hi252 = ind["hi252"]
    lo252 = ind["lo252"]

    def cross_up(a, b, i): return all(v is not None for v in (a[i], b[i], a[i - 1], b[i - 1])) and a[i - 1] <= b[i - 1] and a[i] > b[i]
    def cross_dn(a, b, i): return all(v is not None for v in (a[i], b[i], a[i - 1], b[i - 1])) and a[i - 1] >= b[i - 1] and a[i] < b[i]
    out = {k: [False] * n for k in ("Golden cross (50 above 200)", "Death cross (50 below 200)",
                                     "Close crosses above 200-day", "Close crosses below 200-day",
                                     "RSI back above 30", "RSI drops below 70", "MACD bullish cross",
                                     "MACD bearish cross", "Supertrend turns up", "Supertrend turns down",
                                     "New 52-week high", "New 52-week low", "Bollinger squeeze breakout up",
                                     "Bollinger squeeze breakdown", "Tenkan crosses above Kijun",
                                     "Tenkan crosses below Kijun", "Close breaks above the cloud",
                                     "Close breaks below the cloud")}
    ich = ind.get("ichi")
    c_s = c
    for i in range(1, n):
        if cross_up(s50, s200, i): out["Golden cross (50 above 200)"][i] = True
        if cross_dn(s50, s200, i): out["Death cross (50 below 200)"][i] = True
        if cross_up(c_s, s200, i): out["Close crosses above 200-day"][i] = True
        if cross_dn(c_s, s200, i): out["Close crosses below 200-day"][i] = True
        if r[i] is not None and r[i - 1] is not None:
            if r[i - 1] < 30 <= r[i]: out["RSI back above 30"][i] = True
            if r[i - 1] > 70 >= r[i]: out["RSI drops below 70"][i] = True
        if cross_up(ml, msig, i): out["MACD bullish cross"][i] = True
        if cross_dn(ml, msig, i): out["MACD bearish cross"][i] = True
        if st[i] is not None and st[i - 1] is not None:
            if st[i - 1] == -1 and st[i] == 1: out["Supertrend turns up"][i] = True
            if st[i - 1] == 1 and st[i] == -1: out["Supertrend turns down"][i] = True
        if hi252[i - 1] is not None and c[i] > hi252[i - 1]: out["New 52-week high"][i] = True
        if lo252[i - 1] is not None and c[i] < lo252[i - 1]: out["New 52-week low"][i] = True
        if i >= 1 and bbw[i - 1] is not None and ind["bbw_pct"][i - 1] is not None and ind["bbw_pct"][i - 1] <= 10:
            if ind["bb_up"][i] is not None and c[i] > ind["bb_up"][i]: out["Bollinger squeeze breakout up"][i] = True
            if ind["bb_lo"][i] is not None and c[i] < ind["bb_lo"][i]: out["Bollinger squeeze breakdown"][i] = True
        if ich:
            if cross_up(ich["tenkan"], ich["kijun"], i): out["Tenkan crosses above Kijun"][i] = True
            if cross_dn(ich["tenkan"], ich["kijun"], i): out["Tenkan crosses below Kijun"][i] = True
            c0, c1 = cloud_at(ich, i - 1), cloud_at(ich, i)
            if c0 and c1:
                if c[i - 1] <= c0[0] and c[i] > c1[0]: out["Close breaks above the cloud"][i] = True
                if c[i - 1] >= c0[1] and c[i] < c1[1]: out["Close breaks below the cloud"][i] = True
    return out


SIGNAL_BIAS = {"Golden cross (50 above 200)": 1, "Death cross (50 below 200)": -1, "Close crosses above 200-day": 1,
               "Close crosses below 200-day": -1, "RSI back above 30": 1, "RSI drops below 70": -1,
               "MACD bullish cross": 1, "MACD bearish cross": -1, "Supertrend turns up": 1, "Supertrend turns down": -1,
               "New 52-week high": 1, "New 52-week low": -1, "Bollinger squeeze breakout up": 1,
               "Bollinger squeeze breakdown": -1, "Tenkan crosses above Kijun": 1, "Tenkan crosses below Kijun": -1,
               "Close breaks above the cloud": 1, "Close breaks below the cloud": -1}


def track_record(bars, fires, horizons=(5, 20, 60), recent=10):
    """For one signal: forward returns after each past firing (non-overlapping per horizon)
    vs the security's ordinary forward returns over the same history."""
    c = [b["c"] for b in bars]
    n = len(c)
    res = {"occurrences": sum(fires), "active": any(fires[-recent:]),
           "last": next((bars[i]["t"] for i in range(n - 1, -1, -1) if fires[i]), None), "h": {}}
    for hz in horizons:
        base = [c[i + hz] / c[i] - 1 for i in range(0, n - hz)]
        ev, last_i = [], -10 ** 9
        for i in range(n - hz):
            if fires[i] and i - last_i >= hz:
                ev.append(c[i + hz] / c[i] - 1)
                last_i = i
        if not ev or not base:
            res["h"][hz] = {"n": len(ev)}
            continue
        res["h"][hz] = {"n": len(ev), "median_pct": round(statistics.median(ev) * 100, 2),
                        "up_pct": round(100 * sum(1 for x in ev if x > 0) / len(ev)),
                        "base_median_pct": round(statistics.median(base) * 100, 2),
                        "base_up_pct": round(100 * sum(1 for x in base if x > 0) / len(base)),
                        "edge_pct": round((statistics.median(ev) - statistics.median(base)) * 100, 2)}
    return res


def judge(tr, bias):
    """Plain-English reading of a track record at the 20-day horizon."""
    h = tr["h"].get(20) or {}
    n = h.get("n", 0)
    if n < 8:
        return "too few past cases (%d) to judge" % n
    e = h["edge_pct"] * bias
    hit = h["up_pct"] if bias > 0 else 100 - h["up_pct"]
    base_hit = h["base_up_pct"] if bias > 0 else 100 - h["base_up_pct"]
    if e > 1.0 and hit >= base_hit + 5:
        return "has worked on this stock (%d cases)" % n
    if e < -1.0:
        return "has worked AGAINST its usual meaning here (%d cases)" % n
    return "no better than an ordinary day on this stock (%d cases)" % n


# ---------------------------------------------------------------- scorecard (for the exported report)

SCORECARD_SIGNALS = {
    "Trend (daily)": ["Golden cross (50 above 200)", "Death cross (50 below 200)", "Close crosses above 200-day",
                      "Close crosses below 200-day"],
    "Moving averages": ["Close crosses above 200-day", "Close crosses below 200-day", "Golden cross (50 above 200)",
                        "Death cross (50 below 200)"],
    "Ichimoku": ["Tenkan crosses above Kijun", "Tenkan crosses below Kijun", "Close breaks above the cloud",
                 "Close breaks below the cloud"],
    "RSI": ["RSI back above 30", "RSI drops below 70"],
    "MACD": ["MACD bullish cross", "MACD bearish cross"],
    "Supertrend": ["Supertrend turns up", "Supertrend turns down"],
    "Bollinger Bands": ["Bollinger squeeze breakout up", "Bollinger squeeze breakdown"],
    "52-week position": ["New 52-week high", "New 52-week low"],
}


def _reading(score, cap):
    """Map a signed score onto 0-10 (5 = neutral, 10 = strongest bullish reading, 0 = strongest bearish)."""
    if score is None or not cap:
        return None
    x = max(-1.0, min(1.0, score / float(cap)))
    return round(5 + 5 * x, 1)


def _reliability(names, signals):
    """Has this indicator's own signal meant anything on this security? Summarised from the track record."""
    rel = [sg for sg in signals if sg["name"] in names]
    if not rel:
        return {"label": "Not tested", "detail": "No signal of this indicator is tracked here", "signals": []}
    tags = []
    for sg in rel:
        r = sg["reading"]
        tag = "worked" if r.startswith("has worked on") else "against" if "AGAINST" in r else \
              "few" if r.startswith("too few") else "ordinary"
        tags.append((sg["name"], tag, (sg["h"].get(20) or {}).get("n", 0)))
    kinds = {t for _, t, _ in tags}
    if "worked" in kinds and "against" not in kinds:
        label = "Has worked here"
    elif "against" in kinds and "worked" not in kinds:
        label = "Worked against its meaning"
    elif "worked" in kinds and "against" in kinds:
        label = "Mixed"
    elif "ordinary" in kinds:
        label = "No better than ordinary"
    else:
        label = "Too few cases"
    word = {"worked": "worked", "against": "worked against", "few": "too few cases", "ordinary": "no better than ordinary"}
    detail = "; ".join("%s: %s (%d)" % (n, word[t], k) for n, t, k in tags)
    return {"label": label, "detail": detail, "signals": [n for n, _, _ in tags]}


def scorecard(bars, ind, trend, weekly_view, mom, vlm, rel, ichi_view, pats, cdl, signals, weekly_tf=False):
    """One row per indicator: a 0-10 READING (how bullish or bearish it looks now) and a separate
    RELIABILITY (whether its signals have beaten an ordinary day on this security). Deliberately no
    total: the combined verdict has not forecast returns in calibration."""
    c = bars[-1]["c"]
    rows = []

    def add(name, score, cap, now, rel_names=None, override=None):
        rows.append({"indicator": name, "reading": override if override is not None else _reading(score, cap),
                     "now": now, "reliability": _reliability(rel_names or SCORECARD_SIGNALS.get(name, []), signals)})

    add("Trend (daily)" if not weekly_tf else "Trend (weekly)", trend["score"], 5,
        "Long %s, intermediate %s, short %s" % (trend.get("long"), trend.get("intermediate"), trend.get("short")),
        SCORECARD_SIGNALS["Trend (daily)"])
    if weekly_view and not weekly_tf:
        add("Weekly trend", weekly_view["score"], 5, "Long %s, intermediate %s, short %s" % (
            weekly_view.get("long"), weekly_view.get("intermediate"), weekly_view.get("short")), [])
    e20, e50, s200 = ind["ema20"][-1], ind["ema50"][-1], ind["sma200"][-1]
    ma = [x for x in ((c > e20) if e20 else None, (c > e50) if e50 else None, (c > s200) if s200 else None,
                      (e20 > e50) if e20 and e50 else None) if x is not None]
    if ma:
        add("Moving averages", sum(1 if x else -1 for x in ma), len(ma),
            "Price %s EMA 20, %s EMA 50, %s SMA 200; EMA 20 %s EMA 50" % (
                "above" if e20 and c > e20 else "below", "above" if e50 and c > e50 else "below",
                "above" if s200 and c > s200 else "below" if s200 else "n/a", "above" if e20 and e50 and e20 > e50 else "below"))
    if ichi_view.get("position") not in (None, "n/a"):
        ich_s = ichi_view["score"] + {"bullish": 1, "bearish": -1}.get(ichi_view.get("future_cloud"), 0) + \
            {"above": 1, "below": -1}.get(ichi_view.get("chikou"), 0)
        add("Ichimoku", ich_s, 4, "%s the cloud; Tenkan %s Kijun; cloud ahead %s" % (
            ichi_view["position"].capitalize(), "over" if ichi_view["tk"] == "bullish" else "under" if ichi_view["tk"] == "bearish" else "level with",
            {"bullish": "green", "bearish": "red"}.get(ichi_view.get("future_cloud"), "flat")))
    r = ind["rsi"][-1]
    if r is not None:
        add("RSI", None, None, "RSI 14 at %.1f%s" % (r, " (above 70: stretched)" if r > 70 else " (below 30: washed out)" if r < 30 else ""),
            override=round(r / 10.0, 1))
    ml, ms, mh = ind["macd"]
    if ml[-1] is not None and ms[-1] is not None:
        m_s = (1 if ml[-1] > ms[-1] else -1) + (1 if ml[-1] > 0 else -1) + \
            ((1 if mh[-1] > mh[-2] else -1) if mh[-1] is not None and mh[-2] is not None else 0)
        add("MACD", m_s, 3, "MACD %s its signal line and %s zero; histogram %s" % (
            "above" if ml[-1] > ms[-1] else "below", "above" if ml[-1] > 0 else "below",
            "rising" if mh[-1] is not None and mh[-2] is not None and mh[-1] > mh[-2] else "falling"))
    if ind["st"][-1] is not None:
        up = c > ind["st"][-1]
        add("Supertrend", 1 if up else -1, 2, "Price %s Supertrend (%.2f)" % ("above" if up else "below", ind["st"][-1]))
    bu, bl = ind["bb_up"][-1], ind["bb_lo"][-1]
    if bu is not None and bl is not None and bu > bl:
        pb = (c - bl) / (bu - bl)
        add("Bollinger Bands", None, None, "Price at %.0f%% of the band width (0 = lower band, 100 = upper)" % (pb * 100),
            override=round(max(0.0, min(10.0, pb * 10)), 1))
    if vlm.get("volume_ratio") is not None or vlm["score"]:
        add("Volume", vlm["score"], 3, "Last volume %sx its average" % vlm.get("volume_ratio", "–"), [])
    rel_s = {"rising": 1, "falling": -1}.get(rel.get("rs_trend"), 0) + \
        sum(1 if x > 0 else -1 for x in (rel.get("vs_nifty") or {}).values())
    if rel.get("rs_trend") or rel.get("vs_nifty"):
        add("Relative strength", rel_s, 1 + len(rel.get("vs_nifty") or {}), "Strength line %s; vs Nifty %s" % (
            rel.get("rs_trend", "n/a"), ", ".join("%s %+.1f pts" % (k, v) for k, v in (rel.get("vs_nifty") or {}).items()) or "n/a"), [])
    import datetime as _dt
    cut = (_dt.date.fromisoformat(bars[-1]["t"][:10]) - _dt.timedelta(days=365)).isoformat()
    yb = [b for b in bars if b["t"] > cut]
    hi, lo = max(b["h"] for b in yb), min(b["l"] for b in yb)
    if hi > lo:
        add("52-week position", None, None, "%.0f%% of the way from the 52-week low (%.2f) to the high (%.2f)" % (
            (c - lo) / (hi - lo) * 100, lo, hi), override=round((c - lo) / (hi - lo) * 10, 1))
    live = [p for p in pats if p["status"] != "failed"]
    if live:
        ps = sum(1 if p["bias"] == "bullish" else -1 if p["bias"] == "bearish" else 0 for p in live)
        add("Chart patterns", ps, 2, "; ".join("%s (%s)" % (p["name"], p["status"]) for p in live), [])
        rows[-1]["reliability"] = {"label": "No record kept", "detail": "Pattern success is not tracked by this tool", "signals": []}
    if cdl:
        cs = 1 if cdl[0]["bias"] == "bullish" else -1 if cdl[0]["bias"] == "bearish" else 0
        add("Candles", cs, 2, "%s (%s)" % (cdl[0]["name"], cdl[0]["bias"]), [])
        rows[-1]["reliability"] = {"label": "No record kept", "detail": "Candle success is not tracked by this tool", "signals": []}
    return rows


# ---------------------------------------------------------------- full analysis

# Indicator settings. Keys keep their historical names (ema20 = the fast EMA, sma200 = the slow SMA, ...)
# so every rule downstream works unchanged; app.py relabels the text when settings differ from these.
DEFAULTS = {"ema_fast": 20, "ema_slow": 50, "sma_fast": 50, "sma_slow": 200, "bb_n": 20, "bb_k": 2.0,
            "rsi_n": 14, "macd_fast": 12, "macd_slow": 26, "macd_sig": 9, "stoch_n": 14, "stoch_d": 3,
            "st_n": 10, "st_mult": 3.0, "atr_n": 14, "adx_n": 14, "ichi_tk": 9, "ichi_kj": 26, "ichi_sb": 52}
LIMITS = {"bb_k": (0.5, 5.0), "st_mult": (0.5, 10.0)}           # floats; every other setting is an integer 2..400


def clean_params(raw):
    """Validated settings: unknown keys dropped, out-of-range values clamped, defaults filled in."""
    out = dict(DEFAULTS)
    for k, v in (raw or {}).items():
        if k not in DEFAULTS:
            continue
        try:
            if k in LIMITS:
                lo, hi = LIMITS[k]
                out[k] = round(min(hi, max(lo, float(v))), 2)
            else:
                out[k] = int(min(400, max(2, int(float(v)))))
        except (TypeError, ValueError):
            pass
    if out["ema_fast"] >= out["ema_slow"]:
        out["ema_fast"], out["ema_slow"] = DEFAULTS["ema_fast"], DEFAULTS["ema_slow"]
    if out["sma_fast"] >= out["sma_slow"]:
        out["sma_fast"], out["sma_slow"] = DEFAULTS["sma_fast"], DEFAULTS["sma_slow"]
    if out["macd_fast"] >= out["macd_slow"]:
        out["macd_fast"], out["macd_slow"] = DEFAULTS["macd_fast"], DEFAULTS["macd_slow"]
    if not out["ichi_tk"] < out["ichi_kj"] < out["ichi_sb"]:
        out["ichi_tk"], out["ichi_kj"], out["ichi_sb"] = DEFAULTS["ichi_tk"], DEFAULTS["ichi_kj"], DEFAULTS["ichi_sb"]
    return out


def indicators(bars, params=None):
    P = clean_params(params)
    o, h, l, c, v = ([b[k] for b in bars] for k in "ohlcv")
    v = [x or 0 for x in v]
    ind = {"sma20": sma(c, 20), "ema20": ema(c, P["ema_fast"]), "sma50": sma(c, P["sma_fast"]),
           "sma200": sma(c, P["sma_slow"]), "rsi": rsi(c, P["rsi_n"]),
           "macd": macd(c, P["macd_fast"], P["macd_slow"], P["macd_sig"]), "atr": atr(h, l, c, P["atr_n"]),
           "obv": obv(c, v)}
    ind["bb_mid"], ind["bb_up"], ind["bb_lo"] = bollinger(c, P["bb_n"], P["bb_k"])
    ind["adx"], ind["pdi"], ind["ndi"] = adx(h, l, c, P["adx_n"])
    ind["stoch_k"], ind["stoch_d"] = stochastic(h, l, c, P["stoch_n"], P["stoch_d"])
    ind["st"], ind["st_dir"] = supertrend(h, l, c, P["st_n"], P["st_mult"])
    ind["vol20"] = sma(v, 20)
    ind["bb_width"] = [((u - lo) / m * 100) if u is not None and m else None
                       for u, lo, m in zip(ind["bb_up"], ind["bb_lo"], ind["bb_mid"])]
    # Bollinger width percentile over the trailing year
    pct = [None] * len(c)
    for i in range(len(c)):
        w = [x for x in ind["bb_width"][max(0, i - 251):i + 1] if x is not None]
        if len(w) >= 120 and ind["bb_width"][i] is not None:
            pct[i] = 100.0 * sum(1 for x in w if x <= ind["bb_width"][i]) / len(w)
    ind["bbw_pct"] = pct
    ind["hi252"] = [max(h[max(0, i - 251):i + 1]) if i >= 251 else None for i in range(len(c))]
    ind["lo252"] = [min(l[max(0, i - 251):i + 1]) if i >= 251 else None for i in range(len(c))]
    ind["ema50"] = ema(c, P["ema_slow"])
    ind["ichi"] = ichimoku(h, l, c, P["ichi_tk"], P["ichi_kj"], P["ichi_sb"], P["ichi_kj"])
    ind["params"] = P
    return ind


def trend_block(bars, ind, piv):
    c = bars[-1]["c"]
    s50, s200, e20 = ind["sma50"][-1], ind["sma200"][-1], ind["ema20"][-1]
    ev = []
    score = 0
    if s200 is not None:
        sl = slope_pct(ind["sma200"], 20)
        above = c > s200
        rising = sl is not None and sl > 0
        score += 2 if above and rising else 1 if above else -2 if not above and not rising else -1
        ev.append(("long", "Price %s the 200-day average (%.2f), which is %s" % (
            "above" if above else "below", s200, "rising" if rising else "falling")))
        long_t = "up" if above and rising else "down" if not above and not rising else "mixed"
    else:
        long_t = "n/a"
    if s50 is not None and s200 is not None:
        gc = [i for i in range(len(bars) - 1, 0, -1) if ind["sma50"][i - 1] is not None and ind["sma200"][i - 1] is not None
              and (ind["sma50"][i - 1] - ind["sma200"][i - 1]) * (ind["sma50"][i] - ind["sma200"][i]) < 0]
        if gc:
            ev.append(("long", "Last %s cross on %s" % ("golden" if s50 > s200 else "death", bars[gc[0]]["t"])))
    # swing structure
    hs = [p for p in piv if p[2] == "H"][-2:]
    ls = [p for p in piv if p[2] == "L"][-2:]
    struct = "n/a"
    if len(hs) == 2 and len(ls) == 2:
        hh, hl = hs[1][1] > hs[0][1], ls[1][1] > ls[0][1]
        struct = ("higher highs and higher lows" if hh and hl else "lower highs and lower lows" if not hh and not hl
                  else "higher highs, lower lows (widening)" if hh else "lower highs, higher lows (narrowing)")
        score += 1 if hh and hl else -1 if not hh and not hl else 0
    inter = "n/a"
    if s50 is not None:
        sl50 = slope_pct(ind["sma50"], 10)
        inter = "up" if c > s50 and (sl50 or 0) > 0 else "down" if c < s50 and (sl50 or 0) < 0 else "sideways"
        score += 1 if inter == "up" else -1 if inter == "down" else 0
        ev.append(("intermediate", "Price %s the 50-day (%.2f); swings show %s" % ("above" if c > s50 else "below", s50, struct)))
    stdir = ind["st_dir"][-1]
    short = "n/a"
    if e20 is not None:
        short = "up" if c > e20 and stdir == 1 else "down" if c < e20 and stdir == -1 else "mixed"
        score += 1 if short == "up" else -1 if short == "down" else 0
        ev.append(("short", "Price %s the 20-day EMA (%.2f); Supertrend %s at %.2f" % (
            "above" if c > e20 else "below", e20, "up" if stdir == 1 else "down", ind["st"][-1] or 0)))
    a = ind["adx"][-1]
    strength = "n/a"
    if a is not None:
        strength = "strong trend" if a >= 25 else "developing" if a >= 20 else "weak / ranging"
        ev.append(("strength", "ADX %.0f (%s); +DI %.0f vs -DI %.0f" % (a, strength, ind["pdi"][-1] or 0, ind["ndi"][-1] or 0)))
    return {"long": long_t, "intermediate": inter, "short": short, "structure": struct, "adx": round(a, 1) if a else None,
            "strength": strength, "score": score, "evidence": [e for _, e in ev]}


def momentum_block(bars, ind, piv):
    r, (ml, ms, mh) = ind["rsi"], ind["macd"]
    c = [b["c"] for b in bars]
    out = {"evidence": [], "score": 0}
    if r[-1] is not None:
        zone = "overbought (>70)" if r[-1] > 70 else "oversold (<30)" if r[-1] < 30 else "bullish zone (>50)" if r[-1] >= 50 else "bearish zone (<50)"
        out["rsi"] = round(r[-1], 1)
        out["evidence"].append("RSI(14) %.1f: %s" % (r[-1], zone))
        out["score"] += 1 if 50 <= r[-1] <= 70 else -1 if 30 <= r[-1] < 50 else 0
        # divergence on the last two pivots of each kind (within ~60 bars)
        for kind, label in (("H", "bearish"), ("L", "bullish")):
            ps = [p for p in piv if p[2] == kind and p[0] >= len(c) - 90][-2:]
            if len(ps) == 2 and r[ps[0][0]] is not None and r[ps[1][0]] is not None:
                (i0, p0, _), (i1, p1, _) = ps
                if kind == "H" and p1 > p0 and r[i1] < r[i0] - 2:
                    out["evidence"].append("Bearish RSI divergence: price high %s above %s, RSI lower" % (bars[i1]["t"], bars[i0]["t"]))
                    out["score"] -= 1
                    out["divergence"] = "bearish"
                if kind == "L" and p1 < p0 and r[i1] > r[i0] + 2:
                    out["evidence"].append("Bullish RSI divergence: price low %s below %s, RSI higher" % (bars[i1]["t"], bars[i0]["t"]))
                    out["score"] += 1
                    out["divergence"] = "bullish"
    if ml[-1] is not None and ms[-1] is not None:
        above = ml[-1] > ms[-1]
        cross = next((len(c) - 1 - k for k in range(1, 6) if ml[-k - 1] is not None and ms[-k - 1] is not None and
                      (ml[-k - 1] - ms[-k - 1]) * (ml[-k] - ms[-k]) < 0), None)
        expanding = mh[-1] is not None and mh[-2] is not None and abs(mh[-1]) > abs(mh[-2])
        out["macd"] = {"line": round(ml[-1], 2), "signal": round(ms[-1], 2), "hist": round(mh[-1], 2)}
        out["evidence"].append("MACD %s signal, %s zero; histogram %s%s" % (
            "above" if above else "below", "above" if ml[-1] > 0 else "below", "expanding" if expanding else "contracting",
            (", crossed on %s" % bars[cross]["t"]) if cross is not None else ""))
        out["score"] += 1 if above and ml[-1] > 0 else -1 if not above and ml[-1] < 0 else 0
    k, d = ind["stoch_k"][-1], ind["stoch_d"][-1]
    if k is not None and d is not None:
        out["evidence"].append("Stochastic %.0f/%.0f%s" % (k, d, " (overbought)" if k > 80 else " (oversold)" if k < 20 else ""))
    return out


def volatility_block(bars, ind):
    c = bars[-1]["c"]
    a = ind["atr"][-1]
    out = {"evidence": []}
    if a:
        out["atr"] = round(a, 2)
        out["atr_pct"] = round(a / c * 100, 2)
        out["evidence"].append("ATR(14) %.2f = %.1f%% of price: a normal day moves about Rs %.0f" % (a, a / c * 100, a))
    up, lo, p = ind["bb_up"][-1], ind["bb_lo"][-1], ind["bbw_pct"][-1]
    if up is not None:
        pos = (c - lo) / (up - lo) * 100 if up > lo else 50
        out["bb_position_pct"] = round(pos, 0)
        out["evidence"].append("Bollinger %.2f–%.2f; price at %.0f%% of the band" % (lo, up, pos))
        if p is not None:
            out["squeeze"] = p <= 10
            out["evidence"].append("Band width at the %.0fth percentile of the past year%s" % (
                p, " — a squeeze: a big move often follows, direction unknown" if p <= 10 else ""))
    return out


def volume_block(bars, ind):
    v = [b["v"] or 0 for b in bars]
    c = [b["c"] for b in bars]
    out = {"evidence": [], "score": 0}
    if not any(v[-20:]):
        out["evidence"].append("No volume data for this series (indices on Yahoo carry none)")
        return out
    v20 = ind["vol20"][-1] or 0
    if v20:
        ratio = v[-1] / v20
        out["volume_ratio"] = round(ratio, 2)
        out["evidence"].append("Last volume %.1fx its 20-day average%s" % (ratio, " — heavy" if ratio >= 1.5 else ""))
    up = sum(v[i] for i in range(len(c) - 20, len(c)) if c[i] > c[i - 1])
    dn = sum(v[i] for i in range(len(c) - 20, len(c)) if c[i] < c[i - 1])
    if up + dn:
        out["updown"] = round(up / max(1, dn), 2)
        out["evidence"].append("Last 20 days: up-day volume %.2fx down-day volume (%s)" % (
            up / max(1, dn), "accumulation" if up > 1.2 * dn else "distribution" if dn > 1.2 * up else "balanced"))
        out["score"] += 1 if up > 1.2 * dn else -1 if dn > 1.2 * up else 0
    ob = ind["obv"]
    if len(ob) > 40:
        ob_s, pr_s = ob[-1] - ob[-21], c[-1] - c[-21]
        if pr_s > 0 and ob_s < 0:
            out["evidence"].append("OBV falling while price rose over 20 days: rally on thin buying")
            out["score"] -= 1
        elif pr_s < 0 and ob_s > 0:
            out["evidence"].append("OBV rising while price fell over 20 days: quiet buying")
            out["score"] += 1
        else:
            out["evidence"].append("OBV confirms the 20-day price direction")
    return out


def relative_block(bars, bench):
    """bench: {date: close} for the Nifty 50."""
    out = {"evidence": [], "score": 0}
    if not bench:
        out["evidence"].append("Benchmark data unavailable")
        return out
    common = [b for b in bars if b["t"] in bench]
    if len(common) < 70:
        return out
    rs = [b["c"] / bench[b["t"]] for b in common]
    for days, lab in ((63, "3 months"), (126, "6 months"), (252, "12 months")):
        if len(common) > days:
            s = common[-1]["c"] / common[-1 - days]["c"] - 1
            m = bench[common[-1]["t"]] / bench[common[-1 - days]["t"]] - 1
            out.setdefault("vs_nifty", {})[lab] = round((s - m) * 100, 1)
            out["evidence"].append("%s: stock %+.1f%% vs Nifty %+.1f%% (%+.1f pts)" % (lab, s * 100, m * 100, (s - m) * 100))
    rs50 = sma(rs, 50)
    if rs50[-1] is not None:
        rising = rs[-1] > rs50[-1]
        out["rs_trend"] = "rising" if rising else "falling"
        out["evidence"].append("Relative-strength line %s its 50-day average: %s" % (
            "above" if rising else "below", "outperforming" if rising else "underperforming"))
        out["score"] += 1 if rising else -1
    out["rs_line"] = [{"t": b["t"], "v": round(x, 6)} for b, x in zip(common[-260:], rs[-260:])]
    return out


def analyse(bars, bench=None, symbol="", source="", params=None):
    """Full chartist read of daily bars. Returns a JSON-ready dict."""
    if len(bars) < 60:
        raise ValueError("need at least 60 daily bars (got %d)" % len(bars))
    ind = indicators(bars, params)
    h, l = [b["h"] for b in bars], [b["l"] for b in bars]
    a_now = ind["atr"][-1] or (bars[-1]["c"] * 0.02)
    piv = pivots(h, l, k=5, min_move=a_now * 1.5)
    trend = trend_block(bars, ind, piv)
    mom = momentum_block(bars, ind, piv)
    vol = volatility_block(bars, ind)
    vlm = volume_block(bars, ind)
    rel = relative_block(bars, bench or {})
    sup, res = levels(bars, piv, a_now)
    pats = chart_patterns(bars, piv, a_now)
    short_dir = 1 if trend["short"] == "up" else -1 if trend["short"] == "down" else 0
    cdl = candles(bars, -1 if bars[-1]["c"] < (bars[-6]["c"] if len(bars) > 6 else bars[0]["c"]) else 1)
    fib = fibonacci(bars, piv)
    # weekly confirmation
    wk = weekly(bars)
    weekly_view = None
    if len(wk) >= 60:
        wind = indicators(wk, params)
        wpiv = pivots([b["h"] for b in wk], [b["l"] for b in wk], k=3, min_move=(wind["atr"][-1] or 0) * 1.0)
        wt = trend_block(wk, wind, wpiv)
        weekly_view = {"short": wt["short"], "intermediate": wt["intermediate"], "long": wt["long"],
                       "structure": wt["structure"], "rsi": round(wind["rsi"][-1], 1) if wind["rsi"][-1] else None,
                       "score": wt["score"]}
    # signals + track record
    sigs = signal_series(ind, bars)
    signals = []
    for name, fires in sigs.items():
        tr = track_record(bars, fires)
        if tr["occurrences"] == 0:
            continue
        tr["name"] = name
        tr["bias"] = SIGNAL_BIAS[name]
        tr["reading"] = judge(tr, SIGNAL_BIAS[name])
        signals.append(tr)
    signals.sort(key=lambda s: s["last"] or "", reverse=True)          # most recent first
    signals.sort(key=lambda s: (not s["active"], (s["h"].get(20) or {}).get("n", 0) < 8))
    active = [s for s in signals if s["active"]]
    # verdict: weight of evidence
    pat_score = sum((1 if p["bias"] == "bullish" else -1 if p["bias"] == "bearish" else 0)
                    for p in pats if p["status"] in ("confirmed", "breakout up", "breakdown"))
    parts = {"trend": trend["score"], "momentum": mom["score"], "volume": vlm["score"], "relative": rel["score"],
             "patterns": pat_score, "weekly": (1 if weekly_view and weekly_view["score"] >= 2 else
                                               -1 if weekly_view and weekly_view["score"] <= -2 else 0)}
    total = sum(parts.values())
    bias = ("Bullish" if total >= 5 else "Mildly bullish" if total >= 2 else "Bearish" if total <= -5
            else "Mildly bearish" if total <= -2 else "Neutral / range")
    agree = sum(1 for v in parts.values() if v * total > 0)
    conf = "high" if abs(total) >= 7 and agree >= 4 else "medium" if abs(total) >= 3 else "low"
    c = bars[-1]["c"]
    s1 = sup[0]["price"] if sup else None
    r1 = res[0]["price"] if res else None
    if total >= 2:
        invalid = ("A daily close below %.2f (nearest support) would weaken this view" % s1) if s1 else "No support level found below"
    elif total <= -2:
        invalid = ("A daily close above %.2f (nearest resistance) would weaken this view" % r1) if r1 else "No resistance level found above"
    else:
        invalid = "Direction is set by a close outside %s–%s" % ("%.2f" % s1 if s1 else "?", "%.2f" % r1 if r1 else "?")
    narrative = narrate(symbol, bars, trend, mom, vlm, rel, sup, res, pats, cdl, weekly_view, bias, active)
    ich = ind["ichi"]
    ichi_view = ichimoku_block(bars, ich)
    adx_now = ind["adx"][-1]
    regime = {"adx": round(adx_now, 1) if adx_now is not None else None,
              "label": "n/a" if adx_now is None else "Trending" if adx_now >= 25 else "Range-bound" if adx_now < 20
              else "Weak trend"}
    fut = future_dates(bars, ich["shift"])
    nb = len(bars)

    def at(j):                      # date of bar position j (may be in the future)
        return bars[j]["t"] if j < nb else fut[j - nb]

    def shifted(x, k):
        return [{"t": at(j + k), "v": round(v, 2)} for j, v in enumerate(x) if v is not None and 0 <= j + k < nb + len(fut)]
    # overlays for the chart
    def series(x, nd=2):
        return [{"t": b["t"], "v": round(v, nd)} for b, v in zip(bars, x) if v is not None]
    return {
        "symbol": symbol, "source": source, "last": {"t": bars[-1]["t"], "c": c,
                                                     "chg_pct": round((c / bars[-2]["c"] - 1) * 100, 2)},
        "verdict": {"bias": bias, "score": total, "confidence": conf, "parts": parts, "invalidation": invalid,
                    "narrative": narrative},
        "trend": trend, "weekly": weekly_view, "momentum": mom, "volatility": vol, "volume": vlm,
        "relative": {k: v for k, v in rel.items() if k != "rs_line"}, "levels": {"support": sup, "resistance": res,
        "high_52w": round(ind["hi252"][-1], 2) if ind["hi252"][-1] else None,
        "low_52w": round(ind["lo252"][-1], 2) if ind["lo252"][-1] else None, "gaps": gaps(bars)},
        "candles": cdl, "patterns": pats, "fibonacci": fib, "signals": signals, "ichimoku": ichi_view,
        "regime": regime, "future": fut, "params": ind["params"],
        "scorecard": scorecard(bars, ind, trend, weekly_view, mom, vlm, rel, ichi_view, pats, cdl, signals,
                               weekly_tf=fut and len(bars) > 1 and (
                                   __import__("datetime").date.fromisoformat(fut[0]) -
                                   __import__("datetime").date.fromisoformat(bars[-1]["t"][:10])).days >= 5),
        "overlays": {"sma20": series(ind["sma20"]), "sma50": series(ind["sma50"]), "sma200": series(ind["sma200"]),
                     "bb_up": series(ind["bb_up"]), "bb_lo": series(ind["bb_lo"]), "st": series(ind["st"]),
                     "rsi": series(ind["rsi"], 1), "macd": series(ind["macd"][0], 3), "macd_sig": series(ind["macd"][1], 3),
                     "macd_hist": series(ind["macd"][2], 3), "rs_line": rel.get("rs_line", []),
                     "pivots": [{"t": bars[i]["t"], "price": round(p, 2), "kind": k} for i, p, k in piv[-40:]],
                     "ema20": series(ind["ema20"]), "ema50": series(ind["ema50"]),
                     "tenkan": series(ich["tenkan"]), "kijun": series(ich["kijun"]),
                     "span_a": shifted(ich["span_a"], ich["shift"]), "span_b": shifted(ich["span_b"], ich["shift"]),
                     "chikou": [{"t": bars[j - ich["shift"]]["t"], "v": round(bars[j]["c"], 2)}
                                for j in range(ich["shift"], nb)]},
    }


def touches(n):
    return "%d touch%s" % (n, "" if n == 1 else "es")


def narrate(symbol, bars, trend, mom, vlm, rel, sup, res, pats, cdl, wk, bias, active):
    c = bars[-1]["c"]
    s = []
    s.append("%s closed at %.2f on %s." % (symbol or "The security", c, bars[-1]["t"]))
    tl = {"up": "uptrend", "down": "downtrend", "mixed": "mixed picture", "sideways": "sideways drift", "n/a": "not enough history"}
    s.append("Long-term %s, intermediate %s, short-term %s; swings show %s and ADX reads %s." % (
        tl.get(trend["long"], trend["long"]), tl.get(trend["intermediate"], trend["intermediate"]),
        tl.get(trend["short"], trend["short"]), trend["structure"], trend["strength"]))
    if wk:
        d_sign = (trend["score"] > 1) - (trend["score"] < -1)
        w_sign = (wk["score"] > 1) - (wk["score"] < -1)
        verdict = ("agrees" if d_sign == w_sign and d_sign != 0 else "disagrees" if d_sign * w_sign < 0 else "is mixed")
        s.append("The weekly chart %s: long-term %s, intermediate %s." % (
            verdict, tl.get(wk["long"], wk["long"]), tl.get(wk["intermediate"], wk["intermediate"])))
    if "rsi" in mom:
        s.append("Momentum: RSI %.0f%s." % (mom["rsi"], {"bearish": ", with a bearish divergence", "bullish": ", with a bullish divergence"}.get(mom.get("divergence"), "")))
    if rel.get("rs_trend"):
        s.append("It is %s the Nifty 50 on its relative-strength line." % ("outperforming" if rel["rs_trend"] == "rising" else "underperforming"))
    if sup or res:
        s.append("Nearest support %s; nearest resistance %s." % (
            ("%.2f (%s)" % (sup[0]["price"], touches(sup[0]["touches"]))) if sup else "none found",
            ("%.2f (%s)" % (res[0]["price"], touches(res[0]["touches"]))) if res else "none found (near highs)"))
    live = [p for p in pats if p["status"] in ("forming", "confirmed", "breakout up", "breakdown", "inside")]
    if live:
        s.append("Patterns: " + "; ".join("%s (%s)" % (p["name"], p["status"]) for p in live[:3]) + ".")
    if cdl:
        s.append("Last candle: " + ", ".join(x["name"] for x in cdl[:2]) + ".")
    if active:
        s.append("Recent signals: " + "; ".join("%s — %s" % (a["name"], a["reading"]) for a in active[:3]) + ".")
    s.append("Weight of evidence: %s." % bias.lower())
    return " ".join(s)
