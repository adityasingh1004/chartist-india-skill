#!/usr/bin/env python3
"""Company information for Chartist's Company tab. Standard library only.

    py chartist\\company.py RELIANCE            # print what the tab would show
    py chartist\\company.py RELIANCE --raw      # also dump the raw NSE / Yahoo responses (to check field names)

Sources, each field tagged with where it came from:
  NSE    nseindia.com /api/quote-equity (the exchange's own quote page data): name, ISIN, listing date,
         face value, issued shares, sector/industry classification, index memberships, F&O, surveillance
         (ASM/GSM), price band, trading status. Unofficial website API: path and field names
         UNVERIFIED — CHECK MANUALLY (open the stock's page on nseindia.com, watch the network tab).
  Yahoo  quoteSummary assetProfile (needs a cookie + crumb): business description, website, employees,
         address, key officers. Unofficial; descriptions are Yahoo's / its data vendor's text, not the
         company's filing. UNVERIFIED — CHECK MANUALLY.
  Lists  chartist/data/universe.json (NSE equity list, BSE scrip list): ISIN and BSE scrip code.
Market cap here = issued shares (NSE) x last close, computed by this app; it is labelled as derived.
Results are cached in chartist/fundcache/<SYM>/company.json and rebuilt once a day.
"""

import datetime
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, HERE]
from common import nse, nseweb  # noqa: E402

FUNDCACHE = os.path.join(HERE, "fundcache")
UNIVERSE = os.path.join(HERE, "data", "universe.json")
UA = nseweb.UA
nseweb.ENDPOINTS.setdefault("quote", "/api/quote-equity?symbol={sym}")          # UNVERIFIED — CHECK MANUALLY


def dig(doc, path, default=None):
    """doc['a']['b'][0] from 'a.b.0', or default when any step is missing."""
    cur = doc
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        elif isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        else:
            return default
    return default if cur in (None, "", "-", "NA") else cur


def first(doc, *paths):
    for p in paths:
        v = dig(doc, p)
        if v is not None:
            return v, p
    return None, None


# ---------------------------------------------------------------- list data

def list_row(sym):
    """Row from universe.json for an NSE symbol or a BSE_ id."""
    try:
        with open(UNIVERSE, encoding="utf-8") as fh:
            u = json.load(fh)
    except (OSError, ValueError):
        return {}
    if sym.startswith("BSE_"):
        sid = sym[4:]
        for r in u.get("bse_raw") or []:
            if r.get("sid", "").upper().replace(" ", "") == sid or r.get("code") == sid:
                return {"name": r.get("n"), "isin": r.get("isin"), "bse_code": r.get("code"), "exchange": "BSE"}
        return {}
    nse_row = next((r for r in u.get("nse") or [] if r.get("s") == sym), None)
    if not nse_row:
        return {}
    out = {"name": nse_row.get("n"), "isin": nse_row.get("isin"), "exchange": nse_row.get("x", "NSE")}
    if nse_row.get("isin"):
        b = next((r for r in u.get("bse_raw") or [] if r.get("isin") == nse_row["isin"]), None)
        if b:
            out["bse_code"] = b.get("code")
    return out


# ---------------------------------------------------------------- Yahoo profile

class Yahoo:
    """quoteSummary with the cookie + crumb Yahoo now requires. Every step UNVERIFIED — CHECK MANUALLY."""

    def __init__(self):
        jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.op.addheaders = [("User-Agent", UA), ("Accept", "*/*"), ("Accept-Language", "en-US,en;q=0.9")]
        self.crumb = None

    def _crumb(self):
        if self.crumb:
            return self.crumb
        for url in ("https://fc.yahoo.com", "https://finance.yahoo.com/"):
            try:
                self.op.open(url, timeout=15).read(2000)
            except urllib.error.HTTPError:
                pass                                   # fc.yahoo.com answers 404 but still sets the cookie
            except Exception:
                continue
        for host in ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"):
            try:
                c = self.op.open(host + "/v1/test/getcrumb", timeout=15).read().decode().strip()
                if c and "<" not in c and len(c) < 40:
                    self.crumb = c
                    return c
            except Exception:
                continue
        raise RuntimeError("Yahoo crumb not issued (blocked or changed)")

    def summary(self, ticker, modules="assetProfile,price,quoteType"):
        crumb = self._crumb()
        url = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s&crumb=%s" % (
            urllib.parse.quote(ticker), modules, urllib.parse.quote(crumb))
        doc = json.loads(self.op.open(url, timeout=20).read().decode("utf-8", "replace"))
        res = (dig(doc, "quoteSummary.result") or [None])[0]
        if not res:
            raise RuntimeError("Yahoo: %s" % (dig(doc, "quoteSummary.error.description") or "no profile"))
        return res


# ---------------------------------------------------------------- build

def fact(label, value, source, note=None, link=None):
    return {"label": label, "value": value, "source": source, "note": note, "link": link}


def num_text(x, digits=0):
    try:
        return format(round(float(x), digits), ",.%df" % digits)
    except (TypeError, ValueError):
        return None


def crore(x):
    try:
        return "Rs %s cr" % format(round(float(x) / 1e7), ",")
    except (TypeError, ValueError):
        return None


def build(sym, kind, last_close=None, yahoo_ticker=None, meta=None, web=None, ysrc=None, raw=None):
    """Company card data. kind: 'stock' (NSE), 'bse', 'index', 'global'. meta: Yahoo chart meta (any kind)."""
    today = datetime.date.today().isoformat()
    out = {"symbol": sym, "kind": kind, "built": datetime.datetime.now().isoformat(timespec="seconds"),
           "identity": [], "classification": [], "trading": [], "about": None, "officers": [],
           "links": {}, "errors": []}
    meta = meta or {}
    if kind in ("global", "index"):
        g = meta
        out["name"] = g.get("longName") or g.get("shortName") or sym
        out["identity"] = [f for f in [
            fact("Yahoo ticker", yahoo_ticker, "Yahoo chart"),
            fact("Instrument type", g.get("instrumentType"), "Yahoo chart"),
            fact("Exchange", g.get("fullExchangeName") or g.get("exchangeName"), "Yahoo chart"),
            fact("Currency", g.get("currency"), "Yahoo chart"),
            fact("Exchange time zone", g.get("exchangeTimezoneName") or g.get("timezone"), "Yahoo chart"),
        ] if f["value"]]
        if kind == "global" and (yahoo_ticker or "").endswith("=F"):
            out["notes"] = ["Front-month continuous futures as stitched by Yahoo. Contract size, tick, expiry and "
                            "trading hours are set by the exchange: check its contract specification page."]
        out["not_company"] = ("This is %s, not a company: there is no business profile or shareholding. "
                              "The details below are from Yahoo's chart data (unofficial)."
                              % ("an index" if kind == "index" else "a commodity / rate series"))
        return out

    lst = list_row(sym)
    out["name"] = lst.get("name") or sym
    ex_sym = sym[4:] if kind == "bse" else sym
    if lst.get("isin"):
        out["identity"].append(fact("ISIN", lst["isin"], "NSE/BSE equity list"))
    out["identity"].append(fact("Listed on", "BSE only" if kind == "bse" else
                                "NSE + BSE" if lst.get("bse_code") else lst.get("exchange") or "NSE",
                                "NSE/BSE equity lists"))
    if lst.get("bse_code"):
        out["identity"].append(fact("BSE scrip code", lst["bse_code"], "BSE scrip list"))

    # ---- NSE quote page data
    if kind == "stock":
        out["links"]["nse"] = "https://www.nseindia.com/get-quotes/equity?symbol=" + urllib.parse.quote(sym)
        try:
            q = (web or nseweb.NSEWeb(pause=0.6)).get("quote", sym=sym)
            if raw is not None:
                raw["nse_quote"] = q
            src = "NSE quote data, " + today
            name, _ = first(q, "info.companyName", "metadata.companyName")
            if name:
                out["name"] = name
            for label, paths, fmt in [
                ("Listing date", ("metadata.listingDate", "info.listingDate", "securityInfo.listingDate"), None),
                ("Face value", ("securityInfo.faceValue", "info.faceValue"), "rs"),
                ("Issued shares", ("securityInfo.issuedSize", "securityInfo.issuedCap"), "num"),
                ("Series", ("metadata.series", "info.series"), None),
                ("Class of share", ("securityInfo.classOfShare",), None),
            ]:
                v, _ = first(q, *paths)
                if v is not None:
                    shown = ("Rs %s" % v) if fmt == "rs" else num_text(v) if fmt == "num" else v
                    out["identity"].append(fact(label, shown, src))
            issued, _ = first(q, "securityInfo.issuedSize")
            if issued and last_close:
                out["identity"].append(fact("Market cap (derived)", crore(float(issued) * float(last_close)),
                                            "issued shares (NSE) x last close, computed by Chartist",
                                            "Moves with the price; check against NSE before quoting."))
            for label, path in [("Macro sector", "industryInfo.macro"), ("Sector", "industryInfo.sector"),
                                ("Industry", "industryInfo.industry"), ("Basic industry", "industryInfo.basicIndustry")]:
                v = dig(q, path)
                if v:
                    out["classification"].append(fact(label, v, src))
            if not out["classification"]:
                v, _ = first(q, "metadata.industry", "info.industry")
                if v:
                    out["classification"].append(fact("Industry", v, src))
            sect_idx = dig(q, "metadata.pdSectorInd")
            if sect_idx:
                out["classification"].append(fact("Sector index", str(sect_idx).strip(), src))
            idx_all = dig(q, "metadata.pdSectorIndAll")
            if isinstance(idx_all, list) and idx_all:
                out["classification"].append(fact("Member of indices", ", ".join(str(x) for x in idx_all[:12])
                                                  + (" and %d more" % (len(idx_all) - 12) if len(idx_all) > 12 else ""), src))
            fno, _ = first(q, "info.isFNOSec", "securityInfo.derivatives")
            if fno is not None:
                out["trading"].append(fact("Futures & options", "Yes" if str(fno).lower() in ("true", "yes") else "No", src))
            for label, paths in [("Trading status", ("securityInfo.tradingStatus",)),
                                 ("Board", ("securityInfo.boardStatus",)),
                                 ("Price band", ("priceInfo.pPriceBand",)),
                                 ("Upper / lower circuit", ("priceInfo.upperCP",))]:
                v, _ = first(q, *paths)
                if v is not None:
                    if label.startswith("Upper"):
                        v = "%s / %s" % (v, dig(q, "priceInfo.lowerCP", "?"))
                    out["trading"].append(fact(label, v, src))
            surv = dig(q, "securityInfo.surveillance.surv")
            desc = dig(q, "securityInfo.surveillance.desc")
            out["trading"].append(fact("Surveillance (ASM/GSM)", surv or "None shown", src, desc))
            pe_sym, pe_sec = dig(q, "metadata.pdSymbolPe"), dig(q, "metadata.pdSectorPe")
            if pe_sym or pe_sec:
                out["trading"].append(fact("P/E (stock / sector)", "%s / %s" % (pe_sym or "–", pe_sec or "–"), src,
                                           "As shown on NSE's quote page."))
        except Exception as e:
            out["errors"].append("NSE quote data: %s" % e)

    # ---- Yahoo profile
    if yahoo_ticker:
        out["links"]["yahoo"] = "https://finance.yahoo.com/quote/" + urllib.parse.quote(yahoo_ticker) + "/profile"
        try:
            y = (ysrc or Yahoo()).summary(yahoo_ticker)
            if raw is not None:
                raw["yahoo"] = y
            ap = y.get("assetProfile") or {}
            ysrc_lbl = "Yahoo profile (unofficial), " + today
            out["about"] = {"text": ap.get("longBusinessSummary"), "source": ysrc_lbl} if ap.get("longBusinessSummary") else None
            if not out["classification"]:
                for label, k in (("Sector", "sector"), ("Industry", "industry")):
                    if ap.get(k):
                        out["classification"].append(fact(label, ap[k], ysrc_lbl))
            addr = ", ".join(x for x in (ap.get("address1"), ap.get("city"), ap.get("state"), ap.get("country")) if x)
            for label, v in (("Headquarters", addr), ("Website", ap.get("website")),
                             ("Employees", num_text(ap.get("fullTimeEmployees")) if ap.get("fullTimeEmployees") else None),
                             ("Phone", ap.get("phone"))):
                if v:
                    out["identity"].append(fact(label, v, ysrc_lbl, link=v if label == "Website" else None))
            if ap.get("website"):
                out["links"]["website"] = ap["website"]
            for o in (ap.get("companyOfficers") or [])[:8]:
                if o.get("name"):
                    out["officers"].append({"name": o["name"], "title": o.get("title") or ""})
            if out["officers"]:
                out["officers_source"] = ysrc_lbl
        except Exception as e:
            out["errors"].append("Yahoo profile: %s" % e)
    if kind == "bse" and lst.get("bse_code"):
        out["links"]["bse"] = "https://www.bseindia.com/stock-share-price/x/%s/%s/" % (
            urllib.parse.quote(ex_sym.lower()), lst["bse_code"])
    if kind == "stock":
        out["links"]["annual_reports"] = ("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports?symbol="
                                          + urllib.parse.quote(sym))
    return out


def cached(sym, builder):
    path = os.path.join(FUNDCACHE, sym, "company.json")
    doc = nse.read_json(path)
    today = datetime.date.today().isoformat()
    if doc and doc.get("day") == today:
        return doc["data"]
    data = builder()
    if not (data.get("errors") and len(data["errors"]) >= 2):        # do not cache a total failure
        nse.write_json(path, {"day": today, "data": data})
    return data


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    sym = argv[0].upper()
    raw = {} if "--raw" in argv else None
    kind = "bse" if sym.startswith("BSE_") else "stock"
    yt = (sym[4:] + ".BO") if kind == "bse" else sym + ".NS"
    d = build(sym, kind, yahoo_ticker=yt, raw=raw)
    print(json.dumps(d, indent=1))
    if raw is not None:
        print("\n---- RAW ----")
        print(json.dumps(raw, indent=1)[:20000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
