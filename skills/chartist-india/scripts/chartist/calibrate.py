#!/usr/bin/env python3
"""Does the Chartist verdict mean anything? Point-in-time check, group by group.

    py chartist\\calibrate.py --fetch --sample 10        # quick test: 10 symbols from every group
    py chartist\\calibrate.py --fetch                    # full universe: every NSE/BSE stock + global series
    py chartist\\calibrate.py --fetch --universe nifty50 # the original Nifty 50 check
    py chartist\\calibrate.py                            # recompute from bars already downloaded

At every --step-th trading day (after 400 bars of history), the engine reads the chart
using ONLY bars up to that day, on default settings. The verdict is then compared with
what the security did over the next 20 and 60 trading days. Stocks are measured against
the Nifty 50 over the same days; global series (commodities, yields, FX) on their own
price change, because a Nifty benchmark means nothing for them.

Results are reported for each group separately, never only as one pooled number:
  Nifty 100 · Midcap 150 · Smallcap 250 · NSE outside Nifty 500 · NSE SME · BSE-only ·
  Global commodity / yield / bond futures / FX, plus a liquidity split across all stocks.

Basis: TODAY's lists. Membership of the index groups is taken from the current Nifty
index files, and only symbols listed today are tested. Companies that were delisted or
collapsed are missing, so every stock group is flattered (survivorship). Compare the
verdict buckets with each other inside a group; do not read the levels as achievable.

Downloads resume: a symbol already cached within --refetch-days is not downloaded again,
so an interrupted run can simply be started again. Yahoo is unofficial and rate-limits;
a full run takes hours.
"""
import argparse
import bisect
import csv
import datetime
import gzip
import io
import json
import os
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from common import nse  # noqa: E402
import engine  # noqa: E402
import universe  # noqa: E402

BUCKETS = ["Bearish", "Mildly bearish", "Neutral / range", "Mildly bullish", "Bullish"]
MIN_READINGS = 300            # fewer bullish+bearish readings than this: "too few to judge"
ILLIQUID_RS = 10_00_000       # median daily traded value below Rs 10 lakh over the prior 60 bars

GROUP_ORDER = ["Nifty 100", "Midcap 150", "Smallcap 250", "Nifty 500 (tier unknown)", "NSE outside Nifty 500",
               "NSE SME", "BSE-only", "Global commodity", "Global yield", "Global bond futures", "Global FX",
               "Global other", "All stocks: liquid", "All stocks: illiquid", "All stocks"]

# Current index constituent files. URLs UNVERIFIED — CHECK MANUALLY on niftyindices.com.
TIER_URLS = {
    "Nifty 50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "Nifty 100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "Midcap 150": "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv",
    "Smallcap 250": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv",
}
TIER_DIR = os.path.join(HERE, "data", "tiers")

NIFTY50_DEFAULT = ["ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO"]   # fallback only, if the Nifty 50 file cannot be downloaded — UNVERIFIED — CHECK MANUALLY

GLOBAL_GROUP = {"commodity": "Global commodity", "yield": "Global yield", "bondfut": "Global bond futures", "fx": "Global FX"}


# ---------------------------------------------------------------- lists

def _symbols_from_csv(text):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        s = (r.get("Symbol") or r.get("SYMBOL") or "").strip().upper()
        if s:
            out.append(s)
    return out


def load_tiers(log=print, allow_download=True, max_age_days=7):
    """{'Nifty 50': [...], 'Nifty 100': [...], ...} from today's files (cached for a week)."""
    os.makedirs(TIER_DIR, exist_ok=True)
    tiers = {}
    for name, url in TIER_URLS.items():
        path = os.path.join(TIER_DIR, url.rsplit("/", 1)[1])
        fresh = os.path.exists(path) and (time.time() - os.path.getmtime(path)) < max_age_days * 86400
        if allow_download and not fresh:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": nse.UA, "Referer": "https://www.niftyindices.com/"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    text = r.read().decode("utf-8", "replace")
                if _symbols_from_csv(text):
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)
            except Exception as e:                       # noqa: BLE001 — any failure: fall back below
                log("%s list not downloaded (%s)" % (name, e))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                tiers[name] = _symbols_from_csv(fh.read())
    return tiers


def nifty500_symbols():
    path = os.path.join(HERE, "data", "nifty500.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return _symbols_from_csv(fh.read())


def group_of(item, tiers, n500):
    """Group name for one universe entry ({s, k, x, ...})."""
    s, x = item["s"], item.get("x", "")
    if s.startswith("GLB_"):
        return GLOBAL_GROUP.get(item.get("k"), "Global other")
    if s.startswith("BSE_") or x == "BSE":
        return "BSE-only"
    if x == "NSE SME":
        return "NSE SME"
    for name in ("Nifty 100", "Midcap 150", "Smallcap 250"):
        if s in tiers.get(name, ()):
            return name
    if s in n500:
        return "Nifty 500 (tier unknown)"
    return "NSE outside Nifty 500"


def build_list(which, tiers, log=print, allow_download=True):
    """[(sym, group, [yahoo tickers])] for --universe."""
    items, yahoo, info = universe.entries(log=log, allow_download=allow_download)
    n500 = set(nifty500_symbols())
    by_sym = {i["s"]: i for i in items}
    if which == "nifty50":
        syms = tiers.get("Nifty 50") or NIFTY50_DEFAULT
        return [(s, group_of(by_sym.get(s, {"s": s, "x": "NSE"}), tiers, n500), []) for s in syms]
    if which == "nifty500":
        return [(s, group_of(by_sym.get(s, {"s": s, "x": "NSE"}), tiers, n500), []) for s in sorted(n500)]
    out = []
    for i in items:
        if i.get("k") == "index":
            continue
        g = group_of(i, tiers, n500)
        if which == "stocks" and g.startswith("Global"):
            continue
        if which == "global" and not g.startswith("Global"):
            continue
        out.append((i["s"], g, yahoo.get(i["s"]) or []))
    return out


# ---------------------------------------------------------------- download

def _cache_age_days(sym, root):
    path = os.path.join(root, "daily", sym + ".json")
    if not os.path.exists(path):
        return None
    return (time.time() - os.path.getmtime(path)) / 86400


def fetch(todo, root, refetch_days=30, pause=0.4, log=print):
    """Download up to 10 years of daily bars from Yahoo (unofficial) into root/daily/<SYM>.json. Resumable."""
    failed_path = os.path.join(root, "fetch_failed.json")
    failed = nse.read_json(failed_path, {}) or {}
    today = datetime.date.today()
    jobs = [(s, c) for s, _, c in todo] + [("IDX_NIFTY50", [])]
    got = skipped = bad = 0
    streak = 0
    for n, (sym, cands) in enumerate(jobs, 1):
        age = _cache_age_days(sym, root)
        if age is not None and age < refetch_days:
            skipped += 1
            continue
        when = failed.get(sym)
        if when and (today - datetime.date.fromisoformat(when)).days < refetch_days:
            skipped += 1
            continue
        s, err = None, None
        for c in (cands or [None]):
            if c:
                nse.INDEX_YAHOO[sym] = c
            try:
                s = nse.parse_chart(nse.yahoo_chart(sym, "10y", "1d"), any_currency=sym.startswith("GLB_"))
                break
            except nse.FetchError as e:
                err = e
        if s is None:
            bad += 1
            failed[sym] = today.isoformat()
            streak = streak + 1 if "unreachable" in str(err) else 0
            if streak >= 5:                              # Yahoo is throttling or the network is down
                log("Yahoo unreachable 5 times in a row: waiting 60 s ...")
                time.sleep(60)
                streak = 0
        else:
            streak = 0
            bars = list(zip(s["dt"], s["o"], s["h"], s["l"], s["c"], s["v"]))
            nse.save_cached(sym, "daily", bars, "yahoo", "1d", root=root)
            failed.pop(sym, None)
            got += 1
        if n % 100 == 0:
            log("download %d/%d: %d new, %d already cached, %d failed" % (n, len(jobs), got, skipped, bad))
            nse.write_json(failed_path, failed)
        time.sleep(pause)
    nse.write_json(failed_path, failed)
    log("download done: %d new, %d already cached, %d failed (listed in %s)" % (got, skipped, bad, failed_path))


# ---------------------------------------------------------------- readings

def bars_of(sym, root):
    s = nse.load_cached(sym, "daily", root=root)
    if not s:
        return []
    return [{"t": d.date().isoformat(), "o": o, "h": h, "l": l, "c": c, "v": v or 0}
            for d, o, h, l, c, v in zip(s["dt"], s["o"], s["h"], s["l"], s["c"], s["v"])
            if None not in (o, h, l, c) and c > 0]


_BENCH = {}


def _bench(root):
    if root not in _BENCH:
        _BENCH[root] = {x["t"]: x["c"] for x in bars_of("IDX_NIFTY50", root)}
    return _BENCH[root]


def one(args):
    """All point-in-time readings for one symbol: list of tuples
    (sym, group, date, bias, score, r20, r60, x20, x60, illiquid)."""
    sym, group, root, step = args
    b = bars_of(sym, root)
    is_global = group.startswith("Global")
    nb = {} if is_global else _bench(root)
    nb_dates = sorted(nb)
    out = []
    for i in range(400, len(b) - 60, step):
        hist = b[:i + 1]
        t0, t20, t60 = b[i]["t"], b[i + 20]["t"], b[i + 60]["t"]
        if not is_global and not all(t in nb for t in (t0, t20, t60)):
            continue
        if is_global:
            bench = {}
        else:
            k = bisect.bisect_right(nb_dates, t0)
            bench = {t: nb[t] for t in nb_dates[max(0, k - 1600):k]}
        try:
            r = engine.analyse(hist[-1500:], bench, sym)
        except ValueError:
            continue
        r20 = b[i + 20]["c"] / b[i]["c"] - 1
        r60 = b[i + 60]["c"] / b[i]["c"] - 1
        if is_global:
            x20, x60, illiquid = r20, r60, None
        else:
            x20 = r20 - (nb[t20] / nb[t0] - 1)
            x60 = r60 - (nb[t60] / nb[t0] - 1)
            tv = sorted(x["c"] * x["v"] for x in hist[-60:])
            illiquid = tv[len(tv) // 2] < ILLIQUID_RS
        out.append((sym, group, t0, r["verdict"]["bias"], r["verdict"]["score"], r20, r60, x20, x60, illiquid))
    return out


def summarise(rows):
    """Bucket medians, bullish-minus-bearish gap on 60-day x, and the gap in each half of the dates."""
    out = {"readings": len(rows), "symbols": len(set(r[0] for r in rows)), "buckets": {}}
    if rows:
        out["from"], out["to"] = min(r[2] for r in rows), max(r[2] for r in rows)
    for bkt in BUCKETS:
        xs = [r for r in rows if r[3] == bkt]
        if not xs:
            continue
        med = lambda k: statistics.median(r[k] for r in xs) * 100   # noqa: E731
        out["buckets"][bkt] = {"n": len(xs), "r20": med(5), "x20": med(7), "r60": med(6), "x60": med(8),
                               "up60": 100 * sum(1 for r in xs if r[6] > 0) / len(xs)}
    bull = [r for r in rows if "ullish" in r[3]]
    bear = [r for r in rows if "earish" in r[3]]
    out["n_bull"], out["n_bear"] = len(bull), len(bear)
    if bull and bear:
        out["bull_minus_bear_x60"] = (statistics.median(r[8] for r in bull) - statistics.median(r[8] for r in bear)) * 100
        dates = sorted(set(r[2] for r in rows))
        mid = dates[len(dates) // 2]
        halves = []
        for lo, hi in ((dates[0], mid), (mid, "9999")):
            b1 = [r[8] for r in bull if lo <= r[2] < hi]
            b2 = [r[8] for r in bear if lo <= r[2] < hi]
            halves.append((statistics.median(b1) - statistics.median(b2)) * 100 if b1 and b2 else None)
        out["halves"] = halves
    gap, halves = out.get("bull_minus_bear_x60"), out.get("halves") or []
    if gap is None or min(len(bull), len(bear)) * 2 < MIN_READINGS:
        out["finding"] = "too few"
    elif gap > 1.0 and all(h is not None and h > 0 for h in halves):
        out["finding"] = "separates"
    else:
        out["finding"] = "does not separate"
    return out


def report(name, s, measure, log=print):
    log("")
    log("== %s — %d readings, %d symbols, %s to %s (%s)" % (
        name, s["readings"], s["symbols"], s.get("from", "-"), s.get("to", "-"), measure))
    log("%-16s %7s %9s %10s %9s %10s %8s" % ("verdict", "n", "20d med", "20d x", "60d med", "60d x", "60d up"))
    for bkt in BUCKETS:
        v = s["buckets"].get(bkt)
        if v:
            log("%-16s %7d %8.1f%% %9.1f%% %8.1f%% %9.1f%% %7.0f%%" % (bkt, v["n"], v["r20"], v["x20"], v["r60"], v["x60"], v["up60"]))
    if "bull_minus_bear_x60" in s:
        log("bullish minus bearish, 60 days: %+.1f pts (halves %s) -> %s" % (
            s["bull_minus_bear_x60"], ", ".join("%+.1f" % h if h is not None else "n/a" for h in s["halves"]), s["finding"]))
    else:
        log("no bullish/bearish comparison possible -> too few")


def measure_of(group):
    return "60-day price change" if group.startswith("Global") else "60-day return minus the Nifty 50"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--universe", choices=["all", "stocks", "global", "nifty500", "nifty50"], default="all")
    ap.add_argument("--data", default=os.path.join(HERE, "caldata"), help="folder for downloaded bars (daily/<SYM>.json)")
    ap.add_argument("--fetch", action="store_true", help="download missing or stale bars from Yahoo first")
    ap.add_argument("--refetch-days", type=int, default=30, help="re-download bars older than this (default 30)")
    ap.add_argument("--sample", type=int, default=0, help="test run: only this many symbols from each group")
    ap.add_argument("--step", type=int, default=42, help="trading days between readings (default 42)")
    ap.add_argument("--workers", type=int, default=0, help="processes for the readings (default: all cores)")
    ap.add_argument("--out", default=None, help="results file (default chartist/calibration.json; "
                                                 "a --sample run writes calibration-sample.json)")
    a = ap.parse_args(argv)

    tiers = load_tiers()
    todo = build_list(a.universe, tiers)
    if a.sample:
        seen, kept = {}, []
        for row in todo:
            if seen.get(row[1], 0) < a.sample:
                seen[row[1]] = seen.get(row[1], 0) + 1
                kept.append(row)
        todo = kept
    counts = {}
    for _, g, _ in todo:
        counts[g] = counts.get(g, 0) + 1
    print("CHARTIST CALIBRATION — universe '%s', %d symbols: %s" % (
        a.universe, len(todo), ", ".join("%s %d" % (g, counts[g]) for g in GROUP_ORDER if g in counts)))
    if not tiers.get("Nifty 100"):
        print("NOTE: index tier files not available; Nifty 500 stocks are grouped as 'Nifty 500 (tier unknown)'.")
    if a.fetch:
        fetch(todo, a.data, a.refetch_days)

    have = [(s, g) for s, g, _ in todo if os.path.exists(os.path.join(a.data, "daily", s + ".json"))]
    print("reading %d cached series every %d trading days (this is the slow part) ..." % (len(have), a.step))
    rows, done, t0 = [], 0, time.time()
    with ProcessPoolExecutor(max_workers=a.workers or None) as ex:
        for res in ex.map(one, [(s, g, a.data, a.step) for s, g in have], chunksize=4):
            rows += res
            done += 1
            if done % 250 == 0:
                print("  %d/%d series, %d readings, %.0f min" % (done, len(have), len(rows), (time.time() - t0) / 60))

    groups = {}
    for g in GROUP_ORDER[:-3]:
        sel = [r for r in rows if r[1] == g]
        if sel:
            groups[g] = dict(summarise(sel), measure=measure_of(g))
    stocks = [r for r in rows if not r[1].startswith("Global")]
    if stocks:
        for name, sel in (("All stocks: liquid", [r for r in stocks if r[9] is False]),
                          ("All stocks: illiquid", [r for r in stocks if r[9]]), ("All stocks", stocks)):
            if sel:
                groups[name] = dict(summarise(sel), measure=measure_of("stock"))
    for g in GROUP_ORDER:
        if g in groups:
            report(g, groups[g], groups[g]["measure"])

    out = a.out or os.path.join(HERE, "calibration-sample.json" if a.sample else "calibration.json")
    doc = {
        "version": 2,
        "built": datetime.date.today().isoformat(),
        "universe": a.universe,
        "sample": a.sample or None,
        "step": a.step,
        "settings": "default indicator settings",
        "basis": ("Today's lists: index groups from the current Nifty constituent files, and only symbols listed "
                  "today. Delisted and collapsed companies are missing, so stock groups are flattered "
                  "(survivorship). Compare buckets within a group."),
        "illiquid_rule": "median daily traded value below Rs 10 lakh over the 60 bars before the reading",
        "readings": len(rows),
        "groups": groups,
        "group_of": {s: g for s, g in have},
        "summary": groups.get("All stocks") or next(iter(groups.values()), {}),   # pooled, for older readers
    }
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=1)
    path = os.path.join(a.data, "calibration_readings.csv.gz")
    os.makedirs(a.data, exist_ok=True)
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "group", "date", "bias", "score", "r20", "r60", "x20", "x60", "illiquid"])
        w.writerows(rows)
    print("\nwrote %s and every reading to %s" % (out, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
