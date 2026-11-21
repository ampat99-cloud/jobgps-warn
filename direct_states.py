"""Direct readers for state WARN lists that the standard scrapers cannot read (pages moved, columns renamed, no scraper).

Each reader returns rows {company, notice_date, location, jobs}. A reader that gets rows writes raw/<st>.csv (replacing the
standard scraper's file) and is listed in raw/direct.json; ingest.py then reads those states by column name.
Everything read is a public employer filing; nothing about any person is kept.
What every reader saw is written to data/debug/direct.json so a broken state can be fixed.

Usage: python direct_states.py raw
"""
import csv, datetime, io, json, re, sys, traceback
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
YEAR = datetime.date.today().year
YEARS = [YEAR, YEAR - 1]  # 400 days back always falls inside this year and last year
DEBUG = {}


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=60, **kw)
    r.raise_for_status()
    return r


def soup_of(url):
    r = get(url)
    return BeautifulSoup(r.text, "html.parser"), r


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def num(s):
    m = re.search(r"\d[\d,]*", str(s or ""))
    return int(m.group(0).replace(",", "")) if m else None


def table_rows(table):
    """A table as a list of header->text dicts, header taken from the first row that has text."""
    trs = table.find_all("tr")
    if not trs:
        return []
    hdr = [clean(c.get_text(" ")) for c in trs[0].find_all(["th", "td"])]
    out = []
    for tr in trs[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        out.append({hdr[i] if i < len(hdr) else f"col{i}": cells[i] for i in range(len(cells))})
    return out


def pick(d, *patterns):
    for p in patterns:
        for k in d:
            if k and re.search(p, k, re.I):
                return d[k]
    return None


def txt(cell):
    return clean(cell.get_text(" ")) if hasattr(cell, "get_text") else clean(cell)


# ---------------------------------------------------------------- Ohio: the page loads a CSV; its address is in the page.
OH_PAGES = ["https://jfs.ohio.gov/page/5WdZaPUb5CYbFx54YJDO6P", "https://jfs.ohio.gov/page/2dQudQxdOkV4jP1tMprcfG"]


def read_oh():
    rows = []
    for page in OH_PAGES:
        html = get(page).text
        m = re.search(r"csvUrl[\\\"]*:[\\\"]*(https:[^\"\\]+\.csv)", html)
        if not m:
            DEBUG.setdefault("OH_notes", []).append(f"no csvUrl on {page}")
            continue
        text = get(m.group(1)).content.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        start = next((i for i, l in enumerate(lines) if l.lower().startswith("company")), 0)
        for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
            rows.append({"company": re.sub(r"^(UPDATE|REVISED|AMENDED)\s+", "", clean(r.get("Company")), flags=re.I),
                         "notice_date": clean(r.get("Date Received")), "location": clean(r.get("City/County")),
                         "jobs": num(r.get("Potential Number Affected"))})
    return rows


# ---------------------------------------------------------------- Florida: one HTML table per year, 100 rows a page.
def read_fl():
    rows = []
    for y in YEARS:
        for page in range(1, 40):
            s, _ = soup_of(f"https://reactwarn.floridajobs.org/WarnList/Records?year={y}&page={page}")
            t = s.find("table")
            if not t:
                break
            got = 0
            for tr in (t.find("tbody") or t).find_all("tr"):
                td = tr.find_all("td")
                if len(td) < 4:
                    continue
                b = td[0].find(["b", "strong"])
                name = clean(b.get_text(" ")) if b else clean(td[0].get_text("\n").split("\n")[0])
                loc = clean(td[0].get_text(" ").replace(name, "", 1))[:150]
                rows.append({"company": name, "notice_date": clean(td[1].get_text(" ")), "location": loc, "jobs": num(td[3].get_text(" "))})
                got += 1
            nxt = s.find("a", href=re.compile(rf"page={page + 1}\b"))
            if not got or not nxt:
                break
    return rows


# ---------------------------------------------------------------- Maryland: a table on the page for this year and one per past year.
def read_md():
    rows = []
    base = "https://www.labor.maryland.gov/employment/"
    for url in [base + "warn.shtml", base + f"warn{YEAR - 1}.shtml"]:
        try:
            s, _ = soup_of(url)
        except Exception as e:
            DEBUG.setdefault("MD_notes", []).append(f"{url}: {e}")
            continue
        for t in s.find_all("table"):
            for r in table_rows(t):
                c, d = pick(r, r"^company", r"employer"), pick(r, r"notice date", r"date")
                if c is None or d is None:
                    continue
                rows.append({"company": txt(c), "notice_date": txt(d), "location": txt(pick(r, r"location") or ""), "jobs": num(txt(pick(r, r"total", r"employees") or ""))})
    return rows


# ---------------------------------------------------------------- Washington DC: a table per year.
def read_dc():
    rows = []
    for y in YEARS:
        try:
            s, _ = soup_of(f"https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-{y}")
        except Exception as e:
            DEBUG.setdefault("DC_notes", []).append(f"{y}: {e}")
            continue
        for t in s.find_all("table"):
            for r in table_rows(t):
                c, d = pick(r, r"organization", r"company", r"employer"), pick(r, r"notice date")
                if c is None or d is None:
                    continue
                rows.append({"company": txt(c), "notice_date": txt(d), "location": "Washington, DC", "jobs": num(txt(pick(r, r"employees") or ""))})
    return rows


# ---------------------------------------------------------------- Missouri: a table per year (the standard reader works; this is a backup).
def read_mo():
    rows = []
    for y in YEARS:
        s, _ = soup_of(f"https://jobs.mo.gov/warn/{y}")
        for t in s.find_all("table"):
            for r in table_rows(t):
                c, d = pick(r, r"^title", r"company", r"employer"), pick(r, r"received")
                if c is None or d is None:
                    continue
                rows.append({"company": txt(c), "notice_date": txt(d), "location": txt(pick(r, r"location") or ""), "jobs": num(txt(pick(r, r"affected") or ""))})
    return rows


# ---------------------------------------------------------------- Excel and spreadsheet helpers
def rows_from_grid(grid, company_rx=r"company|employer|business|organization", date_rx=r"notice|received|notif|warn date|date"):
    """grid = list of lists. Finds the header row, then maps company / date / location / jobs by column name."""
    for hi, row in enumerate(grid[:30]):
        cells = [clean(c) for c in row]
        ci = next((i for i, c in enumerate(cells) if re.search(company_rx, c, re.I) and not re.search(r"address|contact|phone", c, re.I)), None)
        if ci is None:
            continue
        di = next((i for i, c in enumerate(cells) if re.search(r"notice|received|notif", c, re.I) and re.search(r"date|received", c, re.I)), None)
        if di is None:
            di = next((i for i, c in enumerate(cells) if re.search(date_rx, c, re.I) and not re.search(r"layoff|effective|separation|closing|closure|end|start", c, re.I)), None)
        if di is None:
            continue
        li = next((i for i, c in enumerate(cells) if re.search(r"city|location|county|address", c, re.I)), None)
        ji = next((i for i, c in enumerate(cells) if re.search(r"employees|affected|workers|jobs|number|#", c, re.I) and i not in (ci, di)), None)
        out = []
        for row in grid[hi + 1:]:
            row = list(row) + [None] * 30
            if not clean(row[ci]) or re.search(company_rx, clean(row[ci]), re.I) and len(clean(row[ci])) < 25:
                continue
            dv = row[di]
            dv = dv.date().isoformat() if isinstance(dv, datetime.datetime) else dv.isoformat() if isinstance(dv, datetime.date) else clean(dv)
            out.append({"company": clean(row[ci]), "notice_date": dv, "location": clean(row[li]) if li is not None else "", "jobs": num(row[ji]) if ji is not None else None})
        return out, cells
    return [], []


def read_xlsx(content):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    rows, heads = [], []
    for ws in wb.worksheets:
        got, h = rows_from_grid([list(r) for r in ws.iter_rows(values_only=True)])
        rows += got
        heads.append(h)
    return rows, heads


# ---------------------------------------------------------------- Rhode Island: an Excel file linked from the page.
def read_ri():
    s, r = soup_of("https://dlt.ri.gov/employers/worker-adjustment-and-retraining-notification-warn")
    a = next((a for a in s.find_all("a", href=True) if re.search(r"\.xlsx?$", a["href"], re.I) and re.search(r"warn", a.get_text(" ") + a["href"], re.I)), None)
    if not a:
        raise ValueError("no WARN Excel link on the page")
    rows, heads = read_xlsx(get(urljoin(r.url, a["href"])).content)
    DEBUG["RI_headers"] = heads
    return rows


# ---------------------------------------------------------------- Colorado: Google Sheets linked from the page, read as CSV.
def read_co():
    s, _ = soup_of("https://cdle.colorado.gov/employers/layoff-separations/layoff-warn-list")
    rows, heads = [], []
    links = [a for a in s.find_all("a", href=True) if "docs.google.com/spreadsheets/d/" in a["href"]]
    for a in links[:2]:  # this year's real-time sheet and last year's
        sid = re.search(r"/d/([\w-]+)", a["href"]).group(1)
        gid = re.search(r"gid=(\d+)", a["href"])
        url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
        grid = list(csv.reader(io.StringIO(get(url).content.decode("utf-8", errors="replace"))))
        got, h = rows_from_grid(grid)
        rows += got
        heads.append(h)
    DEBUG["CO_headers"] = heads
    return rows


# ---------------------------------------------------------------- Michigan: the page's own search results (JSON with HTML cards).
def read_mi():
    url = ("https://www.michigan.gov/leo/sxa/search/results/?s={8E97AB1D-D2D4-47F8-8CC4-3F1039C8854F}&itemid={BE81F7C2-36A8-4FDE-853C-B05B6E090055}"
           "&sig=&autoFireSearch=true&v={1FFFCC21-5151-4A2B-ABFC-F7FE4E5C9783}&p=2000&o=Created%20Date%20sort%2CDescending")
    data = get(url).json()
    res = data.get("Results") or []
    if res:
        DEBUG["MI_sample"] = {k: (str(v)[:1500]) for k, v in res[0].items()}
    rows = []
    date_rx = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, \d{4})\b")
    for e in res:
        h = BeautifulSoup(e.get("Html") or "", "html.parser")
        content = h.select_one(".search-results__section-content") or h
        lines = [clean(x) for x in content.get_text("\n").split("\n") if clean(x)]
        if not lines:
            continue
        dnode = h.select_one("[class*=date]")
        d = date_rx.search(dnode.get_text(" ")) if dnode else None
        d = d or date_rx.search(h.get_text(" "))
        loc = next((l.split(":", 1)[1] for l in lines if re.match(r"(.*cit(y|ies)|count(y|ies))\s*:", l, re.I)), "")
        jobs = next((num(l.split(":", 1)[1]) for l in lines if re.search(r"jobs", l, re.I) and ":" in l), None)
        rows.append({"company": lines[0], "notice_date": d.group(1) if d else "", "location": clean(loc), "jobs": jobs})
    return rows


# ---------------------------------------------------------------- Georgia: the public view loads its table from WordPress (GravityView).
def read_ga():
    page = "https://www.tcsg.edu/warn-public-view/"
    r = get(page)
    html = r.text
    m = re.search(r"gvDTglobals\.push\((\{.*?\})\);", html, re.S)
    if not m:
        raise ValueError("no table settings on the page")
    g = json.loads(m.group(1))
    DEBUG["GA_globals"] = {k: str(v)[:400] for k, v in g.items()}
    s = BeautifulSoup(html, "html.parser")
    heads = [clean(th.get_text(" ")) for th in s.select("table thead th")]
    cols = g.get("columns") or [{"name": f"col{i}"} for i in range(len(heads))]
    payload = {"draw": 1, "start": 0, "length": -1, "search[value]": "", "search[regex]": "false",
               "action": g.get("ajax", {}).get("data", {}).get("action", "gv_datatables_data") if isinstance(g.get("ajax"), dict) else "gv_datatables_data",
               "view_id": g.get("view_id"), "post_id": g.get("post_id"), "nonce": g.get("nonce"), "getData": "[]", "hideUntilSearched": 0}
    for i, c in enumerate(cols):
        payload[f"columns[{i}][data]"] = i
        payload[f"columns[{i}][name]"] = c.get("name", "") if isinstance(c, dict) else ""
        payload[f"columns[{i}][searchable]"] = "true"
        payload[f"columns[{i}][orderable]"] = "true"
    payload["order[0][column]"] = 2
    payload["order[0][dir]"] = "desc"
    ajax = g.get("ajaxurl") or "https://www.tcsg.edu/wp-admin/admin-ajax.php"
    resp = requests.post(ajax, data=payload, headers={**UA, "Referer": page, "X-Requested-With": "XMLHttpRequest"}, timeout=90)
    DEBUG["GA_response_head"] = resp.text[:600]
    data = resp.json().get("data") or []
    hd = heads[: len(heads) // 2] if len(heads) % 2 == 0 and heads[: len(heads) // 2] == heads[len(heads) // 2:] else heads
    ci = next((i for i, h in enumerate(hd) if re.search(r"company", h, re.I)), 1)
    di = next((i for i, h in enumerate(hd) if re.search(r"submitted|date", h, re.I)), 2)
    ji = next((i for i, h in enumerate(hd) if re.search(r"affected|employees", h, re.I)), 3)
    rows = []
    for item in data:
        vals = [item.get(str(i)) for i in range(len(hd))] if isinstance(item, dict) else list(item)
        vals = [clean(BeautifulSoup(str(v or ""), "html.parser").get_text(" ")) for v in vals]
        if len(vals) <= max(ci, di):
            continue
        rows.append({"company": vals[ci], "notice_date": vals[di], "location": "", "jobs": num(vals[ji]) if ji < len(vals) else None})
    return rows


# ---------------------------------------------------------------- PDF lists (North Carolina, New Mexico, Louisiana, Mississippi, Idaho)
def pdf_rows(content):
    import pdfplumber
    grid = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for p in pdf.pages:
            for t in p.extract_tables() or []:
                grid += [[clean(c) for c in row] for row in t if row]
    rows, head = rows_from_grid(grid)
    if not rows:  # a header repeated on every page is fine; no header at all is not
        DEBUG.setdefault("pdf_first_rows", []).append(grid[:4])
    return rows, head


def pdf_links(page, rx):
    s, r = soup_of(page)
    out = []
    for a in s.find_all("a", href=True):
        if re.search(rx, a.get_text(" ") + " " + a["href"], re.I) and re.search(r"\.pdf", a["href"], re.I):
            out.append(urljoin(r.url, a["href"]))
    return list(dict.fromkeys(out))


def read_pdfs(st, urls):
    rows, heads = [], []
    for u in urls:
        try:
            got, h = pdf_rows(get(u).content)
            rows += got
            heads.append([u.rsplit("/", 1)[-1], h, len(got)])
        except Exception as e:
            heads.append([u.rsplit("/", 1)[-1], f"{type(e).__name__}: {e}"[:200]])
    DEBUG[f"{st}_pdfs"] = heads
    return rows


def read_nc():
    base = "https://www.commerce.nc.gov/data-tools-reports/labor-market-data-tools/workforce-warn-reports/"
    return read_pdfs("NC", [base + f"report-workforce-warn-listings-{y}/open" for y in YEARS])


def read_nm():
    return read_pdfs("NM", pdf_links("https://www.dws.nm.gov/Rapid-Response", rf"({YEAR}|{YEAR - 1})_?WARN"))


def read_la():
    return read_pdfs("LA", pdf_links("https://www.laworks.net/Downloads/Downloads_WFD.asp", rf"WarnNotices({YEAR}|{YEAR - 1})"))


def read_ms():
    links = pdf_links("https://mdes.ms.gov/information-center/warn-information/", r"warn-py")
    return read_pdfs("MS", links[:6])  # newest six quarters cover 400 days


def read_id():
    return read_pdfs("ID", pdf_links("https://www.labor.idaho.gov/businesses/layoff-assistance/", r"WARN-Notices"))


READERS = {"OH": read_oh, "FL": read_fl, "MD": read_md, "DC": read_dc, "RI": read_ri, "CO": read_co, "MI": read_mi, "GA": read_ga,
           "NC": read_nc, "NM": read_nm, "LA": read_la, "MS": read_ms, "ID": read_id, "MO": read_mo}


def main():
    raw = Path(sys.argv[1] if len(sys.argv) > 1 else "raw")
    raw.mkdir(parents=True, exist_ok=True)
    done, report = [], {}
    for st, fn in READERS.items():
        try:
            rows = [r for r in fn() if r.get("company") and r.get("notice_date")]
        except Exception as e:
            report[st] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500], "where": traceback.format_exc().splitlines()[-3:]}
            continue
        report[st] = {"ok": bool(rows), "rows": len(rows), "sample": rows[:3]}
        if rows:
            with open(raw / f"{st.lower()}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["company", "notice_date", "location", "jobs"])
                w.writeheader()
                w.writerows(rows)
            done.append(st.lower())
    (raw / "direct.json").write_text(json.dumps(done))
    Path("data/debug").mkdir(parents=True, exist_ok=True)
    Path("data/debug/direct.json").write_text(json.dumps({"report": report, "notes": DEBUG}, indent=1, default=str))
    for st, v in report.items():
        print(st, "ok" if v["ok"] else "FAILED", v.get("rows", ""), v.get("error", ""))


if __name__ == "__main__":
    main()
