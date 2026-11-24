#!/usr/bin/env python3
"""Chartist — a local chart-analysis workstation for Indian markets (NSE/BSE) plus global commodities and yields.
Author: Aditya Kumar Singh. MIT licence. Analysis tool only: not investment advice (see DISCLAIMER.md).
Standard library only. The chart library (TradingView Lightweight Charts 4.1.3, Apache-2.0)
is bundled in static/vendor, so charts work offline; web fonts fall back to system fonts.
It never places an order.

    py chartist\\app.py                 # opens http://127.0.0.1:8765 in your browser
    py chartist\\app.py --port 8800 --no-browser
    py chartist\\app.py --offline path\\to\\data   # read previously cached bars only (no internet)

Search any NSE or BSE stock (full exchange lists, chartist/universe.py), an index (NIFTY 50,
NIFTY BANK, NIFTY IT, NIFTY 100, SENSEX), or a global commodity / benchmark yield that Yahoo
carries (Brent, gold, copper, US 10Y ...; list in chartist/data/globals.json). Any other Yahoo
ticker can be typed as is (e.g. ^GSPC, ^N225, HG=F). BSE-only stocks: type the name or SYMBOL.BO.
Data: Yahoo Finance daily bars and quotes (free, unofficial, can lag). No broker login. Up to 10 years of daily history, cached in
chartist/cache/ for 15 minutes during market hours and 6 hours otherwise.
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from common import nse  # noqa: E402
import engine  # noqa: E402
import fundamentals  # noqa: E402
import universe  # noqa: E402
import company  # noqa: E402

CACHE = os.path.join(HERE, "cache")
SYMBOLS_CSV = os.path.join(HERE, "data", "nifty500.csv")
NIFTY500_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"   # UNVERIFIED URL
INDICES = {   # search name -> internal id (Yahoo tickers in common/nse.py, UNVERIFIED)
    "NIFTY 50": "IDX_NIFTY50", "NIFTY 100": "IDX_NIFTY100", "NIFTY BANK": "IDX_BANKNIFTY", "NIFTY IT": "IDX_NIFTYIT",
    "SENSEX": "IDX_SENSEX",
}
nse.INDEX_YAHOO.setdefault("IDX_BANKNIFTY", "^NSEBANK")
nse.INDEX_YAHOO.setdefault("IDX_NIFTYIT", "^CNXIT")
nse.INDEX_YAHOO.setdefault("IDX_SENSEX", "^BSESN")      # UNVERIFIED — CHECK MANUALLY
YAHOO_ALT = {}       # BSE_/GLB_ id -> [Yahoo tickers to try in order]
META = {}            # id -> {"k", "x", "u", "y"} for BSE and global entries
UNIVERSE_INFO = {}
OFFLINE = None
_LOCK = threading.Lock()
_FLOCK = threading.Lock()      # fundamentals are slow (NSE filings): never block the chart behind them


# ---------------------------------------------------------------- symbols

def load_symbols():
    """[{"s": symbol, "n": name, "k": "stock"|"index"}] for the search box."""
    rows = {}
    text = None
    if os.path.exists(SYMBOLS_CSV):
        with open(SYMBOLS_CSV, encoding="utf-8-sig") as fh:
            text = fh.read()
    elif not OFFLINE:
        try:
            text = nse.http_get(NIFTY500_URL)
            os.makedirs(os.path.dirname(SYMBOLS_CSV), exist_ok=True)
            with open(SYMBOLS_CSV, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception:
            text = None
    if text:
        for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
            s = (r.get("Symbol") or "").strip().upper()
            if s and not s.startswith("DUMMY"):
                rows[s] = {"s": s, "n": (r.get("Company Name") or s).strip(), "k": "stock"}
    if OFFLINE:
        for f in os.listdir(os.path.join(OFFLINE, "daily")):
            s = f.rsplit(".json", 1)[0]
            if s and not s.startswith("IDX_") and s not in rows:
                rows[s] = {"s": s, "n": s, "k": "stock"}
    out = [{"s": v, "n": k, "k": "index", "x": "BSE" if k == "SENSEX" else "NSE"} for k, v in INDICES.items()]
    try:
        items, yahoo, info = universe.entries(list(rows.values()), allow_download=not OFFLINE)
    except Exception as e:          # never let a bad list stop the app: fall back to Nifty 500
        print("universe list unavailable (%s): Nifty 500 only" % e)
        items, yahoo, info = sorted(rows.values(), key=lambda r: r["s"]), {}, {}
    UNIVERSE_INFO.update(info)
    for ident, cands in yahoo.items():
        register(ident, cands)
    for r in items:
        if r["s"].startswith(("BSE_", "GLB_")):
            META[r["s"]] = {"k": r["k"], "x": r.get("x", ""), "u": r.get("u", ""), "y": r.get("y", "")}
    return out + [{k: r[k] for k in ("s", "n", "k", "x", "y", "u") if r.get(k)} for r in items]


def register(ident, cands):
    YAHOO_ALT[ident] = list(cands)
    nse.INDEX_YAHOO[ident] = cands[0]


def kind_of(sym):
    """'global' | 'bse' | 'index' | 'stock'"""
    return "global" if sym.startswith("GLB_") else "bse" if sym.startswith("BSE_") else \
        "index" if sym.startswith("IDX_") else "stock"


def ad_hoc(q):
    """A typed ticker that is not in the lists: SYMBOL.BO -> BSE_, other Yahoo tickers -> GLB_."""
    raw = q.strip().upper()
    if raw.endswith(".BO") and len(raw) > 3:
        base = raw[:-3]
        ident = "BSE_" + re.sub(r"[^A-Z0-9&\-]", "", base)
        if ident not in YAHOO_ALT:
            register(ident, [base + ".BO"])
            META.setdefault(ident, {"k": "stock", "x": "BSE", "u": "", "y": base + ".BO"})
        return ident
    if re.search(r"[=^]|\.[A-Z]{1,3}$", raw) and not raw.endswith(".NS"):
        ident = universe.glb_id(raw)
        if ident not in YAHOO_ALT:
            register(ident, [raw])
            META.setdefault(ident, {"k": "global", "x": "Yahoo", "u": "", "y": raw})
        return ident
    return None


def resolve(q, symbols):
    q0 = (q or "").strip().upper()
    if not q0:
        return None
    if q0.startswith(("BSE_", "GLB_")) and q0 in YAHOO_ALT:
        return q0
    for r in symbols:                      # a Yahoo ticker from the global list, typed as is (BZ=F, ^TNX)
        if r.get("y") and r["y"].upper() == q0:
            return r["s"]
    if q0.endswith(".BO"):
        return ad_hoc(q0)
    q = q0.replace(".NS", "")
    if q in INDICES:
        return INDICES[q]
    alias = {"NIFTY": "IDX_NIFTY50", "NIFTY50": "IDX_NIFTY50", "BANKNIFTY": "IDX_BANKNIFTY"}
    if q in alias:
        return alias[q]
    if any(r["s"] == q for r in symbols):
        return q
    hits = [r for r in symbols if q in r["n"].upper()]
    if len(hits) == 1:
        return hits[0]["s"]
    squeezed = q.replace(" ", "")
    if any(r["s"] == squeezed for r in symbols):
        return squeezed
    if nse.valid_symbol(q) or q.startswith("IDX_"):
        return q
    return ad_hoc(q0)


def display_name(sym, symbols):
    for r in symbols:
        if r["s"] == sym:
            return r["n"]
    return sym


# ---------------------------------------------------------------- data

def market_open(now):
    return now.weekday() < 5 and datetime.time(9, 15) <= now.time() <= datetime.time(15, 35)


def fetch_bars(sym):
    """(bars, source, note). Cache -> Yahoo."""
    if OFFLINE:
        s = nse.load_cached(sym, "daily", root=OFFLINE)
        if s is None:
            raise nse.FetchError("no cached data for %s in %s" % (sym, OFFLINE))
        return to_bars(zip(s["dt"], s["o"], s["h"], s["l"], s["c"], s["v"])), "cache (%s)" % OFFLINE, ""
    now = nse.now_ist().replace(tzinfo=None)
    path = os.path.join(CACHE, sym + ".json")
    doc = nse.read_json(path)
    if doc:
        age = (now - datetime.datetime.fromisoformat(doc["fetched_at"])).total_seconds()
        if age < (900 if open_for(sym, now) else 6 * 3600):
            return doc["bars"], doc["source"], doc.get("note", "")
    s = yahoo_series(sym, "10y", "1d")
    ccy = (s["meta"].get("currency") or ("" if kind_of(sym) == "global" else "INR"))
    bars, source = to_bars(zip(s["dt"], s["o"], s["h"], s["l"], s["c"], s["v"])), \
        "Yahoo Finance (unofficial, %s)" % nse.INDEX_YAHOO.get(sym, nse.yahoo_symbol(sym))
    note = ""
    if bars and open_for(sym, now) and bars[-1]["t"] == now.date().isoformat():
        note = "Today's bar is still forming: readings can change until the close."
    os.makedirs(CACHE, exist_ok=True)
    nse.write_json(path, {"fetched_at": now.isoformat(), "source": source, "note": note, "ccy": ccy, "bars": bars})
    return bars, source, note


def yahoo_series(sym, rng, interval):
    """Yahoo chart for any id. BSE ids try each candidate ticker; global ids accept any currency."""
    cands = YAHOO_ALT.get(sym) or [None]
    last = None
    for i, c in enumerate(cands):
        if c:
            nse.INDEX_YAHOO[sym] = c
        try:
            s = nse.parse_chart(nse.yahoo_chart(sym, rng, interval), any_currency=kind_of(sym) == "global")
            if c and i:
                YAHOO_ALT[sym] = [c] + [x for x in cands if x != c]     # remember the ticker that worked
            return s
        except nse.FetchError as e:
            last = e
    raise last


def cached_ccy(sym):
    doc = nse.read_json(os.path.join(CACHE, sym + ".json")) or {}
    return doc.get("ccy") or ("INR" if kind_of(sym) != "global" else "")


def open_for(sym, now):
    """Is this instrument's market trading now (IST)? Globals: treated as open Mon-Fri round the clock
    (CME/ICE futures trade ~23 h; good enough for cache freshness and live polling)."""
    if kind_of(sym) == "global":
        return now.weekday() < 5 or (now.weekday() == 5 and now.hour < 4)
    return market_open(now)


def live_quote(sym):
    """Latest price for the header and today's candle, from Yahoo (unofficial, can lag)."""
    now = nse.now_ist().replace(tzinfo=None)
    base = {"market_open": open_for(sym, now), "checked": now.isoformat(timespec="seconds")}
    if OFFLINE:
        return dict(base, source="offline", live=False)
    try:
        m = yahoo_series(sym, "1d", "5m")["meta"]
        t = m.get("regularMarketTime")
        return dict(base, source="Yahoo Finance (unofficial, can lag)", live=False, price=m.get("regularMarketPrice"),
                    prev_close=m.get("chartPreviousClose"), high=m.get("regularMarketDayHigh"),
                    low=m.get("regularMarketDayLow"), volume=m.get("regularMarketVolume"), open=None,
                    time=nse.ts_to_ist(t).isoformat(timespec="seconds") if t else None)
    except nse.FetchError as e:
        return dict(base, source="unavailable", live=False, error=str(e))


def bars_for(ident):
    """Price bars for the fundamentals module: a symbol, IDX_*, or 'YAHOO:<ticker>' for a sector index."""
    if ident.startswith("YAHOO:"):
        tick = ident.split(":", 1)[1]
        key = "IDX_Y_" + re.sub(r"[^A-Z0-9]", "_", tick.upper())
        nse.INDEX_YAHOO.setdefault(key, tick)
        ident = key
    return fetch_bars(ident)[0]


def fundamentals_for(sym, refresh=False):
    """Cached daily; filings themselves are cached for good inside fundamentals.Filings."""
    if sym.startswith("IDX_"):
        return {"symbol": sym, "not_applicable": "Fundamentals are for single companies, not indices."}
    if kind_of(sym) == "global":
        return {"symbol": sym, "not_applicable": "Fundamentals are for listed companies. For commodities and yields "
                "the drivers are supply/demand and macro data, which this tab does not read yet."}
    if kind_of(sym) == "bse":
        return {"symbol": sym, "not_applicable": "This company is listed on BSE only. The Fundamentals tab reads NSE "
                "filings, so BSE-only companies are not covered yet."}
    path = os.path.join(fundamentals.FUNDCACHE, sym, "summary.json")
    doc = nse.read_json(path)
    today = nse.now_ist().date().isoformat()
    if doc and not refresh and doc.get("built") == today:
        return doc["data"]
    web = fundamentals.nseweb.NSEWeb(offline=os.path.join(OFFLINE, "filings") if OFFLINE else None, pause=0.6)
    data = fundamentals.build(sym, bars_for, SYMBOLS_CSV, web=web)
    data["built"] = nse.now_ist().replace(tzinfo=None).isoformat(timespec="seconds")
    nse.write_json(path, {"built": today, "data": json.loads(json.dumps(data, default=str))})
    return json.loads(json.dumps(data, default=str))


def relabel(r, P):
    """The engine's text names the default periods; rename them to the user's settings.
    Only unambiguous phrases are touched (the 20-day volume average and the 50-day relative-strength
    average are not indicator settings and keep their names)."""
    bars = r.pop("bars", None)
    t = json.dumps(r, default=str)
    pairs = [("Golden cross (50 above 200)", "Golden cross (%d above %d)" % (P["sma_fast"], P["sma_slow"])),
             ("Death cross (50 below 200)", "Death cross (%d below %d)" % (P["sma_fast"], P["sma_slow"])),
             ("200-day", "%d-day" % P["sma_slow"]), ("SMA 200", "SMA %d" % P["sma_slow"]),
             ("the 50-day (", "the %d-day (" % P["sma_fast"]),
             ("EMA 20", "EMA %d" % P["ema_fast"]), ("EMA 50", "EMA %d" % P["ema_slow"]),
             ("RSI 14", "RSI %d" % P["rsi_n"]), ("ATR(14)", "ATR(%d)" % P["atr_n"])]
    for a, b in pairs:
        if a != b:
            t = t.replace(a, b)
    out = json.loads(t)
    if bars is not None:
        out["bars"] = bars
    return out


def company_for(sym, symbols):
    kind = kind_of(sym)
    yt = nse.INDEX_YAHOO.get(sym, nse.yahoo_symbol(sym))
    last = None
    meta = {}
    try:
        bars = fetch_bars(sym)[0]
        last = bars[-1]["c"] if bars else None
    except Exception:
        pass
    if kind in ("global", "index"):
        try:
            meta = yahoo_series(sym, "5d", "1d")["meta"]
        except Exception as e:
            meta = {"error": str(e)}

    def build():
        d = company.build(sym, kind, last, yt, meta)
        if kind in ("global", "index") and not d.get("name"):
            d["name"] = display_name(sym, symbols)
        m = META.get(sym, {})
        if kind == "global":
            d["name"] = display_name(sym, symbols) if display_name(sym, symbols) != sym else d.get("name")
            if m.get("u"):
                d["identity"].insert(0, company.fact("Quoted in", m["u"], "chartist/data/globals.json"))
            if m.get("x"):
                d["identity"].insert(0, company.fact("Group", m["x"], "chartist/data/globals.json"))
        return d
    if OFFLINE or kind in ("global", "index"):
        return build()
    return company.cached(sym, build)


def to_bars(rows):
    out = {}
    for dt, o, h, l, c, v in rows:
        if None in (o, h, l, c) or c <= 0:
            continue
        t = (dt.date() if hasattr(dt, "date") else dt).isoformat()[:10]
        nd = 2 if float(c) >= 20 else 4          # copper, gas, yields need more than 2 decimals
        out[t] = {"t": t, "o": round(float(o), nd), "h": round(float(h), nd), "l": round(float(l), nd),
                  "c": round(float(c), nd), "v": float(v or 0)}
    return [out[k] for k in sorted(out)]


def analyse(sym, tf, symbols, params=None):
    bars, source, note = fetch_bars(sym)
    if len(bars) < 80:
        first = bars[0]["t"] if bars else "?"
        raise ValueError("%s has only %d trading days of prices (first bar %s, source: %s). Chartist needs at least "
                         "80 (about 4 months) before indicators and patterns mean anything, so a new listing cannot "
                         "be read yet. It will work after about %d more trading days."
                         % (display_name(sym, symbols), len(bars), first, source, 80 - len(bars)))
    bench = {}
    kind = kind_of(sym)
    if sym != "IDX_NIFTY50" and kind != "global":
        try:
            nb, _, _ = fetch_bars("IDX_NIFTY50")
            bench = {b["t"]: b["c"] for b in nb}
        except Exception:
            bench = {}
    use = engine.weekly(bars) if tf == "W" else bars
    if tf == "W":
        bench_w = {}
        for b in engine.weekly([{"t": t, "o": c, "h": c, "l": c, "c": c, "v": 0} for t, c in sorted(bench.items())]):
            bench_w[b["t"]] = b["c"]
        bench = bench_w
    P = engine.clean_params(params)
    r = engine.analyse(use, bench, display_name(sym, symbols), source, P)
    r["id"] = sym
    r["tf"] = tf
    m = META.get(sym, {})
    notes = [note] if note else []
    if kind == "global":
        r["relative"] = {"evidence": ["Not applicable: global instrument, no Nifty benchmark"], "score": 0}
        if m.get("k") == "yield":
            notes.append("This is a YIELD chart: a bullish reading means yields rising, i.e. bond prices falling.")
        if (m.get("y") or "").endswith("=F"):
            notes.append("Continuous front-month futures: a jump on a contract roll is not a real price gap, "
                         "and patterns or gaps across a roll date can be false.")
    if kind in ("global", "bse"):
        notes.append("The verdict was calibrated on Nifty 50 stocks only; it has never been tested on this %s."
                     % ("instrument" if kind == "global" else "BSE-only stock"))
    r["kind"] = "stock" if kind in ("stock", "bse") else kind
    r["exchange"] = "BSE" if kind == "bse" or sym == "IDX_SENSEX" else m.get("x") or "Yahoo" if kind == "global" else "NSE"
    r["currency"] = cached_ccy(sym)
    r["unit"] = m.get("u", "")
    r["ticker"] = nse.INDEX_YAHOO.get(sym, nse.yahoo_symbol(sym))
    if P != engine.DEFAULTS:
        notes.append("Custom indicator settings: signal track records are recomputed on them, but the verdict's "
                     "calibration (calibration.json) was measured on the default settings only.")
    r["note"] = " ".join(notes)
    if kind == "global":                   # the engine's wording assumes a stock
        r = json.loads(json.dumps(r, default=str).replace("on this stock", "on this series"))
    if P != engine.DEFAULTS:
        r = relabel(r, P)
    r["bars"] = use
    r["generated"] = nse.now_ist().replace(tzinfo=None).isoformat(timespec="seconds")
    cal = nse.read_json(os.path.join(HERE, "calibration.json"))
    r["calibration"] = (cal or {}).get("summary")
    return r


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    symbols = []

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "static", "index.html"), "rb") as fh:
                    return self.send(200, fh.read(), "text/html")
            if u.path.startswith("/vendor/"):
                name = os.path.basename(u.path)
                fp = os.path.join(HERE, "static", "vendor", name)
                if os.path.isfile(fp):
                    with open(fp, "rb") as fh:
                        return self.send(200, fh.read(), "application/javascript")
                return self.send(404, json.dumps({"error": "not found"}))
            if u.path == "/api/symbols":
                return self.send(200, json.dumps(Handler.symbols))
            if u.path == "/api/quote":
                sym = resolve((q.get("s") or [""])[0], Handler.symbols)
                if not sym:
                    return self.send(400, json.dumps({"error": "unknown symbol"}))
                return self.send(200, json.dumps(live_quote(sym)))
            if u.path == "/api/fundamentals":
                sym = resolve((q.get("s") or [""])[0], Handler.symbols)
                if not sym:
                    return self.send(400, json.dumps({"error": "unknown symbol"}))
                try:
                    with _FLOCK:
                        r = fundamentals_for(sym, refresh=(q.get("refresh") or ["0"])[0] == "1")
                except Exception as e:
                    return self.send(502, json.dumps({"error": "Could not read NSE filings for %s: %s" % (sym, e)}))
                return self.send(200, json.dumps(r, default=str))
            if u.path == "/api/company":
                sym = resolve((q.get("s") or [""])[0], Handler.symbols)
                if not sym:
                    return self.send(400, json.dumps({"error": "unknown symbol"}))
                try:
                    with _FLOCK:
                        r = company_for(sym, Handler.symbols)
                except Exception as e:
                    return self.send(502, json.dumps({"error": "Could not read company information for %s: %s" % (sym, e)}))
                return self.send(200, json.dumps(r, default=str))
            if u.path == "/api/analyze":
                raw = (q.get("s") or [""])[0]
                tf = (q.get("tf") or ["D"])[0].upper()
                sym = resolve(raw, Handler.symbols)
                if not sym:
                    return self.send(400, json.dumps({"error": "Type an NSE/BSE symbol (RELIANCE, M&M, XYZ.BO), "
                                                               "a company name, an index such as NIFTY 50, or a "
                                                               "Yahoo ticker such as BZ=F or ^TNX."}))
                try:
                    with _LOCK:
                        try:
                            params = json.loads((q.get("p") or ["{}"])[0])
                        except ValueError:
                            params = {}
                        r = analyse(sym, "W" if tf == "W" else "D", Handler.symbols, params)
                except nse.FetchError as e:
                    return self.send(404, json.dumps({"error": "No price data for %s. Check the symbol or Yahoo ticker. (%s)" % (raw.upper(), e)}))
                except ValueError as e:
                    return self.send(422, json.dumps({"error": str(e)}))
                return self.send(200, json.dumps(r))
            return self.send(404, json.dumps({"error": "not found"}))
        except Exception as e:      # keep the server alive; report the error to the page
            return self.send(500, json.dumps({"error": "Analysis failed: %s" % e}))


def main(argv=None):
    global OFFLINE
    ap = argparse.ArgumentParser(description="Chartist: searchable chart analysis for NSE/BSE stocks, commodities and yields. No orders.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--offline", help="read previously cached daily bars from this data folder")
    a = ap.parse_args(argv)
    OFFLINE = a.offline
    Handler.symbols = load_symbols()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = "http://127.0.0.1:%d/" % a.port
    print("Chartist v1.3 running at %s  (%d symbols in search; Ctrl+C to stop)" % (url, len(Handler.symbols)))
    if UNIVERSE_INFO:
        print("  universe %s: NSE %d, BSE-only %d, global %d%s" % (
            UNIVERSE_INFO.get("built"), UNIVERSE_INFO.get("nse", 0), UNIVERSE_INFO.get("bse_only", 0),
            UNIVERSE_INFO.get("global", 0), "".join("\n  problem: " + e for e in UNIVERSE_INFO.get("errors", []))))
    print("Data: %s" % ("cached files in " + OFFLINE if OFFLINE else "Yahoo Finance (unofficial, can lag)"))
    print("Analysis tool only, not investment advice. See DISCLAIMER.md.")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
