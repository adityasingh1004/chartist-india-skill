#!/usr/bin/env python3
"""Chartist from the command line: the same analysis as the web app, printed as text or JSON.
Useful for scripts and AI agents (the Claude skill in skill/chartist uses it).

    python chartist/cli.py RELIANCE              # daily read, plain text
    python chartist/cli.py "nifty bank" --tf W   # weekly read of an index
    python chartist/cli.py BZ=F --json           # Brent, full JSON (bars omitted unless --bars)
    python chartist/cli.py TCS --company         # company information instead of the chart read

Analysis tool only: not investment advice or a research recommendation (see DISCLAIMER.md).
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import app  # noqa: E402

ADVISORY = ("Analysis tool, not an investment recommendation: fixed rules applied to past prices and "
            "public filings. Not investment advice under SEBI regulations; readings can be wrong.")


def text(r):
    v = r["verdict"]
    lines = ["%s  %s  last %s on %s  (%s)" % (r.get("symbol") or r["id"], r["tf"], r["last"]["c"], r["last"]["t"], r["source"]),
             "Chart reads: %s (confidence %s, score %s)" % (v.get("bias"), v.get("confidence"), v.get("score"))]
    if v.get("narrative"):
        lines.append("")
        lines.append(v["narrative"])
    lv = r.get("levels") or {}
    sup = ", ".join(str(z["price"]) for z in (lv.get("support") or [])[:3])
    res = ", ".join(str(z["price"]) for z in (lv.get("resistance") or [])[:3])
    if sup or res:
        lines.append("")
        lines.append("Support: %s | Resistance: %s" % (sup or "-", res or "-"))
    for p in (r.get("patterns") or [])[:4]:
        lines.append("Pattern: %s (%s)" % (p.get("name"), p.get("status")))
    if r.get("note"):
        lines += ["", "Note: " + r["note"]]
    lines += ["", ADVISORY]
    return "\n".join(str(x) for x in lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chartist analysis from the command line. No orders, no advice.")
    ap.add_argument("query", help="symbol, company name, index name or Yahoo ticker")
    ap.add_argument("--tf", choices=["D", "W"], default="D")
    ap.add_argument("--json", action="store_true", help="print the full analysis as JSON")
    ap.add_argument("--bars", action="store_true", help="with --json, keep the price bars")
    ap.add_argument("--company", action="store_true", help="company information instead of the chart read")
    a = ap.parse_args(argv)
    symbols = app.load_symbols()
    sym = app.resolve(a.query, symbols)
    if not sym:
        print("No match for %r. Try an NSE symbol (RELIANCE), SYMBOL.BO for BSE, or a Yahoo ticker (BZ=F)." % a.query,
              file=sys.stderr)
        return 2
    if a.company:
        print(json.dumps(app.company_for(sym, symbols), indent=1, default=str))
        return 0
    try:
        r = app.analyse(sym, a.tf, symbols)
    except Exception as e:
        print("Analysis failed for %s: %s" % (sym, e), file=sys.stderr)
        return 1
    if a.json:
        if not a.bars:
            r.pop("bars", None)
            r.pop("overlays", None)
        r["advisory"] = ADVISORY
        print(json.dumps(r, indent=1, default=str))
    else:
        print(text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
