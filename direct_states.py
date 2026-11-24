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
DATE_LIKE = re.compile(r"(?<!\d)(\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4})(?!\d)")


def rows_from_grid(grid, company_rx=r"company|employer|business|organization|notice name|site name|organi[sz]ation name|layoff name", date_rx=r"notice|received|notif|warn date|date"):
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
        ji = next((i for i, c in enumerate(cells) if re.search(r"employees|affected|workers|jobs|layoff number|total notified", c, re.I) and i not in (ci, di)), None)
        if ji is None:
            ji = next((i for i, c in enumerate(cells) if re.search(r"number|#", c, re.I) and not re.search(r"warn|event|phone|naics", c, re.I) and i not in (ci, di)), None)
        out = []
        for row in grid[hi + 1:]:
            row = list(row) + [None] * 30
            # skip empty cells and header rows repeated on later pages (only an exact header, so "Dexter Axle Company" stays)
            if not clean(row[ci]) or clean(row[ci]).lower() == cells[ci].lower() or re.fullmatch(r"(company|employer|business)( name)?", clean(row[ci]), re.I):
                continue
            dv = row[di]
            dv = dv.date().isoformat() if isinstance(dv, datetime.datetime) else dv.isoformat() if isinstance(dv, datetime.date) else clean(dv)
            if not DATE_LIKE.search(dv or ""):
                # 2026-11-23: on page 2 of some PDFs (North Dakota) the cells shift; take the first real date after the company.
                alt = next((clean(c) for c in row[ci + 1:] if isinstance(c, str) and DATE_LIKE.search(clean(c))), None)
                dv = alt or dv
            m1 = DATE_LIKE.search(dv or "")
            if m1 and m1.group(0) != dv:
                dv = m1.group(0)
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
        pm = re.search(r"searchdata/(\d{4})/(\d{1,2})/(\d{1,2})/", (e.get("Path") or e.get("Url") or ""), re.I)
        notice = f"{pm.group(1)}-{int(pm.group(2)):02d}-{int(pm.group(3)):02d}" if pm else ""
        if not notice:
            d = date_rx.search(h.get_text(" "))
            notice = d.group(1) if d else ""
        items = [clean(li.get_text(" ")) for li in h.find_all("li")]
        name = clean((h.find(["h2", "h3", "h4"]) or content).get_text(" ")) if h.find(["h2", "h3", "h4"]) else lines[0]
        loc = next((i.split(":", 1)[1] for i in items if re.match(r"(site address|.*cit(y|ies)|count(y|ies))\s*:", i, re.I)), "")
        jobs = next((num(i.split(":", 1)[1]) for i in items if re.search(r"jobs", i, re.I) and ":" in i), None)
        rows.append({"company": name, "notice_date": notice, "location": clean(loc)[:150], "jobs": jobs})
    return rows


# ---------------------------------------------------------------- Georgia: the public view loads its table from WordPress (GravityView).
def read_ga():
    """Georgia (TCSG GravityView list, 290+ notices, oldest first). 2026-11-23: try the full page table, then GravityView's
    REST service, then the DataTables call; log what the page holds so a miss can be fixed."""
    page = "https://www.tcsg.edu/warn-public-view/"
    sess = requests.Session()
    r = sess.get(page, headers=UA, timeout=60)
    r.raise_for_status()
    html = r.text
    note = {"status": r.status_code, "len": len(html), "ids_in_html": len(set(re.findall(r"GA\d{9}", html))), "tr": html.count("<tr")}
    for kw in ("gvDT", "dataTable", "gv-container", "view_id", "gravityview/v1", "Showing"):
        i = html.find(kw)
        note[kw] = html[max(0, i - 200): i + 600] if i >= 0 else None
    DEBUG["GA_v2"] = note
    s = BeautifulSoup(html, "html.parser")
    rows = []
    for t in s.find_all("table"):
        got, _ = rows_from_grid(grid_of_table(t), company_rx=r"company", date_rx=r"submitted|date")
        rows += got
    note["table_rows"] = len(rows)
    if len(rows) > 30:
        return rows
    vid = re.search(r"gv-container-(\d+)|data-viewid=[\"'](\d+)|[\"']view_id[\"']\s*:\s*[\"']?(\d+)", html)
    vid = next((g for g in (vid.groups() if vid else []) if g), None)
    note["view_id"] = vid
    if vid:
        try:
            resp = requests.get(f"https://www.tcsg.edu/wp-json/gravityview/v1/views/{vid}/entries.json?limit=1000", headers={**UA, "Referer": page}, timeout=90)
            note["rest"] = [resp.status_code, resp.text[:400]]
            data = resp.json()
            ents = data if isinstance(data, list) else (data.get("entries") or data.get("data") or [])
            out = []
            for e in ents:
                vals = [str(v) for k, v in (e.items() if isinstance(e, dict) else enumerate(e)) if v not in (None, "")]
                d = next((v for v in vals if DATE_LIKE.search(v)), None)
                n = next((v for v in vals if re.fullmatch(r"\d{1,6}", v.strip()) and not re.fullmatch(r"GA\d+", v)), None)
                names = [v for v in vals if not DATE_LIKE.search(v) and not re.fullmatch(r"[\d.\-: ]+|GA\d+", v.strip()) and len(v) < 150 and re.search(r"[A-Za-z]{3}", v)]
                if d and names:
                    out.append({"company": clean(names[0]), "notice_date": DATE_LIKE.search(d).group(0), "location": "", "jobs": num(n) if n else None})
            note["rest_rows"] = len(out)
            if len(out) > len(rows):
                return out
        except Exception as e:
            note["rest_error"] = f"{type(e).__name__}: {e}"[:200]
    try:
        old = read_ga_datatables(r, sess)
        note["datatables_rows"] = len(old)
        if len(old) > len(rows):
            return old
    except Exception as e:
        note["datatables_error"] = f"{type(e).__name__}: {e}"[:200]
    return rows


def read_ga_datatables(r, sess):
    """GravityView DataTables, server side: the page holds `const config = {...}`; the rows come from the same call the page makes."""
    page = "https://www.tcsg.edu/warn-public-view/"
    html = r.text
    i = html.find("const config = ")
    if i < 0:
        raise ValueError("no table settings on the page")
    cfg, _ = json.JSONDecoder().raw_decode(html[i + len("const config = "):])
    aj = cfg.get("ajax") or {}
    DEBUG["GA_cfg"] = {"keys": list(cfg.keys())[:40], "ajax": {k: str(v)[:300] for k, v in aj.items()} if isinstance(aj, dict) else str(aj)[:300],
                       "columns": str(cfg.get("columns"))[:400]}
    url = (aj.get("url") if isinstance(aj, dict) else aj) or "https://www.tcsg.edu/wp-admin/admin-ajax.php"
    base = dict(aj.get("data") or {}) if isinstance(aj, dict) else {}
    cols = cfg.get("columns") or [{} for _ in range(4)]
    payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in base.items()}
    payload.update({"draw": 1, "start": 0, "length": 2000, "search[value]": "", "search[regex]": "false", "order[0][column]": 2, "order[0][dir]": "desc"})
    for ci, c in enumerate(cols):
        payload[f"columns[{ci}][data]"] = (c.get("data") if isinstance(c, dict) and c.get("data") is not None else ci)
        payload[f"columns[{ci}][name]"] = c.get("name", "") if isinstance(c, dict) else ""
        payload[f"columns[{ci}][searchable]"] = "true"
        payload[f"columns[{ci}][orderable]"] = "true"
        payload[f"columns[{ci}][search][value]"] = ""
        payload[f"columns[{ci}][search][regex]"] = "false"
    resp = sess.post(url, data=payload, headers={**UA, "Accept": "application/json, text/javascript, */*; q=0.01", "Referer": page,
                                                 "Origin": "https://www.tcsg.edu", "X-Requested-With": "XMLHttpRequest"}, timeout=90)
    DEBUG["GA_response_head"] = [resp.status_code, resp.text[:600]]
    data = resp.json().get("data") or []
    rows = []
    for item in data:
        vals = list(item.values()) if isinstance(item, dict) else list(item)
        vals = [clean(BeautifulSoup(str(v or ""), "html.parser").get_text(" ")) for v in vals]
        d = next((v for v in vals if DATE_LIKE.search(v)), None)
        names = [v for v in vals if v and not DATE_LIKE.search(v) and not re.fullmatch(r"GA\d+|[\d,. ]+", v)]
        n = next((v for v in vals if re.fullmatch(r"\d[\d,]{0,6}", v)), None)
        if d and names:
            rows.append({"company": names[0], "notice_date": DATE_LIKE.search(d).group(0), "location": "", "jobs": num(n) if n else None})
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
    DEBUG.setdefault("pdf_last_rows", []).append(grid[-6:])
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
    links = pdf_links("https://www.laworks.net/Downloads/Downloads_WFD.asp", rf"WarnNotices({YEAR}|{YEAR - 1})")
    return read_pdfs("LA", [re.sub(r"/Downloads/Downloads/", "/Downloads/", u) for u in links])


def read_ms():
    links = pdf_links("https://mdes.ms.gov/information-center/warn-information/", r"warn-py")
    return read_pdfs("MS", links[:6])  # newest six quarters cover 400 days


def read_id():
    return read_pdfs("ID", pdf_links("https://www.labor.idaho.gov/businesses/layoff-assistance/", r"WARN-Notices"))


def grid_of_table(t):
    return [[clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]


def discover(st, starts, follow=r"warn"):
    """Read a WARN list from a state page: its tables, or the WARN files / pages it links to."""
    seen, rows, notes = set(), [], []
    queue = list(starts)
    while queue and len(seen) < 8:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            r = get(u)
        except Exception as e:
            notes.append([u, f"{type(e).__name__}"])
            continue
        ct = r.headers.get("content-type", "").lower()
        try:
            if "pdf" in ct or u.lower().split("?")[0].endswith(".pdf"):
                got, h = pdf_rows(r.content)
            elif "spreadsheet" in ct or re.search(r"\.xlsx?$", u.lower().split("?")[0]):
                got, h = read_xlsx(r.content)
            else:
                s = BeautifulSoup(r.text, "html.parser")
                got, h = [], []
                for t in s.find_all("table"):
                    g, hh = rows_from_grid(grid_of_table(t))
                    got += g
                    h = h or hh
                if not got:
                    for a in s.find_all("a", href=True):
                        href = urljoin(r.url, a["href"])
                        if re.search(follow, a.get_text(" ") + " " + a["href"], re.I) and href not in seen and href.startswith("http") and not href.startswith("mailto"):
                            if re.search(r"\.(pdf|xlsx?)(\?|$)", href, re.I) and not re.search(rf"{YEAR}|{YEAR - 1}|notices|list|log|report", href, re.I):
                                continue
                            queue.append(href)
        except Exception as e:
            got, h = [], f"{type(e).__name__}: {e}"[:150]
        notes.append([u[:150], len(got), h if isinstance(h, str) else h[:8]])
        rows += got
    DEBUG[f"{st}_discover"] = notes
    return rows


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def letters_list(st, pages):
    """States that post one letter (PDF) per notice instead of a list: the company and date come from each link."""
    rows, notes = [], []
    for page in pages:
        try:
            s, r = soup_of(page)
        except Exception as e:
            notes.append([page, f"{type(e).__name__}"])
            continue
        n = 0
        for a in s.find_all("a", href=True):
            href = urljoin(r.url, a["href"])
            if not re.search(r"warn", href, re.I) or not href.lower().endswith(".pdf"):
                continue
            fname = href.rsplit("/", 1)[-1][:-4]
            text = clean(a.get_text(" "))
            d = None
            m = re.match(rf"({MONTHS})-(\d{{1,2}})-(\d{{4}})-(.+?)_WARN", fname, re.I)  # Hawaii: July-31-2026-Company_WARN.pdf
            if m:
                d, name = f"{m.group(1)} {m.group(2)}, {m.group(3)}", m.group(4).replace("-", " ")
            else:
                name = text if len(text) > 3 and not re.search(r"^(warn|download|pdf|click|here)", text, re.I) else re.sub(r"[-_]+", " ", re.split(r"[-_]WARN", fname, flags=re.I)[0])
                dm = re.search(r"(\d{1,2})[-_.](\d{1,2})[-_.](\d{2,4})", fname)
                um = re.search(r"/uploads/(\d{4})/(\d{2})/", href)
                d = f"{dm.group(1)}/{dm.group(2)}/{dm.group(3)}" if dm else (f"{um.group(1)}-{um.group(2)}-01" if um else None)
                name = re.sub(r"\b(WARN|Notice|State|West Virginia|signed|revised)\b", " ", name, flags=re.I)
            name = clean(re.sub(r"[\s_-]*\d{1,2}[-_./]\d{1,4}([-_./]\d{2,4})?\s*$", "", clean(name)))  # "ECM Energy Services 8-19-26" -> "ECM Energy Services"
            if d and len(name) > 2:
                rows.append({"company": name, "notice_date": d, "location": "", "jobs": None})
                n += 1
        notes.append([page, n])
    DEBUG[f"{st}_letters"] = notes
    return rows


def read_wv():
    return letters_list("WV", ["https://workforcewv.org/businesses/layoffs-downsizing/warn-listing/"])


def read_hi():
    return letters_list("HI", [f"https://labor.hawaii.gov/wdc/{y}-warn-notices/" for y in YEARS] + ["https://labor.hawaii.gov/wdd/warn-notices/"])


# ---------------------------------------------------------------- 21 Nov: the last eight states, from the files their pages link to.
MN_FILES = ["plant-closing-mass-layoff-warn-2026-october_tcm1045-773345", "plant-closing-mass-layoff-warn-2026-september_tcm1045-770964",
            "plant-closing-mass-layoff-warn-2026-august_tcm1045-767763", "plant-closing-mass-layoff-warn-2026-july_tcm1045-766204",
            "plant-closing-mass-layoff-warn-2026-june_tcm1045-758364", "plant-closing-mass-layoff-warn-2026-may_tcm1045-752571",
            "plant-closing-mass-layoff-warn-2026-april_tcm1045-749441", "plant-closing-mass-layoff-warn-2026-march_tcm1045-742363",
            "plant-closing-mass-layoff-warn-december-2025_tcm1045-720888", "plant-closing-mass-layoff-warn-october-2025_tcm1045-712065",
            "plant-closing-mass-layoff-warn-september-2025_tcm1045-712064"]


def read_mn():
    urls = [f"https://mn.gov/deed/assets/{f}.pdf" for f in MN_FILES]
    try:  # newer months appear on the archive page
        urls = pdf_links("https://mn.gov/deed/business/layoff-resources/warn-archive/", r"plant-closing-mass-layoff-warn") + urls
    except Exception as e:
        DEBUG.setdefault("MN_notes", []).append(f"archive page: {type(e).__name__}")
    return read_pdfs("MN", list(dict.fromkeys(urls))[:16])


def read_tx():
    """Texas Workforce Commission WARN data on data.texas.gov (the same source the extension reads). Field names are
    lower case since Nov 2026 (job_site_name, notice_date, city_name, total_layoff_number); upper case is still accepted."""
    r = get("https://data.texas.gov/resource/8w53-c4f6.json?$limit=5000&$order=notice_date%20DESC")
    data = r.json()
    DEBUG["TX_sample"] = data[:2] if isinstance(data, list) else str(data)[:300]
    f = lambda d, k: d.get(k) or d.get(k.upper()) or ""
    return [{"company": clean(f(d, "job_site_name")), "notice_date": str(f(d, "notice_date"))[:10], "location": clean(f(d, "city_name")),
             "jobs": num(str(f(d, "total_layoff_number")))} for d in data if f(d, "job_site_name")]


def read_nv():
    return read_pdfs("NV", [f"https://detr.nv.gov/content/media/WARN_and_NonWARN_Master_w_Logo_{y}.pdf" for y in YEARS])


def read_nd():
    return read_pdfs("ND", ["https://jobsnd.com/sites/www/files/documents/jsnd-documents/WARN%20Notices%202015%20to%20present.pdf"])


def read_ma():
    return discover("MA", ["https://www.mass.gov/info-details/worker-adjustment-and-retraining-act-warn-weekly-report",
                           "https://www.mass.gov/info-details/worker-adjustment-and-retraining-notification-act-warn-layoff-and-closure-updates"],
                    follow=r"warn-report|week-ending|xlsx")


READERS = {"OH": read_oh, "FL": read_fl, "MD": read_md, "DC": read_dc, "RI": read_ri, "CO": read_co, "MI": read_mi, "GA": read_ga,
           "NC": read_nc, "NM": read_nm, "LA": read_la, "MS": read_ms, "ID": read_id, "MO": read_mo,
           "WV": read_wv, "HI": read_hi, "MN": read_mn, "NV": read_nv, "ND": read_nd, "MA": read_ma, "TX": read_tx}  # California is read by the extension itself; MA and NV block data-centre servers and are also read in the browser


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
