#!/usr/bin/env python3
"""Market plumbing for Chartist. Standard library only.

  clock    IST (fixed UTC+05:30, no daylight saving)
  symbols  NSE symbol rule and the Yahoo mapping (SYMBOL.NS, index tickers)
  yahoo    fetch + parse of Yahoo's free chart JSON (unofficial, can lag)
  cache    one JSON shape for cached bars: data/<kind>/<SYM>.json

Every market fact here is UNVERIFIED unless marked otherwise: check it before you rely on it.
"""

import datetime
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")


UTC = datetime.timezone.utc


def now_ist():
    return datetime.datetime.now(IST).replace(microsecond=0)


def ts_to_ist(ts):
    """Unix seconds -> naive IST datetime."""
    return datetime.datetime.fromtimestamp(int(ts), IST).replace(tzinfo=None)


def ist_to_ts(dt):
    """Naive IST datetime -> unix seconds."""
    return int(dt.replace(tzinfo=IST).timestamp())


SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # M&M, BAJAJ-AUTO, 3MINDIA


def valid_symbol(sym):
    return bool(SYMBOL.match(str(sym or "")))


INDEX_YAHOO = {   # Yahoo index tickers — UNVERIFIED — CHECK MANUALLY
    "IDX_NIFTY50": "^NSEI",
    "IDX_NIFTY100": "^CNX100",
}


def yahoo_symbol(sym):
    s = str(sym).upper()
    if s in INDEX_YAHOO:
        return INDEX_YAHOO[s]
    return s + ".NS"


def from_yahoo(ysym):
    s = str(ysym).upper()
    return s[:-3] if s.endswith(".NS") else s


UA = "Mozilla/5.0 (compatible; chartist-india/1.3)"


YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")


class FetchError(Exception):
    pass


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data.decode("utf-8", "replace")


def yahoo_chart(sym, rng, interval, pause=0.5):
    """Yahoo v8 chart for an NSE symbol. Unofficial and can lag."""
    path = "/v8/finance/chart/%s?range=%s&interval=%s" % (
        urllib.parse.quote(yahoo_symbol(sym)), rng, interval)
    last = None
    for host in YAHOO_HOSTS:
        for attempt in range(2):
            try:
                return json.loads(http_get(host + path))
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429 and attempt == 0:
                    time.sleep(5 + pause)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                raise FetchError("Yahoo %s for %s" % (e.code, sym))
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = e
                break
    raise FetchError("Yahoo unreachable for %s: %s" % (sym, last))


def parse_chart(doc, any_currency=False):
    """Yahoo v8 chart JSON -> {'t','o','h','l','c','v','meta'} (lists, IST naive datetimes in 'dt').
    any_currency=True for global instruments (commodities, yields): skips the INR-only guard."""
    if isinstance(doc, str):
        doc = json.loads(doc)
    ch = (doc or {}).get("chart") or {}
    if ch.get("error"):
        raise FetchError("Yahoo: %s" % (ch["error"].get("description") or ch["error"]))
    res = (ch.get("result") or [None])[0]
    if not res:
        raise FetchError("Yahoo returned no result")
    meta = res.get("meta") or {}
    cur = meta.get("currency")
    if cur and cur != "INR" and not any_currency:
        raise FetchError("not an INR series (currency %s): wrong symbol or exchange" % cur)
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    ts = res.get("timestamp") or []
    out = {"t": ts, "dt": [ts_to_ist(x) for x in ts], "meta": meta}
    for k, name in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
        col = q.get(name) or [None] * len(ts)
        out[k] = list(col)
    return out


def read_json_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        return json.load(fh)


DATA_DIR = "data"


def series_from_bars(bars, meta=None):
    out = {"t": [], "dt": [], "o": [], "h": [], "l": [], "c": [], "v": [], "meta": meta or {}}
    for b in bars:
        dt = datetime.datetime.fromisoformat(str(b[0])[:19])
        out["dt"].append(dt)
        out["t"].append(ist_to_ts(dt))
        for k, x in zip("ohlcv", b[1:6]):
            out[k].append(None if x is None else float(x))
    return out


def save_cached(sym, kind, bars, source, interval, extra=None, root=None):
    d = os.path.join(root or DATA_DIR, kind)
    os.makedirs(d, exist_ok=True)
    doc = {"source": source, "symbol": sym, "interval": interval,
           "fetched_at": now_ist().replace(tzinfo=None).isoformat(),
           "bars": [[b[0].isoformat() if hasattr(b[0], "isoformat") else b[0]] + list(b[1:6]) for b in bars]}
    if extra:
        doc.update(extra)
    tmp = os.path.join(d, sym + ".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(doc, fh)
    os.replace(tmp, os.path.join(d, sym + ".json"))


def load_cached(sym, kind, root=None):
    """Cached series for a symbol, or None. Accepts both cache shapes."""
    path = os.path.join(root or DATA_DIR, kind, sym + ".json")
    if not os.path.exists(path):
        if os.path.exists(path + ".gz"):
            path += ".gz"
        else:
            return None
    doc = read_json_any(path)
    if "chart" in doc:
        return parse_chart(doc)
    s = series_from_bars(doc.get("bars") or [], {"source": doc.get("source"), "fetched_at": doc.get("fetched_at")})
    return s


def merge_bars(old, new):
    """Union of two bar lists keyed by time; the newer value wins."""
    by = {str(b[0])[:19]: b for b in (old or [])}
    for b in new or []:
        by[str(b[0])[:19]] = b
    return [by[k] for k in sorted(by)]


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=2, default=str)
    os.replace(tmp, path)
