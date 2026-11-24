#!/usr/bin/env python3
"""Chartist fundamentals: valuation vs the stock's own history, quality and growth (or the bank
view), red-flag checks and a one-line verdict, built from NSE's own filings. Standard library only.

    py chartist\\fundamentals.py ITC RELIANCE HDFCBANK            # print the panel as text
    py chartist\\fundamentals.py ITC --save probe                  # also save NSE's raw answers to probe\\
    py chartist\\fundamentals.py ITC --offline probe               # rebuild from saved answers (no internet)

SOURCES (all primary, all fetched by this program on your computer, no AI, no broker login):
  NSE corporate filings (quarterly results list + each filing's XBRL file, shareholding pattern,
  pledge disclosures, corporate actions, announcements) through common/nseweb.py, and daily prices
  through the same Yahoo/cache path the chart uses. NSE's website API is UNOFFICIAL: endpoint paths,
  JSON field names and XBRL tag names below are UNVERIFIED — CHECK MANUALLY. Anything not found is
  shown as "Not available", never estimated. Every figure carries its source and as-of date.

Fundamentals change once a quarter, so each filing is cached for good (chartist/fundcache/SYM/) and
the summary is rebuilt at most once a day.
"""

import argparse
import bisect
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from common import nseweb  # noqa: E402

FUNDCACHE = os.path.join(HERE, "fundcache")
nseweb.ENDPOINTS.setdefault("corp_actions", "/api/corporates-corporateActions?index=equities&symbol={sym}")
nseweb.ENDPOINTS.setdefault("results_annual", "/api/corporates-financial-results?index=equities&symbol={sym}&period=Annual")
# Since 2025 results are filed as SEBI "Integrated Filing (Financials)"; the old results list stops at Dec 2024
# (seen in real NSE answers, Nov 2026). Path from the open-source nsepython project; fields UNVERIFIED.
nseweb.ENDPOINTS.setdefault("results_integrated",
                            "/api/integrated-filing-results?index=equities&symbol={sym}&type=Integrated%20Filing-%20Financials")
FILINGS_PAGE = "https://www.nseindia.com/get-quotes/equity?symbol=%s"           # human link for MANUAL REVIEW items
ANNUAL_REPORTS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports?symbol=%s"
SRC_XBRL = "NSE results XBRL"
SRC_SHP = "NSE shareholding pattern"
SRC_PLEDGE = "NSE pledge disclosures"
SRC_CA = "NSE corporate actions"
SRC_ANN = "NSE announcements"
SRC_PRICE = "Prices (chart data)"

# ------------------------------------------------------------------ XBRL tags (UNVERIFIED — CHECK MANUALLY)
# Ind-AS "in-capmkt" taxonomy names as used in NSE results filings. Several candidates per item; the first
# one present wins. Run with --save on a real filing and compare with the tag list it prints.
TAGS = {
    "revenue": ("RevenueFromOperations", "Revenue", "TotalRevenueFromOperations"),
    "other_income": ("OtherIncome",),
    "total_income": ("Income", "TotalIncome"),
    "finance_cost": ("FinanceCosts", "FinanceCost"),
    "depreciation": ("DepreciationDepletionAndAmortisationExpense", "DepreciationAndAmortisationExpense"),
    "exceptional": ("ExceptionalItemsBeforeTax", "ExceptionalItems"),
    "pbt": ("ProfitBeforeTax", "ProfitLossBeforeTax"),
    "pat": ("ProfitLossForPeriod", "ProfitLossForThePeriod", "ProfitLossForPeriodFromContinuingOperations"),
    "eps": ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsPerShare"),
    "paid_up": ("PaidUpValueOfEquityShareCapital",),
    "face_value": ("FaceValueOfEquityShareCapital",),
    # balance sheet / cash flow (half-yearly and annual filings)
    "equity": ("EquityAttributableToOwnersOfParent", "Equity", "TotalEquity"),
    "borrow_nc": ("BorrowingsNoncurrent", "NoncurrentBorrowings"),
    "borrow_c": ("BorrowingsCurrent", "CurrentBorrowings"),
    "receivables": ("TradeReceivablesCurrent", "TradeReceivables"),
    "inventories": ("Inventories",),
    "cwip": ("CapitalWorkInProgress",),
    "unbilled": ("UnbilledRevenue", "ContractAssetsCurrent"),
    "total_assets": ("Assets", "TotalAssets"),
    "ocf": ("CashFlowsFromUsedInOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities"),
    # banks (banking results format)
    "gnpa_pct": ("PercentageOfGrossNpa", "GrossNpaPercentage", "PercentageOfGrossNonPerformingAssets"),
    "nnpa_pct": ("PercentageOfNpa", "NetNpaPercentage", "PercentageOfNetNonPerformingAssets"),
    "gnpa_amt": ("GrossNonPerformingAssets", "GrossNpa"),
    "nnpa_amt": ("NetNonPerformingAssets", "NetNpa"),
    "crar": ("CapitalAdequacyRatio", "CapitalAdequacyRatioBaselIii", "CapitalAdequacyRatioBasel3"),
    "roa": ("ReturnOnAssets", "ReturnOnAssetsAnnualised"),
    "op_profit": ("OperatingProfitBeforeProvisionAndContingencies", "OperatingProfitBeforeProvisionsAndContingencies"),
    "provisions": ("ProvisionsOtherThanTaxAndContingencies", "ProvisionsAndContingencies"),
    "interest_earned": ("InterestEarned",),
}
BANK_KEYS = ("gnpa_pct", "nnpa_pct", "crar", "interest_earned")
CYCLICAL = ("metal", "mining", "steel", "oil", "gas", "petro", "sugar", "cement", "construction materials",
            "chemical", "fertil", "paper", "commodit", "aluminium", "copper", "zinc", "power", "textile")
SECTOR_INDEX = {   # Nifty 500 'Industry' -> Yahoo ticker of the matching NSE sector index (UNVERIFIED — CHECK MANUALLY)
    "information technology": "^CNXIT", "fast moving consumer goods": "^CNXFMCG", "healthcare": "^CNXPHARMA",
    "automobile and auto components": "^CNXAUTO", "metals & mining": "^CNXMETAL", "oil gas & consumable fuels": "^CNXENERGY",
    "power": "^CNXENERGY", "realty": "^CNXREALTY", "media entertainment & publication": "^CNXMEDIA",
    "financial services": "NIFTY_FIN_SERVICE.NS", "construction materials": "^CNXINFRA", "construction": "^CNXINFRA",
}


def num(x):
    try:
        v = float(str(x).replace(",", "").strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def parse_date(s):
    """NSE dates come as '30-Sep-2026', '30-SEP-2026', '2026-09-30' or '30/09/2026'."""
    if not s:
        return None
    s = str(s).strip()[:11]
    for f in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s.title() if "%b" in f else s, f).date()
        except ValueError:
            continue
    return None


def qlabel(d):
    """Indian fiscal quarter label for a period-end date: 2026-09-30 -> 'Q2 FY27'."""
    if not d:
        return "?"
    fy = d.year + 1 if d.month >= 4 else d.year
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month, 0)
    return ("Q%d FY%02d" % (q, fy % 100)) if q else d.isoformat()


# ------------------------------------------------------------------ XBRL reader

def xbrl_contexts(text):
    out = {}
    for m in re.finditer(r"<(?:[\w\-]+:)?context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</(?:[\w\-]+:)?context>", text, re.S):
        cid, body = m.group(1), m.group(2)
        g = lambda tag: (re.search(r"<(?:[\w\-]+:)?%s>\s*([^<\s]+)\s*<" % tag, body) or [None, None])[1]
        out[cid] = {"start": parse_date(g("startDate")), "end": parse_date(g("endDate")),
                    "instant": parse_date(g("instant")), "dims": bool(re.search(r"explicitMember|typedMember", body))}
    return out


def xbrl_values(text, period_end, want=TAGS):
    """{key: value} for the filing's own period. Duration items use the context ending on the period end
    with the SHORTEST duration (the quarter, not the year-to-date); balance items use the instant at
    period end. Dimensional contexts (segments, standalone/consolidated breakdowns) are ignored."""
    ctx = xbrl_contexts(text)
    if period_end:
        # Older NSE files (and some newer ones) use OneD / FourD / OneI without defining them. NSE's convention:
        # OneD = the reported quarter, FourD = financial year to date, OneI = balance at period end.
        q0 = period_end.month - 2
        qstart = dt.date(period_end.year if q0 > 0 else period_end.year - 1, q0 if q0 > 0 else q0 + 12, 1)
        fystart = dt.date(period_end.year if period_end.month >= 4 else period_end.year - 1, 4, 1)
        ctx.setdefault("OneD", {"start": qstart, "end": period_end, "instant": None, "dims": False})
        ctx.setdefault("FourD", {"start": fystart, "end": period_end, "instant": None, "dims": False})
        ctx.setdefault("OneI", {"start": None, "end": None, "instant": period_end, "dims": False})
    facts = {}
    for m in re.finditer(r"<([\w\-]+):([\w]+)\b([^>]*?)\bcontextRef=\"([^\"]+)\"([^>]*)>([^<]*)</\1:\2>", text):
        local, cref, raw = m.group(2), m.group(4), m.group(6)
        v = num(raw)
        c = ctx.get(cref)
        if v is None or c is None or c["dims"]:
            continue
        facts.setdefault(local, []).append((c, v))
    out, found = {}, sorted(facts)
    for key, names in want.items():
        val = None
        for n in names:
            cands = facts.get(n) or []
            dur = [(c, v) for c, v in cands if c["end"] == period_end and c["start"]]
            ins = [(c, v) for c, v in cands if c["instant"] == period_end]
            if dur:
                c, v = min(dur, key=lambda cv: (cv[0]["end"] - cv[0]["start"]).days)
                val = {"v": v, "days": (c["end"] - c["start"]).days + 1}
            elif ins:
                val = {"v": ins[0][1], "days": 0}
            elif cands and period_end is None:
                val = {"v": cands[0][1], "days": None}
            if val:
                break
        out[key] = val
    return out, found


# ------------------------------------------------------------------ fetching (NSE website, cached)

class Filings:
    """Everything fetched for one symbol. online=False reads the cache/--offline folder only."""

    def __init__(self, sym, web=None, cache=FUNDCACHE, save=None, log=print):
        self.sym, self.web, self.save, self.log = sym, web or nseweb.NSEWeb(pause=0.6), save, log
        self.dir = os.path.join(cache, sym)
        os.makedirs(self.dir, exist_ok=True)
        self.errors = []

    def _keep(self, name, doc):
        if self.save:
            os.makedirs(self.save, exist_ok=True)
            with open(os.path.join(self.save, "%s-%s.json" % (name, self.sym)), "w") as fh:
                json.dump(doc, fh, indent=1)

    def api(self, name):
        try:
            doc = self.web.get(name, sym=self.sym)
            self._keep(name, doc)
            return nseweb.rows(doc)
        except (nseweb.NSEWebError, KeyError) as e:
            self.errors.append("%s: %s" % (name, e))
            return []

    def xbrl(self, url, key):
        path = os.path.join(self.dir, re.sub(r"[^\w\-.]", "_", key) + ".xml")
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        if self.web.offline:
            off = os.path.join(self.web.offline, "xbrl-%s-%s.xml" % (self.sym, key))
            if os.path.exists(off):
                with open(off, encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            return None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": nseweb.UA, "Referer": nseweb.HOME + "/"})
            text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except Exception as e:
            self.errors.append("XBRL %s: %s" % (key, e))
            return None
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        if self.save:
            with open(os.path.join(self.save, "xbrl-%s-%s.xml" % (self.sym, key)), "w", encoding="utf-8") as fh:
                fh.write(text)
        return text


def filing_list(rows):
    """Normalise the results list. Field names UNVERIFIED — several candidates each."""
    out = []
    for r in rows:
        end = parse_date(nseweb.field(r, "toDate", "periodEnded", "period_ended", "qe_Date", "qeDate", "to_date", "toDt",
                                      "quarterEndingDate"))
        url = nseweb.field(r, "xbrl", "xbrlFile", "xbrlUrl", "xbrl_file", "xbrlLink", "attachmentXbrl", default="") or ""
        cons = str(nseweb.field(r, "consolidated", "consolidatedFlag", "isConsolidated", "natureOfReport",
                                "type_of_submission", default="")).lower()
        basis = "standalone" if ("non" in cons or "stand" in cons) else \
            "consolidated" if cons.startswith("cons") or cons in ("y", "yes", "true") else "standalone"
        out.append({"end": end, "basis": basis, "url": url if str(url).startswith("http") else "",
                    "filed": str(nseweb.field(r, "filingDate", "broadCastDate", "broadcastDate", "exchdisstime", default="")),
                    "audited": str(nseweb.field(r, "audited", "auditedUnaudited", default="")),
                    "bank": bool(re.search(r"bank", json.dumps(r).lower()) and re.search(r"banking|bank format", json.dumps(r).lower()))})
    return [f for f in out if f["end"]]


def pick_basis(fl, recent=24):
    """One basis for the whole history (mixing consolidated and standalone would corrupt every trailing
    sum): consolidated when it is filed for at least 80% of the recent periods, else standalone. A period
    missing in that basis stays missing."""
    ends = sorted({f["end"] for f in fl if f["url"]}, reverse=True)[:recent]
    have = lambda b: {f["end"] for f in fl if f["basis"] == b and f["url"] and f["end"] in ends}
    basis = "consolidated" if ends and len(have("consolidated")) >= 0.8 * len(ends) else "standalone"
    per = {}
    for f in sorted(fl, key=lambda f: f["filed"], reverse=True):     # latest revision of a period wins
        if f["basis"] == basis and f["url"] and f["end"] not in per:
            per[f["end"]] = f
    return basis, [per[k] for k in sorted(per)]


def quarters_from_filings(F, fl, max_q=24):
    """Fetch and read up to max_q quarterly XBRL filings (newest first). Returns rows oldest -> newest."""
    out, tagset = [], set()
    for f in sorted(fl, key=lambda f: f["end"], reverse=True)[:max_q]:
        text = F.xbrl(f["url"], "%s-%s" % (f["end"].isoformat(), f["basis"]))
        if not text:
            continue
        vals, found = xbrl_values(text, f["end"])
        tagset.update(found)
        row = {"end": f["end"], "basis": f["basis"], "label": qlabel(f["end"]), "filed": f["filed"], "url": f["url"]}
        for k, v in vals.items():
            row[k] = v["v"] if v else None
            if k in ("revenue", "pat", "eps") and v and v["days"] and v["days"] > 120:
                row["_ytd_" + k] = True            # quarter figure missing; only year-to-date found
        out.append(row)
    return dense(sorted(out, key=lambda r: r["end"])), sorted(tagset)


def quarter_ends(a, b):
    """All quarter-end dates from a to b inclusive."""
    out, y, m = [], a.year, a.month
    while True:
        d = dt.date(y, m, {3: 31, 6: 30, 9: 30, 12: 31}[m])
        if d > b:
            return out
        if d >= a:
            out.append(d)
        m += 3
        if m > 12:
            m, y = m - 12, y + 1


def dense(rows):
    """Insert an empty row for every quarter NSE has no filing for, so 'four rows back' is always the
    same quarter a year earlier and trailing sums never jump a gap."""
    if not rows:
        return rows
    have = {r["end"]: r for r in rows}
    return [have.get(d) or {"end": d, "label": qlabel(d), "missing": True}
            for d in quarter_ends(rows[0]["end"], rows[-1]["end"])]


# ------------------------------------------------------------------ helpers on price history

def series_close(bars):
    return [(dt.date.fromisoformat(b["t"][:10]), b["c"]) for b in bars]


def price_on(ser, d):
    i = bisect.bisect_right([x[0] for x in ser], d) - 1
    return ser[i][1] if i >= 0 else None


def pct_rank(values, x):
    vs = [v for v in values if v is not None]
    if len(vs) < 8 or x is None:
        return None
    return round(100.0 * sum(1 for v in vs if v <= x) / len(vs))


def cagr(a, b, years):
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    return (b / a) ** (1.0 / years) - 1


def ret(ser, days):
    if len(ser) <= days:
        return None
    return ser[-1][1] / ser[-1 - days][1] - 1


# ------------------------------------------------------------------ metric records

def M(label, value, source=None, asof=None, status="ok", note="", raw=None):
    """One displayed figure. status: ok | na (not available) | unverified | manual."""
    if value is None and status == "ok":
        status = "na"
    return {"label": label, "value": value, "source": source, "asof": asof, "status": status, "note": note, "raw": raw}


def fmt_pct(x, d=1, sign=False):
    if x is None:
        return None
    return (("%+." if sign else "%.") + str(int(d)) + "f%%") % (x * 100)


def flag(name, level, value, note, source=None, asof=None):
    """Red-flag row: level Clear | Watch | Red | MANUAL REVIEW | Not available."""
    return {"check": name, "level": level, "value": value, "note": note, "source": source, "asof": asof}


# ------------------------------------------------------------------ dividends and corporate actions

def corporate_actions_raw(rows):
    out = []
    for r in rows:
        d = parse_date(nseweb.field(r, "exDate", "exdate", "ex_date"))
        txt = str(nseweb.field(r, "subject", "purpose", "corpAction", default=""))
        if d and txt:
            out.append((d, "other", None, txt))
    return out


def corporate_actions(rows):
    """[(exdate, kind, amount_or_factor, text)] from NSE corporate actions. Field names UNVERIFIED."""
    out = []
    for r in rows:
        d = parse_date(nseweb.field(r, "exDate", "exdate", "ex_date"))
        txt = str(nseweb.field(r, "subject", "purpose", "corpAction", default=""))
        if not d or not txt:
            continue
        t = txt.lower()
        m = re.search(r"(?:rs\.?|re\.?|₹)\s*([\d.]+)", t)
        if "dividend" in t and m:
            amts = [float(x) for x in re.findall(r"(?:rs\.?|re\.?|₹)\s*([\d.]+)", t)]
            out.append((d, "dividend", sum(amts), txt))
        b = re.search(r"bonus\s*(\d+)\s*:\s*(\d+)", t)
        if b:
            new, old = float(b.group(1)), float(b.group(2))
            out.append((d, "factor", old / (old + new), txt))
        s = re.search(r"(?:split|sub-division).*?rs\.?\s*([\d.]+).*?rs\.?\s*([\d.]+)", t)
        if s and float(s.group(1)) > 0:
            out.append((d, "factor", float(s.group(2)) / float(s.group(1)), txt))
    return sorted(out)


def adjust_eps(quarters, actions):
    """EPS and share count before a bonus or split ex-date restated to today's share basis
    (1:1 bonus: EPS halved, shares doubled), so they compare with today's price."""
    for q in quarters:
        f = 1.0
        for d, kind, val, _ in actions:
            if kind == "factor" and q["end"] < d:
                f *= val
        q["share_f"] = f
        q["eps_adj"] = q["eps"] * f if q.get("eps") is not None else None


# ------------------------------------------------------------------ the analysis

def ttm(qs, key, i):
    """Sum of the 4 quarters ending at index i (None unless all four are present)."""
    if i < 3:
        return None
    vals = [qs[j].get(key) for j in range(i - 3, i + 1)]
    if any(v is None for v in vals) or any(qs[j].get("_ytd_" + key) for j in range(i - 3, i + 1)):
        return None
    return sum(vals)


def latest(qs, key):
    for q in reversed(qs):
        if q.get(key) is not None:
            return q
    return None


def analyse(sym, name, industry, quarters, actions, shp, pledge, announcements, bars, nifty, sector, sector_name,
            basis="consolidated", errors=None, tags_found=None, today=None, actions_all=()):
    today = today or dt.date.today()
    ser = series_close(bars)
    if not ser:
        raise ValueError("no price history for %s" % sym)
    px, px_date = ser[-1][1], ser[-1][0].isoformat()
    qs = quarters
    adjust_eps(qs, actions)
    is_bank = bool(qs) and sum(1 for q in qs if any(q.get(k) is not None for k in BANK_KEYS)) >= max(1, len(qs) // 2)
    is_fin = is_bank or "financial" in (industry or "").lower()
    cyclical = any(k in (industry or "").lower() for k in CYCLICAL)
    lastq = qs[-1] if qs else None
    asof_q = lastq["label"] + " (to " + lastq["end"].isoformat() + ")" if lastq else None
    out = {"symbol": sym, "name": name, "industry": industry or "Not available", "basis": basis,
           "kind": "bank" if is_bank else "financial" if is_fin else "company", "cyclical": cyclical,
           "price": {"value": px, "asof": px_date, "source": SRC_PRICE}, "quarters_read": sum(1 for q in qs if not q.get("missing")),
           "latest_quarter": asof_q, "errors": errors or [], "tags_found": (tags_found or [])[:400],
           "links": {"filings": FILINGS_PAGE % sym, "annual_reports": ANNUAL_REPORTS_PAGE % sym}}

    # ---------- A. valuation vs own history
    A = []
    pe_hist, pe_now = [], None
    eps_points = []
    for i in range(len(qs)):
        e = ttm([{**q, "eps": q.get("eps_adj")} for q in qs], "eps", i)
        if e is not None:
            eps_points.append((qs[i]["end"], e))
    if eps_points:
        # month-end P/E using the TTM EPS known at that date (results usually filed within ~45 days)
        known = [(d + dt.timedelta(days=45), e) for d, e in eps_points]
        start = today - dt.timedelta(days=5 * 365)
        d = dt.date(start.year, start.month, 1)
        while d <= today:
            k = [e for kd, e in known if kd <= d]
            p = price_on(ser, d)
            if k and p and k[-1] > 0:
                pe_hist.append(p / k[-1])
            d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        e_now = eps_points[-1][1]
        pe_now = px / e_now if e_now > 0 else None
    pe_pct = pct_rank(pe_hist, pe_now)
    A.append(M("P/E (trailing 12 months)", None if pe_now is None else round(pe_now, 1), SRC_XBRL + " + price",
               asof_q, note=("loss-making: P/E not meaningful" if eps_points and eps_points[-1][1] <= 0 else
                             "5-year range %.1f to %.1f; now at the %s percentile of its own history" % (
                                 min(pe_hist), max(pe_hist), pe_pct) if pe_pct is not None else
                             "fewer than 8 months of history: no range" if pe_now else "")))
    # P/B: price x shares / equity, shares = paid-up capital / face value (both from XBRL, INR)
    pb_now, pb_hist = None, []
    eqrows = [q for q in qs if q.get("equity") and q.get("paid_up") and q.get("face_value")]
    for q in eqrows:
        shares = q["paid_up"] / q["face_value"] / q.get("share_f", 1.0)
        p = price_on(ser, q["end"])
        if p and q["equity"] > 0:
            pb_hist.append(p * shares / q["equity"])
    if eqrows:
        q = eqrows[-1]
        pb_now = px * (q["paid_up"] / q["face_value"] / q.get("share_f", 1.0)) / q["equity"]
    pb_pct = pct_rank(pb_hist, pb_now) if len(pb_hist) >= 8 else None
    A.append(M("P/B", None if pb_now is None else round(pb_now, 2), SRC_XBRL + " + price",
               eqrows[-1]["label"] if eqrows else None,
               note=("own-history range %.2f to %.2f (%d balance sheets)%s" % (min(pb_hist), max(pb_hist), len(pb_hist),
                     "; percentile %s" % pb_pct if pb_pct is not None else "; too few balance sheets for a percentile")
                     if pb_hist else "")))
    divs = [(d, a) for d, k, a, _ in actions if k == "dividend"]
    last12 = sum(a for d, a in divs if d > today - dt.timedelta(days=365))
    A.append(M("Dividend yield (last 12 months)", fmt_pct(last12 / px, 2) if divs else None, SRC_CA + " + price",
               divs[-1][0].isoformat() if divs else None,
               note="; ".join("%s ₹%g" % (d.isoformat(), a) for d, a in divs[-6:]) if divs else ""))
    lows3 = [c for d, c in ser if d > today - dt.timedelta(days=3 * 365)]
    yb = [b for b in bars if dt.date.fromisoformat(b["t"][:10]) > today - dt.timedelta(days=365)]
    lo52 = min(b["l"] for b in yb) if yb else None
    hi52 = max(b["h"] for b in yb) if yb else None
    A.append(M("Above 3-year low", fmt_pct(px / min(lows3) - 1) if lows3 else None, SRC_PRICE, px_date,
               note="3-year low close ₹%.2f" % min(lows3) if lows3 else ""))
    A.append(M("Above 52-week low", fmt_pct(px / lo52 - 1) if lo52 else None, SRC_PRICE, px_date,
               note="52-week range ₹%.2f to ₹%.2f; %s below the high" % (lo52, hi52, fmt_pct(1 - px / hi52)) if lo52 else ""))
    nser = series_close(nifty) if nifty else []
    sser = series_close(sector) if sector else []
    rel = {}
    for n in (20, 60):
        r, rn, rs = ret(ser, n), ret(nser, n), ret(sser, n)
        rel[n] = (r, rn, rs)
        A.append(M("Last %d days" % n, fmt_pct(r, 1, True),
                   SRC_PRICE, px_date, note="Nifty 50 %s%s" % (fmt_pct(rn, 1, True) or "n/a",
                   ("; %s %s" % (sector_name, fmt_pct(rs, 1, True))) if sser else "; sector index not mapped")))
    out["valuation"] = A
    out["valuation_stats"] = {"pe": pe_now, "pe_pct": pe_pct, "pb": pb_now, "pb_pct": pb_pct, "pe_hist_n": len(pe_hist)}

    # ---------- B. quality and growth (non-financials) or C. banks / NBFCs
    fy = {}
    for q in qs:                                   # fiscal-year sums from complete sets of 4 quarters
        y = q["end"].year + (1 if q["end"].month >= 4 else 0)
        fy.setdefault(y, []).append(q)
    years = {y: {"revenue": sum(q.get("revenue") or 0 for q in v), "pat": sum(q.get("pat") or 0 for q in v),
                 "ok": len(v) == 4 and all(q.get("revenue") is not None and q.get("pat") is not None for q in v)}
             for y, v in fy.items()}
    full = sorted(y for y, v in years.items() if v["ok"])
    i_last = len(qs) - 1
    ttm_rev, ttm_pat = (ttm(qs, "revenue", i_last), ttm(qs, "pat", i_last)) if qs else (None, None)
    g = {}
    for n in (3, 5):
        base_i = i_last - 4 * n
        if base_i >= 3:
            g[n] = (cagr(ttm(qs, "revenue", base_i), ttm_rev, n), cagr(ttm(qs, "pat", base_i), ttm_pat, n))
        else:
            g[n] = (None, None)
    loss_q = sum(1 for q in qs[-12:] if q.get("pat") is not None and q["pat"] < 0)
    fall_q = sum(1 for k in range(max(4, len(qs) - 12), len(qs))
                 if qs[k].get("pat") is not None and qs[k - 4].get("pat") not in (None, 0) and qs[k]["pat"] < qs[k - 4]["pat"])
    span = "last %d quarters" % min(12, len(qs))
    B = [M("Sales growth, 3 years (CAGR, TTM)", fmt_pct(g[3][0]), SRC_XBRL, asof_q,
           note="" if g[3][0] is not None else "needs 16 quarters of filings"),
         M("Sales growth, 5 years (CAGR, TTM)", fmt_pct(g[5][0]), SRC_XBRL, asof_q,
           note="" if g[5][0] is not None else "needs 24 quarters of filings"),
         M("Net profit growth, 3 years (CAGR, TTM)", fmt_pct(g[3][1]), SRC_XBRL, asof_q),
         M("Net profit growth, 5 years (CAGR, TTM)", fmt_pct(g[5][1]), SRC_XBRL, asof_q),
         M("Loss quarters", "%d of %d" % (loss_q, min(12, len(qs))) if qs else None, SRC_XBRL, asof_q, note=span),
         M("Quarters with profit below a year earlier", "%d of %d" % (fall_q, max(0, min(12, len(qs) - 4))) if len(qs) > 4 else None,
           SRC_XBRL, asof_q, note=span)]
    # balance-sheet based ratios (half-yearly filings)
    bs = [q for q in qs if q.get("equity")]
    roe_trend, roce_trend = [], []
    for q in bs:
        i = qs.index(q)
        p4 = ttm(qs, "pat", i)
        if p4 is not None and q["equity"] > 0:
            roe_trend.append((q["label"], p4 / q["equity"]))
        pbt4, fc4 = ttm(qs, "pbt", i), ttm(qs, "finance_cost", i)
        cap = q["equity"] + (q.get("borrow_nc") or 0) + (q.get("borrow_c") or 0)
        if pbt4 is not None and fc4 is not None and cap > 0:
            roce_trend.append((q["label"], (pbt4 + fc4) / cap))
    de = None
    if bs and bs[-1]["equity"] > 0 and (bs[-1].get("borrow_nc") is not None or bs[-1].get("borrow_c") is not None):
        de = ((bs[-1].get("borrow_nc") or 0) + (bs[-1].get("borrow_c") or 0)) / bs[-1]["equity"]
    pbt4, fc4 = (ttm(qs, "pbt", i_last), ttm(qs, "finance_cost", i_last)) if qs else (None, None)
    icov = (pbt4 + fc4) / fc4 if pbt4 is not None and fc4 else None
    trend = lambda tr: "; ".join("%s %s" % (l, fmt_pct(v)) for l, v in tr[-10::2])
    B += [M("ROE (TTM profit ÷ equity)", fmt_pct(roe_trend[-1][1]) if roe_trend else None, SRC_XBRL,
            bs[-1]["label"] if bs else None, note=trend(roe_trend)),
          M("ROCE (TTM EBIT ÷ equity + borrowings)", fmt_pct(roce_trend[-1][1]) if roce_trend else None, SRC_XBRL,
            bs[-1]["label"] if bs else None, note=trend(roce_trend)),
          M("Debt / equity", None if de is None else round(de, 2), SRC_XBRL, bs[-1]["label"] if bs else None),
          M("Interest cover (TTM)", None if icov is None else round(icov, 1), SRC_XBRL, asof_q,
            note="(PBT + finance cost) ÷ finance cost")]
    out["growth"] = {"cagr": g, "loss_q": loss_q, "fall_q": fall_q, "roe": roe_trend[-1][1] if roe_trend else None,
                     "roe_first": roe_trend[0][1] if roe_trend else None, "de": de, "icov": icov}
    if is_fin:
        C = []
        trend_of = lambda k: [(q["label"], q.get(k)) for q in qs if q.get(k) is not None]
        for k, lab, fmt in (("roa", "RoA", "%.2f%%"), ("gnpa_pct", "Gross NPA", "%.2f%%"), ("nnpa_pct", "Net NPA", "%.2f%%"),
                            ("crar", "Capital adequacy (CRAR)", "%.2f%%")):
            tr = trend_of(k)
            C.append(M(lab, fmt % tr[-1][1] if tr else None, SRC_XBRL, tr[-1][0] if tr else None,
                       note="; ".join("%s %s" % (l, fmt % v) for l, v in tr[-8::2])))
        gq = latest(qs, "gnpa_amt")
        pcr = None
        if gq and gq.get("nnpa_amt") is not None and gq["gnpa_amt"]:
            pcr = 1 - gq["nnpa_amt"] / gq["gnpa_amt"]
        C.append(M("Provision coverage (approx.)", fmt_pct(pcr), SRC_XBRL, gq["label"] if gq else None,
                   status="ok" if pcr is not None else "na",
                   note="1 − net NPA ÷ gross NPA; excludes technical write-offs, so below the bank's reported PCR"))
        C.insert(0, M("Net profit growth, 3 years (CAGR, TTM)", fmt_pct(g[3][1]), SRC_XBRL, asof_q,
                      note="5 years: %s" % (fmt_pct(g[5][1]) or "n/a")))
        C.append(M("P/B vs own history", None if pb_now is None else round(pb_now, 2), SRC_XBRL + " + price",
                   eqrows[-1]["label"] if eqrows else None, note=A[1]["note"]))
        for lab in ("CASA ratio", "SMA-1 / SMA-2 book and restructured-book trend", "Write-offs vs recoveries",
                    "RBI NPA divergence disclosure"):
            C.append(M(lab, None, "Investor presentation / annual report notes", None, status="manual",
                       note="not in NSE's structured filings: read it in the filing"))
        out["bank"] = C
        out["bank_stats"] = {"gnpa": trend_of("gnpa_pct"), "nnpa": trend_of("nnpa_pct"), "crar": trend_of("crar"),
                             "roa": trend_of("roa")}
    else:
        out["quality"] = B

    # ---------- D. red flags
    D = []
    ann = [(parse_date(nseweb.field(a, "an_dt", "date", "broadcastDate", "sort_date")),
            str(nseweb.field(a, "desc", "subject", "attchmntText", default="")) + " " +
            str(nseweb.field(a, "attchmntText", "details", default="")), nseweb.field(a, "attchmntFile", "url", default=""))
           for a in announcements]
    ann = [a for a in ann if a[0]]
    # 1 cash conversion: annual OCF / annual PAT (Q4 filings carry the full-year cash flow)
    cc = []
    for q in qs:
        if q.get("ocf") is not None and q["end"].month == 3:
            p = years.get(q["end"].year, {}).get("pat")
            if p and years[q["end"].year]["ok"] and p > 0:
                cc.append((q["label"].replace("Q4 ", ""), q["ocf"] / p))
    if is_fin:
        D.append(flag("Cash conversion (OCF ÷ net profit)", "Not available", None,
                      "not meaningful for lenders: their operating cash flow is driven by loan growth"))
    elif len(cc) >= 2:
        avg = sum(v for _, v in cc[-5:]) / len(cc[-5:])
        lvl = "Red" if avg < 0.8 and sum(1 for _, v in cc[-5:] if v < 0.8) >= max(2, len(cc[-5:]) - 1) else \
              "Watch" if avg < 0.8 or cc[-1][1] < 0.6 else "Clear"
        D.append(flag("Cash conversion (OCF ÷ net profit)", lvl, "%.2fx average of %d years" % (avg, len(cc[-5:])),
                      "; ".join("%s %.2fx" % c for c in cc[-5:]), SRC_XBRL, cc[-1][0]))
    else:
        D.append(flag("Cash conversion (OCF ÷ net profit)", "Not available", None,
                      "needs 2+ annual cash-flow statements in the filings read", SRC_XBRL))
    # 2 working capital: debtor and inventory days vs sales growth (same quarter a year apart)
    if not is_fin:
        wc = []
        for i in range(4, len(qs)):
            a, b = qs[i - 4], qs[i]
            r4a, r4b = ttm(qs, "revenue", i - 4), ttm(qs, "revenue", i)
            if r4a and r4b and b.get("receivables") is not None and a.get("receivables") is not None:
                wc.append({"label": b["label"], "deb_a": a["receivables"] / r4a * 365, "deb_b": b["receivables"] / r4b * 365,
                           "inv_a": (a.get("inventories") or 0) / r4a * 365, "inv_b": (b.get("inventories") or 0) / r4b * 365,
                           "sales_g": r4b / r4a - 1, "unb_a": a.get("unbilled"), "unb_b": b.get("unbilled")})
        if wc:
            w = wc[-1]
            rising = (w["deb_b"] - w["deb_a"] > 10) + (w["inv_b"] - w["inv_a"] > 10)
            unb = (w["unb_a"] and w["unb_b"] and w["unb_b"] / w["unb_a"] - 1 > w["sales_g"] + 0.15)
            persistent = sum(1 for x in wc[-3:] if x["deb_b"] - x["deb_a"] > 10) >= 2
            lvl = "Red" if (rising and persistent) or (rising and unb) else "Watch" if rising or unb else "Clear"
            D.append(flag("Debtor / inventory days vs sales", lvl,
                          "debtor days %.0f → %.0f, inventory days %.0f → %.0f, sales %s" % (
                              w["deb_a"], w["deb_b"], w["inv_a"], w["inv_b"], fmt_pct(w["sales_g"], 1, True)),
                          ("unbilled revenue grew faster than sales; " if unb else "") + "a year apart, on TTM sales",
                          SRC_XBRL, w["label"]))
        else:
            D.append(flag("Debtor / inventory days vs sales", "Not available", None,
                          "needs balance sheets a year apart", SRC_XBRL))
    # 3 other income and exceptional items
    oi = [(q["label"], (q.get("other_income") or 0) / q["pbt"], q.get("exceptional")) for q in qs[-8:]
          if q.get("pbt") and q["pbt"] > 0 and q.get("other_income") is not None]
    if oi:
        share = sum(x[1] for x in oi[-4:]) / len(oi[-4:])
        exc = sum(1 for x in qs[-8:] if x.get("exceptional") not in (None, 0))
        lvl = "Red" if share > 0.4 and exc >= 3 else "Watch" if share > 0.25 or exc >= 3 else "Clear"
        if is_fin:
            lvl = "Watch" if share > 0.6 else "Clear"
        D.append(flag("Other income and exceptional items", lvl,
                      "other income %s of pre-tax profit (last 4 quarters); exceptional items in %d of last %d quarters"
                      % (fmt_pct(share), exc, min(8, len(qs))),
                      "for banks 'other income' includes fees and treasury gains: see the treasury check" if is_fin else
                      "profit leaning on non-operating income", SRC_XBRL, oi[-1][0]))
    else:
        D.append(flag("Other income and exceptional items", "Not available", None, "tags not found", SRC_XBRL))
    # 4 CWIP growing without commissioning
    cw = [(q["label"], q.get("cwip"), q.get("total_assets")) for q in qs if q.get("cwip") is not None]
    if not is_fin and len(cw) >= 3:
        rising = all(cw[k][1] >= cw[k - 1][1] for k in range(len(cw) - 3, len(cw)))
        sh = cw[-1][1] / cw[-1][2] if cw[-1][2] else None
        lvl = "Red" if rising and sh and sh > 0.2 else "Watch" if rising and sh and sh > 0.1 else "Clear"
        D.append(flag("Capital work-in-progress", lvl, "%s of total assets; %s over the last 3 balance sheets" % (
            fmt_pct(sh), "rising" if rising else "not rising"), "large, growing CWIP can hide capitalised costs",
            SRC_XBRL, cw[-1][0]))
    elif not is_fin:
        D.append(flag("Capital work-in-progress", "Not available", None, "needs 3 balance sheets", SRC_XBRL))
    # 5 promoter holding and pledge
    sh_rows = []
    for r in shp:
        d = parse_date(nseweb.field(r, "date", "asOnDate", "quarter", "submissionDate"))
        p = num(nseweb.field(r, "pr_and_prgrp", "promoterAndPromoterGroup", "promoter"))
        if d and p is not None:
            sh_rows.append((d, p))
    sh_rows.sort()
    pl_pct = None
    if pledge:
        pl_pct = num(nseweb.field(pledge[0], "percPromoterShares", "per3", "percPromoterSharesPledged", "pledgedPercent"))
    if sh_rows and all(p == 0 for _, p in sh_rows[-5:]):
        D.append(flag("Promoter holding and pledge", "Clear", "no promoter group",
                      "professionally managed: promoter checks do not apply", SRC_SHP, sh_rows[-1][0].isoformat()))
    elif sh_rows:
        ch4 = sh_rows[-1][1] - sh_rows[-5][1] if len(sh_rows) >= 5 else None
        lvl = "Red" if (pl_pct or 0) > 25 or (ch4 is not None and ch4 < -5) else \
              "Watch" if (pl_pct or 0) > 10 or (ch4 is not None and ch4 < -2) else "Clear"
        D.append(flag("Promoter holding and pledge", lvl, "promoter %.2f%%%s; pledged %s of promoter shares" % (
            sh_rows[-1][1], (", %+.2f pts in 4 quarters" % ch4) if ch4 is not None else "",
            ("%.2f%%" % pl_pct) if pl_pct is not None else "not reported"),
            "; ".join("%s %.2f%%" % (d.isoformat(), p) for d, p in sh_rows[-5:]), SRC_SHP + (" / " + SRC_PLEDGE if pledge else ""),
            sh_rows[-1][0].isoformat()))
    else:
        D.append(flag("Promoter holding and pledge", "Not available", None, "shareholding not returned", SRC_SHP))
    # 6 auditor signals from announcements (last 3 years)
    recent = [a for a in ann if a[0] > today - dt.timedelta(days=3 * 365)]
    resign = [a for a in recent if re.search(r"resign", a[1], re.I) and re.search(r"auditor", a[1], re.I)
              and not re.search(r"internal auditor|secretarial", a[1], re.I)]
    qual = [a for a in recent if re.search(r"audit qualification|qualified opinion|modified opinion(?!.*unmodified)|"
                                           r"emphasis of matter", a[1], re.I) and not re.search(r"unmodified", a[1], re.I)]
    if announcements:
        lvl = "Red" if resign or len(qual) >= 2 else "Watch" if qual else "Clear"
        D.append(flag("Auditor signals", lvl, "%d auditor resignation(s), %d qualification / emphasis notice(s) in 3 years"
                      % (len(resign), len(qual)),
                      "; ".join("%s: %s" % (a[0].isoformat(), a[1][:90].strip()) for a in (resign + qual)[:3]) or
                      "keyword scan of announcements; the audit report itself is a manual read", SRC_ANN,
                      recent[0][0].isoformat() if recent else None))
    else:
        D.append(flag("Auditor signals", "Not available", None, "announcements not returned", SRC_ANN))
    if is_fin:
        tg = [(q["label"], q.get("op_profit"), q.get("other_income")) for q in qs[-8:] if q.get("op_profit") is not None
              and q.get("other_income") is not None]
        if len(tg) >= 5:
            core_now, core_then = tg[-1][1] - tg[-1][2], tg[-5][1] - tg[-5][2]
            lvl = "Watch" if tg[-1][1] > tg[-5][1] and core_now < core_then else "Clear"
            D.append(flag("Treasury / other income masking core profit", lvl,
                          "operating profit %s vs a year ago; excluding other income %s" % (
                              fmt_pct(tg[-1][1] / tg[-5][1] - 1, 1, True) if tg[-5][1] else "n/a",
                              fmt_pct(core_now / core_then - 1, 1, True) if core_then else "n/a"),
                          "other income here includes fees as well as treasury gains: confirm in the notes", SRC_XBRL, tg[-1][0]))
        else:
            D.append(flag("Treasury / other income masking core profit", "MANUAL REVIEW", None,
                          "operating-profit tags not found: compare treasury gains with core operating profit in the results notes"))
    for lab in ("Related-party transactions", "Contingent liabilities vs net worth", "Consolidated vs standalone debt gap",
                "Accounting-policy changes"):
        D.append(flag(lab, "MANUAL REVIEW", None, "needs reading the notes to accounts", "Annual report / results notes", None))
    out["red_flags"] = D
    scored = [f for f in D if f["level"] in ("Clear", "Watch", "Red")]
    n_red, n_watch = sum(f["level"] == "Red" for f in scored), sum(f["level"] == "Watch" for f in scored)
    out["flag_summary"] = {"red": n_red, "watch": n_watch, "clear": sum(f["level"] == "Clear" for f in scored),
                           "scored": len(scored),
                           "pattern": n_red >= 2 or (n_red + n_watch) >= 3,
                           "text": ("%d red and %d watch flags together: worth a closer look at the filings" % (n_red, n_watch))
                           if n_red >= 2 or (n_red + n_watch) >= 3 else
                           "No cluster of flags. One flag alone is not proof of anything."}

    # ---------- E. verdict
    r60, n60, s60 = rel.get(60, (None, None, None))
    fell = (hi52 and px / hi52 - 1 < -0.15) or (r60 is not None and n60 is not None and r60 - n60 < -0.10)
    weak = []
    if is_fin:
        gn = out["bank_stats"]["gnpa"]
        if len(gn) >= 5 and gn[-1][1] > gn[-5][1] + 0.3:
            weak.append("gross NPA up from %.2f%% to %.2f%% in a year" % (gn[-5][1], gn[-1][1]))
        ro = out["bank_stats"]["roa"]
        if len(ro) >= 5 and ro[-1][1] < ro[-5][1] - 0.2:
            weak.append("RoA down from %.2f%% to %.2f%%" % (ro[-5][1], ro[-1][1]))
    p_now, p_then = ttm(qs, "pat", i_last) if qs else None, ttm(qs, "pat", i_last - 4) if len(qs) > 7 else None
    if p_now is not None and p_then and p_then > 0 and p_now / p_then - 1 < -0.05:
        weak.append("trailing-12-month profit %s vs a year earlier" % fmt_pct(p_now / p_then - 1, 1, True))
    if out["flag_summary"]["pattern"]:
        weak.append(out["flag_summary"]["text"].split(":")[0])
    if not qs:
        verdict = ("Not enough filings read", "no quarterly results could be read, so the business side cannot be judged")
    elif fell and weak:
        verdict = ("Price fall matched by weaker fundamentals", "; ".join(weak))
    elif fell:
        why = []
        if hi52:
            why.append("price %s below its 52-week high" % fmt_pct(1 - px / hi52))
        if r60 is not None and n60 is not None:
            why.append("%s vs Nifty over 60 days" % fmt_pct(r60 - n60, 1, True))
        verdict = ("Business intact — fall looks external",
                   ", ".join(why) + ", while profit, %s and the red-flag checks have not weakened" % (
                       "asset quality" if is_fin else "growth"))
    elif weak:
        verdict = ("No large price fall, but fundamentals are weakening", "; ".join(weak))
    else:
        verdict = ("No large price fall to explain", "price within 15% of its 52-week high and in line with the Nifty over 60 days")
    out["verdict"] = {"line": verdict[0], "reason": verdict[1]}
    if lastq and (today - lastq["end"]).days > 200:
        out["stale"] = ("Latest results read: %s. NSE's newer results (filed since 2025 as \"Integrated Filing\") were not "
                        "read, so growth, margins and red flags stop at that quarter. Check recent quarters on nseindia.com."
                        % lastq["label"])
        out["verdict"]["reason"] += " (results only to %s)" % lastq["label"]
    dm = [d for d, k, v, txt in actions_all if re.search(r"demerger|scheme of arrangement", txt, re.I)
          and d > today - dt.timedelta(days=5 * 365)]
    if dm:
        out["demerger_note"] = ("Demerger on %s: price, book value and earnings before that date belonged to a larger "
                                "company, so the P/E and P/B history is not like-for-like." % dm[-1].isoformat())
    if cyclical:
        out["cyclical_note"] = ("Cyclical business (%s): a low trailing P/E near a cycle peak is not cheap — earnings may be at "
                                "their high. Compare with mid-cycle earnings." % industry)

    # ---------- scorecard reading (0-10) and its reliability on this stock
    out["score"] = score(out, qs, ser, pe_hist)
    return out


def _reading(out):
    parts = []
    vs = out["valuation_stats"]
    if vs["pe_pct"] is not None:
        parts.append(("valuation", 10 - vs["pe_pct"] / 10.0))
    elif vs["pb_pct"] is not None:
        parts.append(("valuation", 10 - vs["pb_pct"] / 10.0))
    gr = out["growth"]
    g3 = gr["cagr"][3][1]
    if g3 is not None:
        parts.append(("growth", max(0, min(10, 5 + g3 * 25))))          # +20%/yr profit growth -> 10
    if out["kind"] == "company" and gr["roe"] is not None:
        parts.append(("quality", max(0, min(10, gr["roe"] * 40))))    # 25% ROE -> 10
    if out["kind"] != "company":
        gn = out.get("bank_stats", {}).get("gnpa", [])
        if len(gn) >= 5:
            parts.append(("asset quality", max(0, min(10, 5 - (gn[-1][1] - gn[-5][1]) * 5))))
    fs = out["flag_summary"]
    if fs["scored"]:
        parts.append(("red flags", max(0, 10 - 4 * fs["red"] - 1.5 * fs["watch"])))
    if not parts:
        return None, parts
    return round(sum(v for _, v in parts) / len(parts), 1), parts


def score(out, qs, ser, pe_hist):
    """Reading out of 10 (higher = cheaper vs own history, growing, cleaner). Reliability: point-in-time
    readings at past quarter ends (valuation percentile + profit growth known then) against the stock's
    next-6-month return. Fewer than 8 past quarters with both -> 'Too few cases'."""
    val, parts = _reading(out)
    cases = []
    eps_known = []
    for i in range(len(qs)):
        e = ttm([{**q, "eps": q.get("eps_adj")} for q in qs], "eps", i)
        p4, p4o = ttm(qs, "pat", i), ttm(qs, "pat", i - 4) if i >= 7 else None
        if e is None or e <= 0:
            continue
        d = qs[i]["end"] + dt.timedelta(days=45)
        p0, p6 = price_on(ser, d), price_on(ser, d + dt.timedelta(days=182))
        if not p0 or not p6 or d + dt.timedelta(days=182) > ser[-1][0]:
            continue
        eps_known.append(p0 / e)
        if len(eps_known) < 4 or p4 is None or not p4o or p4o <= 0:
            continue
        pct = 100.0 * sum(1 for x in eps_known if x <= p0 / e) / len(eps_known)
        rd = ((10 - pct / 10) + max(0, min(10, 5 + (p4 / p4o - 1) * 25))) / 2
        cases.append((rd, p6 / p0 - 1))
    if len(cases) < 8:
        rel = {"label": "Too few cases", "detail": "%d past quarters with a point-in-time reading and a 6-month outcome; "
                                                   "8 needed" % len(cases), "n": len(cases)}
    else:
        cases.sort()
        lo, hi = cases[:len(cases) // 2], cases[len(cases) // 2:]
        med = lambda xs: sorted(x[1] for x in xs)[len(xs) // 2]
        edge = med(hi) - med(lo)
        lab = "Has worked here" if edge > 0.03 else "Worked against its meaning" if edge < -0.03 else "No better than ordinary"
        rel = {"label": lab, "detail": "higher readings were followed by a median 6-month return %s vs lower readings "
                                       "(%d quarters)" % (fmt_pct(edge, 1, True), len(cases)), "n": len(cases)}
    return {"reading": val, "parts": [{"part": p, "value": round(v, 1)} for p, v in parts], "reliability": rel}


# ------------------------------------------------------------------ orchestration

def industry_of(sym, csv_path):
    try:
        import csv
        with open(csv_path, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if (r.get("Symbol") or "").strip().upper() == sym:
                    return (r.get("Industry") or "").strip(), (r.get("Company Name") or sym).strip()
    except OSError:
        pass
    return "", sym


def build(sym, get_bars, csv_path, web=None, cache=FUNDCACHE, save=None, max_q=24, log=lambda *a: None):
    """Fetch (or read cached) filings for sym and return the analysis dict. get_bars(id) -> list of bars."""
    F = Filings(sym, web=web, cache=cache, save=save, log=log)
    industry, name = industry_of(sym, csv_path)
    fl = filing_list(F.api("results")) + filing_list(F.api("results_integrated"))
    basis, chosen = pick_basis(fl)
    quarters, tags = quarters_from_filings(F, chosen, max_q=max_q)
    ca_rows = F.api("corp_actions")
    actions, actions_all = corporate_actions(ca_rows), corporate_actions_raw(ca_rows)
    shp = F.api("shareholding")
    pledge = F.api("pledge")
    ann = F.api("announcements")
    bars = get_bars(sym)
    nifty, sector, sector_name = [], [], ""
    try:
        nifty = get_bars("IDX_NIFTY50")
    except Exception as e:
        F.errors.append("Nifty prices: %s" % e)
    tick = "^NSEBANK" if "bank" in name.lower() or sym.endswith("BANK") else SECTOR_INDEX.get(industry.lower())
    if tick:
        try:
            sector, sector_name = get_bars("YAHOO:" + tick), "sector index " + tick
        except Exception as e:
            F.errors.append("sector index %s: %s" % (tick, e))
    if not fl:
        F.errors.append("no results list from NSE (endpoint blocked or changed)")
    r = analyse(sym, name, industry, quarters, actions, shp, pledge, ann, bars, nifty, sector, sector_name,
                basis=basis, errors=F.errors, tags_found=tags, actions_all=actions_all)
    if F.web.offline and os.path.exists(os.path.join(F.web.offline, "SYNTHETIC")):
        r["synthetic"] = True          # test fixtures: never mistaken for real filings
    return r


def to_text(r):
    L = ["=" * 78, "%s  %s  [%s%s]  basis: %s, %d quarters read, latest %s" % (
        r["symbol"], r["name"], r["kind"], ", CYCLICAL" if r["cyclical"] else "", r["basis"], r["quarters_read"],
        r["latest_quarter"]), "VERDICT: %s — %s" % (r["verdict"]["line"], r["verdict"]["reason"])]
    sec = lambda title, rows: L.extend(["", title] + ["  %-44s %-14s %s%s" % (
        m["label"], m["value"] if m["value"] is not None else ("MANUAL REVIEW" if m["status"] == "manual" else "Not available"),
        ("[%s, %s] " % (m["source"], m["asof"])) if m["source"] else "", m["note"]) for m in rows])
    sec("A. VALUATION (vs own history)", r["valuation"])
    sec("C. BANK / NBFC" if r["kind"] != "company" else "B. QUALITY AND GROWTH", r.get("bank") or r.get("quality"))
    L += ["", "D. RED FLAGS"] + ["  %-44s %-14s %s  %s" % (f["check"], f["level"], f["value"] or "", f["note"])
                                 for f in r["red_flags"]]
    L += ["  -> " + r["flag_summary"]["text"]]
    if r.get("cyclical_note"):
        L += ["", "NOTE: " + r["cyclical_note"]]
    sc = r["score"]
    L += ["", "FUNDAMENTALS READING %s / 10 (%s); reliability: %s — %s" % (
        sc["reading"], ", ".join("%s %s" % (p["part"], p["value"]) for p in sc["parts"]), sc["reliability"]["label"],
        sc["reliability"]["detail"])]
    if r.get("stale"):
        L += ["", "STALE: " + r["stale"]]
    if r.get("demerger_note"):
        L += ["NOTE: " + r["demerger_note"]]
    if r["errors"]:
        L += ["", "FETCH PROBLEMS:"] + ["  " + e for e in r["errors"]]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chartist fundamentals from NSE filings (no broker login, no AI).")
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--save", help="also save NSE's raw answers and XBRL files to this folder (to check field names)")
    ap.add_argument("--offline", help="read saved answers from this folder instead of the internet")
    ap.add_argument("--quarters", type=int, default=24)
    ap.add_argument("--tags", action="store_true", help="list the XBRL tag names found (to map new tags)")
    ap.add_argument("--out", help="also write the text report to this file (UTF-8)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # Windows consoles cannot print the rupee sign
    except Exception:
        pass
    sys.path.insert(0, HERE)
    import app
    app.Handler.symbols = app.load_symbols()
    web = nseweb.NSEWeb(offline=a.offline, pause=0.6)
    rc = 0
    for s in [x.upper() for x in a.symbols]:
        try:
            r = build(s, app.bars_for, app.SYMBOLS_CSV, web=web, save=a.save, max_q=a.quarters)
            text = to_text(r)
            if a.tags:
                text += "\nXBRL TAGS FOUND (%d): %s" % (len(r["tags_found"]), ", ".join(r["tags_found"]))
        except Exception as e:
            import traceback
            text = "%s: failed: %s\n%s" % (s, e, traceback.format_exc())
            rc = 1
        print(text)
        if a.out:
            with open(a.out, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
