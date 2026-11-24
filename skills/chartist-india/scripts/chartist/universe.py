#!/usr/bin/env python3
"""Chartist search universe: every NSE equity, every BSE equity, and global
commodities + benchmark yields that Yahoo Finance carries. Standard library only.

    py chartist\\universe.py            # rebuild chartist\\data\\universe.json (needs internet)
    py chartist\\universe.py --show     # print counts from the saved file

Sources (all UNVERIFIED — CHECK MANUALLY: unofficial download paths that can change):
  NSE main board  nsearchives.nseindia.com/content/equities/EQUITY_L.csv
  NSE SME         nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv
  BSE             api.bseindia.com ListofScripData (active equity scrips)
  Global          chartist\\data\\globals.json (edit it to add/remove tickers)

Ids used inside Chartist (they are also cache file names, so Windows-safe):
  RELIANCE        NSE stock              -> Yahoo RELIANCE.NS
  BSE_<scripid>   BSE stock not on NSE   -> Yahoo <scripid>.BO, then <code>.BO
  GLB_<ticker>    global instrument      -> Yahoo ticker as written (BZ=F, ^TNX ...)
A company on both exchanges is listed once, as its NSE symbol (matched by ISIN).
"""

import csv
import datetime
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
UNIVERSE = os.path.join(DATA, "universe.json")
GLOBALS = os.path.join(DATA, "globals.json")

NSE_MAIN = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"                 # UNVERIFIED URL
NSE_SME = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"      # UNVERIFIED URL
BSE_LIST = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry="
            "&segment=Equity&status=Active")                                                  # UNVERIFIED URL
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

# Yahoo tickers — UNVERIFIED — CHECK MANUALLY (Yahoo renames/drops tickers without notice).
# Futures (=F) are Yahoo's front-month continuous series: the roll from one contract month to
# the next shows as a jump that is NOT a real price gap. k: commodity | yield | bondfut | fx
DEFAULT_GLOBALS = [
    # energy
    {"y": "BZ=F", "n": "Brent crude oil (ICE futures)", "k": "commodity", "g": "Energy", "u": "USD/bbl"},
    {"y": "CL=F", "n": "WTI crude oil (NYMEX futures)", "k": "commodity", "g": "Energy", "u": "USD/bbl"},
    {"y": "NG=F", "n": "Natural gas Henry Hub (NYMEX futures)", "k": "commodity", "g": "Energy", "u": "USD/MMBtu"},
    {"y": "HO=F", "n": "Heating oil / diesel (NYMEX futures)", "k": "commodity", "g": "Energy", "u": "USD/gal"},
    {"y": "RB=F", "n": "RBOB gasoline (NYMEX futures)", "k": "commodity", "g": "Energy", "u": "USD/gal"},
    # metals
    {"y": "GC=F", "n": "Gold (COMEX futures)", "k": "commodity", "g": "Metals", "u": "USD/oz"},
    {"y": "SI=F", "n": "Silver (COMEX futures)", "k": "commodity", "g": "Metals", "u": "USD/oz"},
    {"y": "PL=F", "n": "Platinum (NYMEX futures)", "k": "commodity", "g": "Metals", "u": "USD/oz"},
    {"y": "PA=F", "n": "Palladium (NYMEX futures)", "k": "commodity", "g": "Metals", "u": "USD/oz"},
    {"y": "HG=F", "n": "Copper (COMEX futures)", "k": "commodity", "g": "Metals", "u": "USD/lb"},
    {"y": "ALI=F", "n": "Aluminium (COMEX futures)", "k": "commodity", "g": "Metals", "u": "USD/t"},
    # agriculture
    {"y": "ZC=F", "n": "Corn (CBOT futures)", "k": "commodity", "g": "Agri", "u": "USc/bu"},
    {"y": "ZW=F", "n": "Wheat (CBOT futures)", "k": "commodity", "g": "Agri", "u": "USc/bu"},
    {"y": "ZS=F", "n": "Soybeans (CBOT futures)", "k": "commodity", "g": "Agri", "u": "USc/bu"},
    {"y": "ZL=F", "n": "Soybean oil (CBOT futures)", "k": "commodity", "g": "Agri", "u": "USc/lb"},
    {"y": "ZM=F", "n": "Soybean meal (CBOT futures)", "k": "commodity", "g": "Agri", "u": "USD/short t"},
    {"y": "ZR=F", "n": "Rough rice (CBOT futures)", "k": "commodity", "g": "Agri", "u": "USD/cwt"},
    {"y": "KC=F", "n": "Coffee arabica (ICE futures)", "k": "commodity", "g": "Agri", "u": "USc/lb"},
    {"y": "SB=F", "n": "Sugar No.11 (ICE futures)", "k": "commodity", "g": "Agri", "u": "USc/lb"},
    {"y": "CC=F", "n": "Cocoa (ICE futures)", "k": "commodity", "g": "Agri", "u": "USD/t"},
    {"y": "CT=F", "n": "Cotton No.2 (ICE futures)", "k": "commodity", "g": "Agri", "u": "USc/lb"},
    {"y": "OJ=F", "n": "Orange juice (ICE futures)", "k": "commodity", "g": "Agri", "u": "USc/lb"},
    {"y": "LE=F", "n": "Live cattle (CME futures)", "k": "commodity", "g": "Agri", "u": "USc/lb"},
    {"y": "HE=F", "n": "Lean hogs (CME futures)", "k": "commodity", "g": "Agri", "u": "USc/lb"},
    # benchmark yields (Yahoo's cash-index series, in percent)
    {"y": "^IRX", "n": "US 13-week T-bill yield", "k": "yield", "g": "Yields", "u": "%"},
    {"y": "^FVX", "n": "US 5-year Treasury yield", "k": "yield", "g": "Yields", "u": "%"},
    {"y": "^TNX", "n": "US 10-year Treasury yield", "k": "yield", "g": "Yields", "u": "%"},
    {"y": "^TYX", "n": "US 30-year Treasury yield", "k": "yield", "g": "Yields", "u": "%"},
    # Treasury futures (PRICE, not yield: up = yields down)
    {"y": "ZT=F", "n": "US 2-year Treasury note futures (price)", "k": "bondfut", "g": "Bond futures", "u": "pts"},
    {"y": "ZF=F", "n": "US 5-year Treasury note futures (price)", "k": "bondfut", "g": "Bond futures", "u": "pts"},
    {"y": "ZN=F", "n": "US 10-year Treasury note futures (price)", "k": "bondfut", "g": "Bond futures", "u": "pts"},
    {"y": "ZB=F", "n": "US Treasury bond futures (price)", "k": "bondfut", "g": "Bond futures", "u": "pts"},
    # context series
    {"y": "DX-Y.NYB", "n": "US dollar index (DXY)", "k": "fx", "g": "FX", "u": "index"},
    {"y": "INR=X", "n": "USD/INR (Yahoo)", "k": "fx", "g": "FX", "u": "INR"},
]


def glb_id(ticker):
    return "GLB_" + re.sub(r"[^A-Z0-9]", "_", ticker.upper()).strip("_")


def _get(url, referer=None, timeout=40):
    hdr = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        hdr["Referer"] = referer
        hdr["Origin"] = referer.rstrip("/")
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":
        import gzip
        data = gzip.decompress(data)
    return data.decode("utf-8-sig", "replace")


def _col(row, *names):
    """Header-tolerant column read (NSE headers carry stray spaces)."""
    low = {(k or "").strip().upper(): v for k, v in row.items()}
    for n in names:
        v = low.get(n.upper())
        if v not in (None, ""):
            return v.strip()
    return ""


def parse_nse(text, sme=False):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        s = _col(r, "SYMBOL").upper()
        if not s or s.startswith("DUMMY"):
            continue
        out.append({"s": s, "n": _col(r, "NAME OF COMPANY", "NAME_OF_COMPANY", "COMPANY NAME") or s,
                    "k": "stock", "x": "NSE SME" if sme else "NSE", "isin": _col(r, "ISIN NUMBER", "ISIN")})
    return out


def parse_bse(text):
    doc = json.loads(text)
    rows = doc if isinstance(doc, list) else (doc.get("Table") or doc.get("data") or [])
    out = []
    for r in rows:
        low = {str(k).lower(): v for k, v in r.items()}
        code = str(low.get("scrip_cd") or low.get("scripcode") or "").strip()
        sid = str(low.get("scrip_id") or low.get("scripid") or "").strip().upper()
        name = str(low.get("scrip_name") or low.get("issuer_name") or low.get("scripname") or sid).strip()
        isin = str(low.get("isin_number") or low.get("isin") or "").strip()
        status = str(low.get("status") or "Active").strip().lower()
        if not (code or sid) or status not in ("active", ""):
            continue
        out.append({"code": code, "sid": sid or code, "n": name, "isin": isin})
    return out


def load_globals():
    if not os.path.exists(GLOBALS):
        os.makedirs(DATA, exist_ok=True)
        with open(GLOBALS, "w", encoding="utf-8") as fh:
            json.dump({"_note": "Yahoo tickers shown in Chartist search. y = Yahoo ticker, n = name, "
                                "k = commodity|yield|bondfut|fx, g = group, u = unit. Tickers UNVERIFIED — CHECK MANUALLY.",
                       "items": DEFAULT_GLOBALS}, fh, indent=1)
    with open(GLOBALS, encoding="utf-8") as fh:
        items = json.load(fh).get("items") or []
    return [{"s": glb_id(g["y"]), "n": g["n"], "k": g.get("k", "commodity"), "x": g.get("g", "Global"),
             "y": g["y"], "u": g.get("u", "")} for g in items if g.get("y")]


def build(log=print):
    """Download the lists and write universe.json. Keeps the old section if a download fails."""
    old = {}
    if os.path.exists(UNIVERSE):
        with open(UNIVERSE, encoding="utf-8") as fh:
            old = json.load(fh)
    nse_rows, bse_rows, errors = [], [], []
    for url, sme in ((NSE_MAIN, False), (NSE_SME, True)):
        try:
            got = parse_nse(_get(url, "https://www.nseindia.com/"), sme)
            if not got:
                raise ValueError("empty list")
            nse_rows += got
            log("NSE%s: %d symbols" % (" SME" if sme else "", len(got)))
        except Exception as e:
            errors.append("NSE%s list: %s" % (" SME" if sme else "", e))
    if not nse_rows:
        nse_rows = old.get("nse") or []
    try:
        bse_rows = parse_bse(_get(BSE_LIST, "https://www.bseindia.com/"))
        if not bse_rows:
            raise ValueError("empty list")
        log("BSE: %d active scrips" % len(bse_rows))
    except Exception as e:
        errors.append("BSE list: %s" % e)
        bse_rows = old.get("bse_raw") or []
    doc = {"built": datetime.datetime.now().isoformat(timespec="seconds"), "nse": nse_rows,
           "bse_raw": bse_rows, "errors": errors}
    os.makedirs(DATA, exist_ok=True)
    tmp = UNIVERSE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, UNIVERSE)
    for e in errors:
        log("WARNING " + e)
    return doc


def entries(nifty500_rows=None, log=print, allow_download=True, max_age_days=7):
    """The search list: [{s, n, k, x, y?, u?}], plus {id: yahoo candidates} for BSE/global ids.
    nifty500_rows: fallback NSE rows when no universe file can be had."""
    doc = None
    if os.path.exists(UNIVERSE):
        with open(UNIVERSE, encoding="utf-8") as fh:
            doc = json.load(fh)
        age = datetime.datetime.now() - datetime.datetime.fromisoformat(doc.get("built", "2000-01-01T00:00:00"))
        if allow_download and age.days >= max_age_days:
            try:
                doc = build(log)
            except Exception as e:
                log("universe refresh failed (%s): using the saved list" % e)
    elif allow_download:
        try:
            doc = build(log)
        except Exception as e:
            log("universe download failed (%s)" % e)
    doc = doc or {"nse": [], "bse_raw": []}
    rows, yahoo = {}, {}
    for r in doc.get("nse") or []:
        rows.setdefault(r["s"], {"s": r["s"], "n": r["n"], "k": "stock", "x": r.get("x", "NSE")})
    for r in nifty500_rows or []:
        rows.setdefault(r["s"], dict(r, x="NSE"))
    nse_isin = {r.get("isin") for r in doc.get("nse") or [] if r.get("isin")}
    nse_syms = set(rows)
    bse_only = 0
    for r in doc.get("bse_raw") or []:
        if r.get("isin") and r["isin"] in nse_isin:
            continue                                     # dual-listed: keep the NSE line only
        if not r.get("isin") and r["sid"] in nse_syms:
            continue
        sid = re.sub(r"[^A-Z0-9&\-]", "", r["sid"].upper()) or r["code"]
        ident = "BSE_" + sid
        cands = [sid + ".BO"] + ([r["code"] + ".BO"] if r.get("code") and r["code"] != sid else [])
        yahoo[ident] = cands
        rows.setdefault(ident, {"s": ident, "n": r["n"], "k": "stock", "x": "BSE", "code": r.get("code", "")})
        bse_only += 1
    glb = load_globals()
    for g in glb:
        yahoo[g["s"]] = [g["y"]]
    stocks = sorted(rows.values(), key=lambda r: r["s"])
    info = {"built": doc.get("built"), "nse": sum(1 for r in stocks if r["x"].startswith("NSE")),
            "bse_only": bse_only, "global": len(glb), "errors": doc.get("errors") or []}
    return glb + stocks, yahoo, info


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--show" not in argv:
        build()
    items, _, info = entries(allow_download=False)
    print("universe built %s: NSE %d, BSE-only %d, global %d (total %d)" % (
        info["built"], info["nse"], info["bse_only"], info["global"], len(items)))
    for e in info["errors"]:
        print("  problem: " + e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
