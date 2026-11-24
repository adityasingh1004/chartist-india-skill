#!/usr/bin/env python3
"""Does the Chartist verdict mean anything? Point-in-time check across many stocks.

    py chartist\\calibrate.py --fetch                 # download 10y of Nifty 50 bars from Yahoo, then calibrate
    py chartist\\calibrate.py --data my\\data --step 42  # use bars you already cached

At every --step-th trading day (after 400 bars of history), for every stock with cached
daily data, the engine reads the chart using ONLY bars up to that day. The verdict
bucket is then compared with what the stock did over the next 20 and 60 trading days,
against the Nifty 50 over the same days. If 'Bullish' readings are not followed by
better returns than 'Bearish' ones, the verdict is decoration — and the tool says so.

Survivorship: run on today's index list, so all stocks survived; excess returns vs the
Nifty are flattered for every bucket alike. Compare buckets with each other.
"""
import argparse
import json
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from common import nse  # noqa: E402
import engine  # noqa: E402

BUCKETS = ["Bearish", "Mildly bearish", "Neutral / range", "Mildly bullish", "Bullish"]


def bars_of(sym, root):
    s = nse.load_cached(sym, "daily", root=root)
    if not s:
        return []
    return [{"t": d.date().isoformat(), "o": o, "h": h, "l": l, "c": c, "v": v or 0}
            for d, o, h, l, c, v in zip(s["dt"], s["o"], s["h"], s["l"], s["c"], s["v"]) if None not in (o, h, l, c)]


NIFTY50_DEFAULT = ["ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO"]   # Nifty 50 members as last known — UNVERIFIED — CHECK MANUALLY against niftyindices.com


def fetch(syms, root):
    """Download up to 10 years of daily bars from Yahoo (unofficial) into root/daily/<SYM>.json."""
    for sym in list(syms) + ["IDX_NIFTY50"]:
        try:
            s = nse.parse_chart(nse.yahoo_chart(sym, "10y", "1d"))
        except nse.FetchError as e:
            print("skip %s: %s" % (sym, e))
            continue
        bars = list(zip(s["dt"], s["o"], s["h"], s["l"], s["c"], s["v"]))
        nse.save_cached(sym, "daily", bars, "yahoo", "1d", root=root)
        print("cached %-12s %d bars" % (sym, len(bars)))


def one(args):
    sym, root, step = args
    b = bars_of(sym, root)
    nb = {x["t"]: x["c"] for x in bars_of("IDX_NIFTY50", root)}
    out = []
    for i in range(400, len(b) - 60, step):
        hist = b[:i + 1]
        try:
            r = engine.analyse(hist[-1500:], {t: c for t, c in nb.items() if t <= hist[-1]["t"]}, sym)
        except ValueError:
            continue
        t0, t20, t60 = b[i]["t"], b[i + 20]["t"], b[i + 60]["t"]
        if not all(t in nb for t in (t0, t20, t60)):
            continue
        out.append({"sym": sym, "t": t0, "bias": r["verdict"]["bias"], "score": r["verdict"]["score"],
                    "r20": b[i + 20]["c"] / b[i]["c"] - 1, "r60": b[i + 60]["c"] / b[i]["c"] - 1,
                    "x20": b[i + 20]["c"] / b[i]["c"] - nb[t20] / nb[t0], "x60": b[i + 60]["c"] / b[i]["c"] - nb[t60] / nb[t0]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "caldata"), help="folder holding daily/<SYM>.json bars")
    ap.add_argument("--fetch", action="store_true", help="download the symbols' bars from Yahoo first")
    ap.add_argument("--symbols", help="file with one symbol per line (default: all cached stocks)")
    ap.add_argument("--step", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(HERE, "calibration.json"))
    a = ap.parse_args(argv)
    if a.fetch:
        fetch([x.strip() for x in open(a.symbols) if x.strip()] if a.symbols else NIFTY50_DEFAULT, a.data)
    if a.symbols:
        syms = [x.strip() for x in open(a.symbols) if x.strip()]
    else:
        syms = sorted(f[:-5] for f in os.listdir(os.path.join(a.data, "daily")) if f.endswith(".json") and not f.startswith("IDX_"))
    rows = []
    with ProcessPoolExecutor() as ex:
        for res in ex.map(one, [(s, a.data, a.step) for s in syms]):
            rows += res
    print("CHARTIST VERDICT CALIBRATION — %d readings, %d stocks, every %d trading days, point-in-time" % (
        len(rows), len(set(r["sym"] for r in rows)), a.step))
    print("%-16s %6s %10s %10s %10s %10s %9s" % ("verdict", "n", "20d med", "20d vs N50", "60d med", "60d vs N50", "60d up%"))
    summary = {}
    for bkt in BUCKETS:
        xs = [r for r in rows if r["bias"] == bkt]
        if not xs:
            continue
        m = lambda k: statistics.median(r[k] for r in xs) * 100
        up = 100 * sum(1 for r in xs if r["r60"] > 0) / len(xs)
        summary[bkt] = {"n": len(xs), "r20": m("r20"), "x20": m("x20"), "r60": m("r60"), "x60": m("x60"), "up60": up}
        print("%-16s %6d %9.1f%% %9.1f%% %9.1f%% %9.1f%% %8.0f%%" % (bkt, len(xs), m("r20"), m("x20"), m("r60"), m("x60"), up))
    bull = [r["x60"] for r in rows if "ullish" in r["bias"]]
    bear = [r["x60"] for r in rows if "earish" in r["bias"]]
    if bull and bear:
        gap = (statistics.median(bull) - statistics.median(bear)) * 100
        # dates cluster: a rough check by half-period
        dates = sorted(set(r["t"] for r in rows))
        mid = dates[len(dates) // 2]
        halves = []
        for lo, hi in ((dates[0], mid), (mid, dates[-1])):
            b1 = [r["x60"] for r in rows if "ullish" in r["bias"] and lo <= r["t"] < hi]
            b2 = [r["x60"] for r in rows if "earish" in r["bias"] and lo <= r["t"] < hi]
            halves.append((statistics.median(b1) - statistics.median(b2)) * 100 if b1 and b2 else None)
        print("Bullish minus bearish, 60-day excess vs Nifty (medians): %+.1f pts; by half: %s" % (
            gap, ", ".join("%+.1f" % h if h is not None else "n/a" for h in halves)))
        print("Reading: %s" % ("the verdict has separated winners from losers here" if gap > 1.0 and all(h and h > 0 for h in halves)
                               else "the verdict has NOT reliably separated winners from losers — treat it as a description of the chart, not a forecast"))
        summary["bull_minus_bear_x60"] = gap
        summary["halves"] = halves
    with open(a.out, "w") as fh:
        json.dump({"step": a.step, "readings": len(rows), "summary": summary}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
