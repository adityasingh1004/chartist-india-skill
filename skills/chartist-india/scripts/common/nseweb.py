#!/usr/bin/env python3
"""NSE website JSON API (corporate filings) and BSE fallback. Standard library only.

NSE's site serves JSON to its own pages. It is free and primary-source
(the exchange itself), but UNOFFICIAL: no published contract, it needs the
cookies set by the home page, it rate-limits and blocks scripted clients.
Every endpoint path and field name below is UNVERIFIED — CHECK MANUALLY
(open the matching page on nseindia.com, watch the network tab, compare).
Parsers read fields defensively and say when a field is missing instead of
guessing. --offline DIR reads saved responses <name>-<SYM>.json instead.
"""

import http.cookiejar
import json
import os
import time
import urllib.parse
import urllib.request

HOME = "https://www.nseindia.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

ENDPOINTS = {   # UNVERIFIED — CHECK MANUALLY
    "announcements": "/api/corporate-announcements?index=equities&symbol={sym}",
    "results": "/api/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly",
    "shareholding": "/api/corporate-share-holdings-master?index=equities&symbol={sym}",
    "pledge": "/api/corporate-pledgedata?index=equities&symbol={sym}",
    "pit": "/api/corporates-pit?index=equities&symbol={sym}",
    "bulk": "/api/historical/bulk-deals?from={frm}&to={to}",
    "block": "/api/historical/block-deals?from={frm}&to={to}",
}


class NSEWebError(Exception):
    pass


class NSEWeb:
    def __init__(self, offline=None, pause=1.0):
        self.offline = offline
        self.pause = pause
        self._opener = None

    def _open(self):
        if self._opener is None:
            jar = http.cookiejar.CookieJar()
            self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            self._opener.addheaders = [("User-Agent", UA), ("Accept", "application/json, text/plain, */*"),
                                       ("Accept-Language", "en-US,en;q=0.9"), ("Referer", HOME + "/")]
            try:
                self._opener.open(HOME, timeout=20).read()
            except Exception as e:
                raise NSEWebError("nseindia.com home page unreachable (%s)" % e)
        return self._opener

    def get(self, name, **kw):
        sym = kw.get("sym", "")
        if self.offline:
            path = os.path.join(self.offline, "%s-%s.json" % (name, sym) if sym else "%s.json" % name)
            if not os.path.exists(path):
                raise NSEWebError("offline file missing: %s" % path)
            with open(path) as fh:
                return json.load(fh)
        kw = {k: urllib.parse.quote(str(v)) for k, v in kw.items()}
        url = HOME + ENDPOINTS[name].format(**kw)
        op = self._open()
        for attempt in range(2):
            try:
                with op.open(url, timeout=25) as r:
                    data = r.read().decode("utf-8", "replace")
                time.sleep(self.pause)
                return json.loads(data)
            except ValueError:
                raise NSEWebError("NSE returned non-JSON for %s (blocked or page changed)" % name)
            except Exception as e:
                if attempt == 0:
                    self._opener = None
                    op = self._open()
                    time.sleep(2)
                    continue
                raise NSEWebError("NSE %s for %s failed: %s" % (name, sym, e))


def rows(doc):
    """NSE answers are a list, or {'data': [...]}; normalise to a list of dicts."""
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        for k in ("data", "rows", "result"):
            if isinstance(doc.get(k), list):
                return [r for r in doc[k] if isinstance(r, dict)]
    return []


def field(r, *names, default=None):
    for n in names:
        if n in r and r[n] not in (None, "", "-"):
            return r[n]
    return default
