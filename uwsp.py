#!/usr/bin/env python3
"""UWSP Police & Security weekly event/fire logs (public PDFs) -> incidents.csv + logs.csv.

    uv run --with pdfplumber uwsp.py [outdir=.]

Downloads every log PDF linked from the UWSP page (cached in <outdir>/pdf/), parses each line
`U26-00897 EXTRA PATROL 23:03:36 02/15/26 2101 FOURTH AVE; UWSP Chemistry Biology Building`,
and records the source: page URL, PDF URL and the PDF's sha256 (so anyone can check a copy).
Lines that don't parse are printed, never silently dropped.
"""
import csv, hashlib, json, html, re, sys, urllib.request
from pathlib import Path

import pdfplumber

PAGE = "https://www.uwsp.edu/police-and-security-services/event-and-fire-logs/"
UA = {"User-Agent": "Mozilla/5.0"}
LINE = re.compile(r"^(U\d{2}-\d+)\s+(.+?)\s*(\d{2}:\d{2}:\d{2})\s+(\d{2})/(\d{2})/(\d{2})\s*(.*)$")
HEADER = re.compile(r"^Incident\s+Nature")
TOTAL = re.compile(r"^Record List - Total:\s*(\d+)$")


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def links():
    page = get(PAGE).decode()
    seen = {}
    for url, label in re.findall(r'href="([^"]+\.pdf)">([^<]*)', page):
        if "ASR-ASFR" not in url:  # ponytail: the annual security report isn't a log; skip by name
            seen.setdefault(url, html.unescape(label).strip())
    return seen


FOOTER = re.compile(r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M Page \d+ of \d+$")


def parse(pages):
    """pages: each page's text lines -> (incidents, unparsed lines). Three layouts seen:
    one line per incident; the address wrapped onto the next line; and a wide table printed across pages,
    the address column on the second half of the pages, row for row."""
    out, bad, inc_pages, addr_pages, total = [], [], [], [], None
    for lines in pages:
        # a row glued onto the one before it in the PDF ('… STANLEY ST U25-04483 FRAUD COMPLAINT06:15:05 …')
        lines = [part.strip() for ln in lines for part in re.split(r"\s(?=U\d{2}-\d{4,}\s)", ln)]
        for ln in lines:
            if m := TOTAL.match(ln):
                total = int(m[1])
        lines = [ln for ln in lines if ln and not (FOOTER.match(ln) or HEADER.match(ln) or TOTAL.match(ln) or ln == "Incident address")]
        if not lines:
            continue
        (inc_pages if any(LINE.match(ln) for ln in lines) else addr_pages).append(lines)
    if addr_pages and [len(p) for p in addr_pages] != [len(p) for p in inc_pages]:
        bad += [f"address pages don't line up: {[len(p) for p in inc_pages]} vs {[len(p) for p in addr_pages]}"]
        addr_pages = []
    for i, lines in enumerate(inc_pages):
        for j, ln in enumerate(lines):
            m = LINE.match(ln)
            if m:
                inc, nature, t, mm, dd, yy, addr = m.groups()
                if addr_pages:
                    addr = addr_pages[i][j]
                out.append({"incident": inc, "nature": nature, "reported": f"20{yy}-{mm}-{dd}T{t}", "location": addr})
            elif out and not addr_pages:  # a wrapped address
                out[-1]["location"] = f"{out[-1]['location']} {ln}".strip()
            else:
                bad.append(ln)
    if total is not None and total != len(out):
        bad.append(f"the log says {total} records, parsed {len(out)}")
    for r in out:
        loc = r.pop("location").split(";", 1)
        r["address"], r["place"] = loc[0].strip(), loc[1].strip() if len(loc) > 1 else ""
    return out, bad


# The log's codes in plain words, and a broad category. The code itself stays in `nature`.
# ponytail: ARM, BLUE, REDPHONE, ADMIT, TRIP are read from context (where and how often they occur), not from a
# UWSP glossary; ask UWSP for theirs and replace these.
CAT = {"routine": "Routine patrol and building", "traffic": "Traffic", "property": "Property", "medical": "Medical and welfare",
       "fire": "Fire and hazard", "person": "Crimes against people", "conduct": "Conduct and disorder", "service": "Assists and service"}
KINDS = {
    "EXTRA PATROL": ("Extra patrol", "routine"), "ARM": ("Building alarm armed or disarmed", "routine"),
    "ADMIT": ("Let someone into a building", "routine"), "MAINT": ("Maintenance issue", "routine"),
    "ELEV": ("Elevator problem", "routine"), "INFO": ("Information", "service"), "ESCORT": ("Safety escort", "service"),
    "SPECIAL EVENT": ("Special event", "routine"), "SPECIAL DUTY": ("Special duty", "routine"), "PARK": ("Parking", "traffic"),
    "SAFE POINT APP": ("Safety app alert (SafePoint)", "service"), "BLUE": ("Blue-light emergency phone", "service"),
    "REDPHONE": ("Red emergency phone", "service"), "CAMERA": ("Camera check", "routine"), "BUILDING": ("Building issue", "routine"),
    "TRFSTP": ("Traffic stop", "traffic"), "TRFST": ("Traffic stop", "traffic"), "MOTORIST ASSIST": ("Motorist assist", "traffic"),
    "MOTORIST": ("Motorist assist", "traffic"), "DRIVING": ("Reckless or impaired driving", "traffic"),
    "CRASH PROPERTY": ("Crash, property damage", "traffic"), "CRASH H&R": ("Hit and run crash", "traffic"),
    "CRASH INJURY": ("Crash with injury", "traffic"), "CRASH CAR DEER": ("Crash with a deer", "traffic"),
    "CRASH SLIDE IN": ("Vehicle slid off the road", "traffic"), "ASSIST": ("Assist", "service"),
    "SUSPICIOUS": ("Suspicious activity", "conduct"), "FNDPROP": ("Found property", "property"), "LOST": ("Lost property", "property"),
    "PROPERTY CRIME": ("Property crime (theft, damage)", "property"), "PROPERTY": ("Property", "property"),
    "FRAUD COMPLAINT": ("Fraud complaint", "property"), "VACANT HOME": ("Vacant home check", "property"),
    "WELFARE CHECK": ("Welfare check", "medical"), "AMBULANCE": ("Ambulance call", "medical"), "PI": ("Personal injury", "medical"),
    "MISSING PERSON": ("Missing person", "medical"), "RESCUE TECHNICA": ("Technical rescue", "medical"),
    "FIRE ALARM": ("Fire alarm", "fire"), "FIRE": ("Fire", "fire"), "FIRE GAS": ("Gas leak or smell", "fire"),
    "FIRE BURN": ("Burning", "fire"), "CONTROL BURN": ("Controlled burn", "fire"), "HAZARD": ("Hazard", "fire"),
    "WEATHER": ("Weather", "fire"), "ALARM LAW": ("Intrusion or panic alarm", "conduct"), "THREATS": ("Threats", "person"),
    "SEXUAL ASSAULT": ("Sexual assault", "person"), "SEX CRIME": ("Sex crime", "person"), "DISTURBANCE": ("Disturbance", "conduct"),
    "NOISE": ("Noise complaint", "conduct"), "DRUGS": ("Drugs", "conduct"), "TRESPASS": ("Trespassing", "conduct"),
    "VAGRANCY": ("Vagrancy", "conduct"), "ANIMAL": ("Animal", "service"), "911 ACCIDENTAL": ("Accidental 911 call", "service"),
    "INVESTIGATION": ("Investigation", "conduct"), "WARRANT SERVICE": ("Warrant served", "conduct"), "ARREST": ("Arrest", "conduct"),
    "PAPER SERVICE": ("Legal papers served", "service"), "CIVIL": ("Civil matter", "service"), "TRIP": ("Trip", "service"),
}


def kind(nature):
    k, c = KINDS.get(nature, (nature.capitalize(), "service"))
    return {"kind": k, "category": CAT[c]}


def log_id(file):
    """'02.09.2026-02.15.2026_1.pdf' -> 'log-2026-02-09' (the week it starts)."""
    n = re.sub(r"\D", "", file.split("-")[0])  # digits only: one file is named '05.252026-…'
    return f"log-{n[4:8]}-{n[:2]}-{n[2:4]}"


def records(logs, incidents, geo=None):
    """-> {logs, incidents, omissions}: Fangorn records. An incident a later log lists differently keeps
    the first version and lists the others in `variants`."""
    by_url = {l["url"]: log_id(l["file"]) for l in logs}
    recs = {}
    for r in incidents:
        lid, rid = by_url[r["source_url"]], r["incident"].lower()
        body = {k: r[k] for k in ("nature", "reported", "address", "place")}
        if rid in recs:
            p = recs[rid]["payload"]
            p["logs"].append(lid)
            if body != {k: p[k] for k in body}:
                p.setdefault("variants", []).append({**body, "log": lid})
            continue
        ll = (geo or {}).get(r["address"])
        recs[rid] = {"id": rid, "type": "incident", "payload": {
            "title": f"{kind(r['nature'])['kind']} · {r['place'] or r['address']}", "incident": r["incident"], **body,
            **kind(r["nature"]), **({"geo": ll} if ll else {}), "logs": [lid]}}
    # Every week has a log, so an incident number in no log was never published. Each run of missing numbers
    # is one `omission` record naming the log(s) around it (the same log twice when the gap is inside one).
    omissions, first = [], {}
    for r in incidents:
        first.setdefault(r["incident"], by_url[r["source_url"]])
    for yr in sorted({i[:4] for i in first}):  # 'U26-'
        nums = sorted(int(i[4:]) for i in first if i.startswith(yr))
        for lo, hi in zip(nums, nums[1:]):
            if hi - lo > 1:
                a_, b_ = f"{yr}{lo + 1:05d}", f"{yr}{hi - 1:05d}"
                around = sorted({first[f"{yr}{lo:05d}"], first[f"{yr}{hi:05d}"]})
                omissions.append({"id": f"omitted-{a_.lower()}" + (f"-{hi - 1:05d}" if hi - lo > 2 else ""), "type": "omission",
                                  "payload": {"title": f"{hi - lo - 1} incident number(s) missing from the public log: {a_}" + (f" to {b_}" if hi - lo > 2 else ""),
                                              "from": a_, "to": b_, "count": hi - lo - 1, "logs": around}})
    out_logs = []
    for l in logs:
        out_logs.append({"id": log_id(l["file"]), "type": "log", "file": l["file"], "payload": {
            "title": f"UWSP Police & Security event and fire log, {l['label']}",
            "publisher": "University of Wisconsin-Stevens Point Police and Security Services",
            "page": PAGE, "url": l["url"], "sha256": l["sha256"], "bytes": l["bytes"],
            "incidents": l["incidents"], "image_pages": l["image_pages"]}})
    return {"logs": out_logs, "incidents": list(recs.values()), "omissions": omissions}


def main(outdir="."):
    out = Path(outdir)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    incidents, logs = [], []
    for url, label in links().items():
        f = out / "pdf" / url.rsplit("/", 1)[1]
        if not f.exists():
            f.write_bytes(get(url))
        data = f.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        with pdfplumber.open(f) as pdf:
            rows, bad = parse([(p.extract_text() or "").splitlines() for p in pdf.pages])
            # Pages that are only an image (UWSP flattens a page it redacts) carry no text: their incidents
            # show up as omissions (see records()).
            image_pages = sum(1 for p in pdf.pages if not p.chars and p.images)
        for b in bad:
            print(f"UNPARSED {f.name}: {b}", file=sys.stderr)
        logs.append({"file": f.name, "label": label, "url": url, "sha256": sha, "bytes": len(data), "incidents": len(rows), "unparsed": len(bad), "image_pages": image_pages})
        incidents += [{**r, "source_url": url, "source_sha256": sha} for r in rows]
    for name, rows in (("incidents.csv", incidents), ("logs.csv", logs)):
        with open(out / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    geo_file = Path(__file__).parent / "data" / "geo.json"  # from geocode.py
    geo = json.loads(geo_file.read_text()) if geo_file.exists() else {}
    (out / "fangorn.json").write_text(json.dumps(records(logs, incidents, geo)))
    dupes = len(incidents) - len({r["incident"] for r in incidents})
    print(f"{len(logs)} logs, {len(incidents)} incidents, {dupes} duplicate incident numbers, "
          f"{sum(l['unparsed'] for l in logs)} unparsed lines -> {out}")


if __name__ == "__main__":
    rows, bad = parse([["Incident Nature Reported Incident address",
                        "U26-00895 ANIMAL 21:18:54 02/15/26 CLARK ST & FREMONT ST",
                        "U25-01846 ARM 20:01:10 03/23/25 1925 MARIA DR; UWSP", "Protective Services",
                        "8/17/2026 11:39:59 AM Page 1 of 2", "Record List - Total:2"]])
    assert bad == [] and rows[1] == {"incident": "U25-01846", "nature": "ARM", "reported": "2025-03-23T20:01:10",
                                     "address": "1925 MARIA DR", "place": "UWSP Protective Services"}
    rows, bad = parse([["Incident Nature Reported", "U25-07970 EXTRA PATROL 10:33:41 12/08/25"],
                       ["Incident address", "2050 FOURTH AVE; UWSP HEC"]])
    assert bad == [] and rows[0]["address"] == "2050 FOURTH AVE" and rows[0]["nature"] == "EXTRA PATROL"
    r = records([{"file": "02.09.2026-02.15.2026.pdf", "url": "a", "label": "x", "sha256": "", "bytes": 0, "incidents": 2, "image_pages": 0},
                 {"file": "02.16.2026-02.22.2026.pdf", "url": "b", "label": "y", "sha256": "", "bytes": 0, "incidents": 1, "image_pages": 0}],
                [{"incident": "U26-00001", "nature": "ARM", "reported": "t1", "address": "A", "place": "", "source_url": "a"},
                 {"incident": "U26-00004", "nature": "ARM", "reported": "t2", "address": "A", "place": "", "source_url": "a"},
                 {"incident": "U26-00004", "nature": "ARM", "reported": "t3", "address": "A", "place": "", "source_url": "b"}])
    assert r["logs"][0]["id"] == "log-2026-02-09"
    assert [(o["id"], o["payload"]["count"], o["payload"]["logs"]) for o in r["omissions"]] == [("omitted-u26-00002-00003", 2, ["log-2026-02-09"])]
    inc = r["incidents"][1]["payload"]
    assert inc["logs"] == ["log-2026-02-09", "log-2026-02-16"] and inc["variants"] == [{"nature": "ARM", "reported": "t3", "address": "A", "place": "", "log": "log-2026-02-16"}]
    rows, bad = parse([["U25-04482 ASSIST 05:00:00 07/17/25 ILLINOIS AVE & STANLEY ST U25-04483 FRAUD COMPLAINT06:15:05 07/17/25 1108 FREMONT ST"]])
    assert bad == [] and [(x["incident"], x["nature"], x["address"]) for x in rows] == [
        ("U25-04482", "ASSIST", "ILLINOIS AVE & STANLEY ST"), ("U25-04483", "FRAUD COMPLAINT", "1108 FREMONT ST")]
    assert kind("TRFSTP") == {"kind": "Traffic stop", "category": "Traffic"} and kind("NEW CODE")["kind"] == "New code"
    main(*sys.argv[1:])
