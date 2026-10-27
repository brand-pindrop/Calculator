#!/usr/bin/env python3
"""Builds public-record.json: the last three months of DOJ and FBI actions (at least the five most recent) about North Korean IT-worker schemes.

Sources (primary, public):
  * DOJ press-release API   https://www.justice.gov/api/v1/press_releases.json
  * FBI IC3 public service announcements   https://www.ic3.gov/PSA

How it works
  1. Searches DOJ release titles for North Korea / DPRK / IT worker / laptop farm terms.
  2. Keeps only releases whose text mentions North Korea (or DPRK) AND IT workers / laptop farms / remote IT work.
  3. Merges duplicates: the same action is often published by several DOJ components (FBI, NSD, U.S. Attorney
     offices) on the same day. One item is kept per (date, agency); the Office of Public Affairs release is preferred.
  4. Adds FBI IC3 PSAs that mention North Korea or IT workers.
  5. Writes the most recent items to public-record.json.

Safety
  * If fewer than 3 items are found (network problem, API change), the existing file is left untouched.
  * Optional hand-written numbers (a headline stat for an item) live in public-record-stats.json, keyed by URL.
    The script never invents a statistic.

Run:  python3 update_public_record.py            (needs only the Python standard library)
"""
import datetime
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "public-record.json")
STATS = os.path.join(HERE, "public-record-stats.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; pindrop-calculator-updater)"}
TITLE_TERMS = ["North Korea", "North Korean", "IT Worker", "Laptop Farm", "DPRK", "Information Technology Worker"]
MAX_ITEMS = 40
WINDOW_DAYS = 92  # keep roughly the last three months


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def text(markup):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup or ""))).strip()


def tidy(s):
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    return s.replace("ITW s", "ITWs")


def shorten(s, n=230):
    s = tidy(text(s))
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def doj_items():
    seen = {}
    for term in TITLE_TERMS:
        for page in (0, 1):
            q = urllib.parse.urlencode({"parameters[title]": term, "sort": "date", "direction": "DESC",
                                        "pagesize": "20", "page": str(page)}, safe="[]")
            try:
                data = json.loads(get("https://www.justice.gov/api/v1/press_releases.json?" + q))
            except Exception as e:
                print("DOJ query failed for %r page %d: %s" % (term, page, e), file=sys.stderr)
                continue
            for r in data.get("results", []):
                seen[r["url"]] = r
    out = []
    for url, r in seen.items():
        body = text(r.get("body", ""))
        low = (r.get("title", "") + " " + body).lower()
        north_korea = "north korea" in low or "dprk" in low
        it_work = any(k in low for k in ["it worker", "information technology worker", "laptop farm",
                                         "remote it", "remote information technology"])
        if not (north_korea and it_work):
            continue
        date = datetime.datetime.fromtimestamp(int(r["date"]), datetime.timezone.utc).strftime("%Y-%m-%d")
        out.append({"date": date, "agency": "DOJ", "title": text(r["title"]), "url": url,
                    "summary": shorten(r.get("teaser") or body), "pref": 0 if "/opa/pr/" in url else 1})
    return out


def fbi_items():
    out = []
    try:
        index = get("https://www.ic3.gov/PSA")
    except Exception as e:
        print("IC3 index failed: %s" % e, file=sys.stderr)
        return out
    for href, label in re.findall(r'<a[^>]+href="(/PSA/\d{4}/PSA\d{6})"[^>]*>(.*?)</a>', index, re.S):
        title = text(label)
        if not re.search(r"korea|dprk|it worker", title, re.I):
            continue
        m = re.match(r"/PSA/\d{4}/PSA(\d{2})(\d{2})(\d{2})$", href)
        if not m:
            continue
        date = "20%s-%s-%s" % m.groups()
        url = "https://www.ic3.gov" + href
        summary = "FBI public service announcement."
        try:
            page = get(url)
            body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
            paras = [text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)]
            paras = [p for p in paras if len(p) > 80]
            if paras:
                first = paras[0]
                if title in first:  # drop the "Alert Number ... | date Title" header that precedes the text
                    first = first.split(title, 1)[1].strip()
                summary = shorten(first)
        except Exception as e:
            print("IC3 PSA fetch failed (%s): %s" % (url, e), file=sys.stderr)
        out.append({"date": date, "agency": "FBI", "title": title, "url": url, "summary": summary, "pref": 0})
    return out


def main():
    items = doj_items() + fbi_items()
    best = {}
    for it in items:  # one item per (date, agency); prefer the Office of Public Affairs release
        key = (it["date"], it["agency"])
        if key not in best or it["pref"] < best[key]["pref"]:
            best[key] = it
    ranked = sorted(best.values(), key=lambda x: (x["date"], x["agency"]), reverse=True)
    cutoff = (datetime.date.today() - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    merged = [x for x in ranked if x["date"] >= cutoff][:MAX_ITEMS]
    if len(merged) < 5:  # quiet quarter: fall back to the five most recent so the list is never thin
        merged = ranked[:5]
    if len(merged) < 3:
        print("Only %d items found; leaving %s untouched." % (len(merged), OUT), file=sys.stderr)
        return 1
    stats = {}
    if os.path.exists(STATS):
        with open(STATS, encoding="utf-8") as f:
            stats = json.load(f)
    for it in merged:
        s = stats.get(it["url"])
        if s:
            it["stat"], it["statLabel"] = s.get("stat"), s.get("label")
        it.pop("pref", None)
    payload = {"updated": datetime.date.today().isoformat(), "items": merged}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("Wrote %d items to %s" % (len(merged), OUT))
    for it in merged:
        print("  %s  %-3s  %s" % (it["date"], it["agency"], it["title"][:90]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
