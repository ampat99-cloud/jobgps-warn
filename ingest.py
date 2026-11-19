"""Turn public state WARN layoff notices into the small JSON files the JobGPS extension reads.

Runs after `warn-scraper all --data-dir raw` (biglocalnews/warn-scraper, Apache-2.0), which saves one CSV per state.
Each state is cleaned with biglocalnews/warn-transformer (same licence) into one shape:
company, location, notice date, jobs. Only the last 400 days are kept. Nothing about any person is stored:
WARN notices are public employer filings.

Usage:  python ingest.py raw data   (writes data/coverage.json and data/warn/<letter>.json; the workflow commits them)
"""
import datetime, json, logging, re, sys
from importlib import import_module
from pathlib import Path

from warn_transformer import utils as wtu

KEEP_DAYS = 400
# Same company-name key as the extension (enrich.js normName / looseKey); keep these three in step.
SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|the)\b")
LOOSE = re.compile(r"\b(technologies|technology|tech|solutions|services|group|holdings|systems|software|labs|usa|us|america|international|global|com)\b")


def norm_name(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def loose_key(key):
    return re.sub(r"\s+", " ", LOOSE.sub(" ", key)).strip()


def as_date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def scrape_error(raw_dir: Path, st: str):
    """Last meaningful lines of that state's scraper log (written by the workflow), so a failure can be fixed."""
    log = raw_dir.parent / "logs" / f"{st}.txt"
    if not log.exists():
        return None
    lines = [l.strip() for l in log.read_text(errors="replace").splitlines() if l.strip()]
    err = [l for l in lines if re.search(r"Error|Exception|Traceback|HTTP|status|refused|timed out|403|404|500", l)]
    return " | ".join((err or lines)[-3:])[:600] or None


def collect(raw_dir: Path, today: datetime.date):
    cutoff = today - datetime.timedelta(days=KEEP_DAYS)
    rows, coverage = [], []
    for st in wtu.get_all_transformers():
        path = raw_dir / f"{st}.csv"
        if not path.exists():
            coverage.append({"state": st.upper(), "rows": 0, "latest_notice": None, "status": "no data from the state site", "error": scrape_error(raw_dir, st)})
            continue
        try:
            items = import_module(f"warn_transformer.transformers.{st}").Transformer(raw_dir).transform()
        except Exception as e:  # one broken state must never stop the others
            logging.warning("%s failed: %s", st, e)
            coverage.append({"state": st.upper(), "rows": 0, "latest_notice": None, "status": "could not be read", "error": f"{type(e).__name__}: {e}"[:600]})
            continue
        kept, latest = 0, None
        for it in items:
            nd = as_date(it.get("notice_date"))
            if nd is None or nd < cutoff or nd > today + datetime.timedelta(days=60):
                continue
            key = norm_name(it.get("company"))
            if len(key) < 2:
                continue
            latest = nd if latest is None or nd > latest else latest
            kept += 1
            rows.append({
                "hash_id": f"{st}:{it['hash_id']}",
                "state": st.upper(),
                "company": (it.get("company") or "")[:200],
                "key": key[:200],
                "key2": loose_key(key)[:200],
                "location": (it.get("location") or None) and str(it["location"])[:200],
                "notice_date": nd.isoformat(),
                "effective_date": (as_date(it.get("effective_date")) or None) and as_date(it.get("effective_date")).isoformat(),
                "jobs": it.get("jobs"),
                "is_closure": it.get("is_closure"),
            })
        coverage.append({"state": st.upper(), "rows": kept, "latest_notice": latest.isoformat() if latest else None,
                         "status": "ok" if kept else "no notices in the last 400 days"})
    # same notice listed twice by a state: keep one
    rows = list({r["hash_id"]: r for r in rows}.values())
    return rows, coverage


def shard_of(key2):
    c = (key2 or "_")[0]
    return c if c.isalpha() else "0"


def write_files(rows, coverage, out: Path, now_iso: str):
    """data/coverage.json + data/warn/<first letter>.json, read by the extension from raw.githubusercontent.com."""
    (out / "warn").mkdir(parents=True, exist_ok=True)
    shards = {}
    for r in sorted(rows, key=lambda r: r["notice_date"], reverse=True):
        shards.setdefault(shard_of(r["key2"]), {}).setdefault(r["key2"], []).append(
            [r["state"], r["company"], r["location"] or "", r["notice_date"], r["jobs"]])
    for f in (out / "warn").glob("*.json"):
        if f.stem not in shards:
            f.unlink()
    for k, v in shards.items():
        (out / "warn" / f"{k}.json").write_text(json.dumps(v, separators=(",", ":")))
    (out / "coverage.json").write_text(json.dumps({"updated_at": now_iso, "keep_days": KEEP_DAYS,
        "states": {c["state"]: {k: v for k, v in c.items() if k != "state" and v is not None} for c in coverage}}, indent=1))


def main():
    logging.basicConfig(level="INFO")
    raw_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "raw")
    out = Path(sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "data")
    today = datetime.date.today()
    rows, coverage = collect(raw_dir, today)
    ok = [c for c in coverage if c["status"] == "ok"]
    print(f"{len(rows)} notices from {len(ok)} states; {len(coverage) - len(ok)} states without usable data")
    for c in coverage:
        print(f"  {c['state']}: {c['status']} ({c['rows']} notices, latest {c['latest_notice']})")
    write_files(rows, coverage, out, datetime.datetime.now(datetime.timezone.utc).isoformat())
    print("written to", out)


if __name__ == "__main__":
    main()
