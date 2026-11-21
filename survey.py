"""One-off look at the state WARN pages that the standard scrapers cannot read, so direct readers can be written.
Writes data/debug/survey.json. Run by .github/workflows/survey.yml (manual only)."""
import csv, io, json, re, subprocess, sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}

PAGES = {
    "FL": ["https://reactwarn.floridajobs.org/WarnList/Records?year=2026&page=1", "https://reactwarn.floridajobs.org/WarnList/"],
    "GA": ["https://www.tcsg.edu/warn-public-view/"],
    "NC": ["https://www.commerce.nc.gov/data-tools-reports/labor-market-data-tools/workforce-warn-reports",
           "https://www.commerce.nc.gov/data-tools-reports/labor-market-data-tools/workforce-warn-reports/report-workforce-warn-listings-2026/open"],
    "MA": ["https://www.mass.gov/info-details/worker-adjustment-and-retraining-act-warn-weekly-report"],
    "MN": ["https://mn.gov/deed/business/layoff-resources/warn-archive/", "https://mn.gov/deed/programs-services/dislocated-worker/dislocated-worker/news/"],
    "NV": ["https://detr.nv.gov/Page/WARN"],
    "MD": ["https://www.labor.maryland.gov/employment/warn.shtml"],
    "CO": ["https://cdle.colorado.gov/employers/layoff-separations/layoff-warn-list"],
    "MI": ["https://www.michigan.gov/leo/bureaus-agencies/wd/data-public-notices/warn-notices"],
    "RI": ["https://dlt.ri.gov/employers/worker-adjustment-and-retraining-notification-warn"],
    "TN": ["https://www.tn.gov/workforce/general-resources/major-publications0/major-publications-redirect/reports.html"],
    "HI": ["https://labor.hawaii.gov/wdc/real-time-warn-updates/"],
    "DC": ["https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-2026", "https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-2025"],
    "ID": ["https://www.labor.idaho.gov/businesss/layoff-assistance/", "https://www.labor.idaho.gov/warn/"],
    "LA": ["https://www.laworks.net/Downloads/Downloads_WFD.asp"],
    "ME": ["https://joblink.maine.gov/search/warn_lookups"],
    "MO": ["https://jobs.mo.gov/warn/2026", "https://jobs.mo.gov/warn/"],
    "NM": ["https://www.dws.state.nm.us/Rapid-Response"],
    "MS": ["https://mdes.ms.gov/information-center/warn-information/"],
    "ND": ["https://www.jobsnd.com/workforce-programs/warn-notices", "https://www.jobsnd.com/unemployment-business/layoff-assistance"],
    "AR": ["https://dws.arkansas.gov/workforce-services/employers/warn-notices/", "https://www.dws.arkansas.gov/workforce-services/employers/warn-notices/"],
    "NH": ["https://www.nhes.nh.gov/services/employers/warn.htm", "https://www.nhes.nh.gov/services/employers/warn-notices"],
    "WV": ["https://workforcewv.org/public-information/warn-notices/", "https://workforcewv.org/warn-notices"],
    "WY": ["https://dws.wyo.gov/dws-division/workforce-center-program-operations/programs/warn-notices/", "https://dws.wyo.gov/warn/"],
    "OH": ["https://jfs.ohio.gov/page/5WdZaPUb5CYbFx54YJDO6P"],
}
INTEREST = re.compile(r"warn|layoff|closing|closure|notice|\.pdf|\.xlsx?|\.csv|docs\.google|spreadsheet|report", re.I)


def look(url):
    out = {"url": url}
    try:
        r = requests.get(url, headers=UA, timeout=40, allow_redirects=True)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:300]
        return out
    out.update(status=r.status_code, final=r.url, type=r.headers.get("content-type", ""), bytes=len(r.content))
    ct = out["type"].lower()
    if "html" not in ct and not r.text.lstrip().startswith("<"):
        out["head"] = r.content[:300].decode("latin-1")
        return out
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        t, h = a.get_text(" ", strip=True)[:90], a["href"]
        if INTEREST.search(t + " " + h):
            links.append([t, h[:300]])
    out["links"] = links[:120]
    out["tables"] = [[len(t.find_all("tr")), [[c.get_text(" ", strip=True)[:60] for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")[:4]]]
                     for t in soup.find_all("table")[:6]]
    out["embedded"] = sorted(set(re.findall(r"https?:[\\/]+[^\"'\s<>]+?\.(?:csv|xlsx?|pdf|json)", r.text)))[:40]
    out["iframes"] = [f.get("src", "")[:300] for f in soup.find_all("iframe")][:10]
    for s in soup(["script", "style", "nav", "header", "footer"]):
        s.decompose()
    out["text"] = re.sub(r"\s+", " ", soup.get_text(" "))[:2500]
    return out


def raw_sample(st):
    """Run the standard scraper for a state that 'worked' but kept nothing recent, and show its columns and newest rows."""
    p = Path("raw") / f"{st}.csv"
    if not p.exists():
        subprocess.run(["timeout", "600", "warn-scraper", st, "--data-dir", "raw", "--log-level", "WARNING"], capture_output=True)
    if not p.exists():
        return {"missing": True}
    rows = list(csv.reader(io.open(p, encoding="utf-8", errors="replace")))
    return {"rows": len(rows), "header": rows[0] if rows else [], "first": rows[1:4], "last": rows[-3:]}


def main():
    res = {st: [look(u) for u in urls] for st, urls in PAGES.items()}
    res["_raw"] = {st: raw_sample(st) for st in ["ga", "me", "mo", "nm", "md"]}
    Path("data/debug").mkdir(parents=True, exist_ok=True)
    Path("data/debug/survey.json").write_text(json.dumps(res, indent=1))
    print("survey written")


if __name__ == "__main__":
    main()
