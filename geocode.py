#!/usr/bin/env python3
"""Coordinates for every address in data/incidents.csv -> data/geo.json {address: [lat, lon]}.

    python3 geocode.py

Intersections ('CLARK ST & FREMONT ST'): one Overpass query for Stevens Point's named streets, then the OSM node
both streets share. Street addresses ('2101 FOURTH AVE #A'): Nominatim, one request a second (its usage policy).
Reruns only look up what geo.json lacks; misses are printed and stored as null so they aren't retried.
"""
import csv, json, re, time, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "data"
BBOX = (44.46, -89.65, 44.58, -89.48)  # Stevens Point and Plover's edge
UA = {"User-Agent": "fangorn-patrol-prototype/0.1"}
WORDS = {"ST": "STREET", "AVE": "AVENUE", "DR": "DRIVE", "RD": "ROAD", "CT": "COURT", "LN": "LANE", "BLVD": "BOULEVARD",
         "PL": "PLACE", "N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST", "HWY": "HIGHWAY", "CIR": "CIRCLE", "PKWY": "PARKWAY"}


def norm(street):
    """'MICHIGAN AVE N' and 'Michigan Avenue North' -> 'MICHIGAN AVENUE NORTH'."""
    return " ".join(WORDS.get(w, w) for w in re.sub(r"[^A-Z0-9 ]", " ", street.upper()).split())


DIRS = {"NORTH", "SOUTH", "EAST", "WEST"}
SUFFIX = {"STREET", "AVENUE", "DRIVE", "ROAD", "COURT", "LANE", "BOULEVARD", "PLACE", "CIRCLE", "PARKWAY"}


def keys(street):
    """Looser and looser names for one street: as written, without N/S/E/W, without the street type too.
    'DIVISION ST N' -> 'DIVISION STREET NORTH', 'DIVISION STREET', 'DIVISION' (OSM: 'Division Street')."""
    ws = norm(street).split()
    k1 = [w for w in ws if w not in DIRS]
    return list(dict.fromkeys(" ".join(k) for k in (ws, k1, [w for w in k1 if w not in SUFFIX]) if k))


def get(url, data=None):
    req = urllib.request.Request(url, data=data, headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=180))


def intersections(wanted):
    """{'A ST & B AVE': [lat, lon]} for the pairs whose streets share a point; {} when no Overpass server answers."""
    q = f'[out:json][timeout:90];way["highway"]["name"]({",".join(map(str, BBOX))});out tags geom;'
    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                "https://overpass.private.coffee/api/interpreter"):  # public servers are often busy: try the next
        try:
            els = get(url, urllib.parse.urlencode({"data": q}).encode())["elements"]
            break
        except OSError as e:
            print(f"overpass {url}: {e}")
    else:
        print("no Overpass server answered: intersections left for the next run")
        return {}
    points = defaultdict(set)  # normalized street name -> its vertices, rounded (shared nodes have equal coords)
    for e in els:
        pts = {(round(p["lat"], 7), round(p["lon"], 7)) for p in e.get("geometry", [])}
        for key in ("name", "alt_name", "old_name", "ref"):
            if key in e.get("tags", {}):
                for k in keys(e["tags"][key]):
                    points[k] |= pts
    out = {}
    for a in wanted:
        x, y = a.split("&", 1)
        # the strictest names that meet: 'DIVISION ST N' falls back to 'DIVISION STREET' only if needed
        shared = next((p for kx in keys(x) for ky in keys(y) if (p := points.get(kx, set()) & points.get(ky, set()))), set())
        # ponytail: streets that cross more than once (a loop) get one of the shared points
        out[a] = list(min(shared)) if shared else None
    return out


def address(a):
    street = re.sub(r"\s*#.*$", "", a)
    q = urllib.parse.urlencode({"q": f"{street}, Stevens Point, Wisconsin", "format": "json", "limit": 1,
                                "viewbox": f"{BBOX[1]},{BBOX[2]},{BBOX[3]},{BBOX[0]}", "bounded": 1})
    hit = get(f"https://nominatim.openstreetmap.org/search?{q}")
    time.sleep(1.1)
    return [float(hit[0]["lat"]), float(hit[0]["lon"])] if hit else None


if __name__ == "__main__":
    assert norm("MICHIGAN AVE N") == norm("Michigan Avenue North") == "MICHIGAN AVENUE NORTH"
    assert keys("DIVISION ST N") == ["DIVISION STREET NORTH", "DIVISION STREET", "DIVISION"] and keys("DIVISION") == ["DIVISION"]
    f = DATA / "geo.json"
    geo = json.loads(f.read_text()) if f.exists() else {}
    every = {r["address"] for r in csv.DictReader(open(DATA / "incidents.csv"))}
    todo = sorted(every - geo.keys())
    # intersections cost one query for all of them, so the misses are retried every run
    geo |= intersections(sorted(a for a in every if "&" in a and not geo.get(a)))
    for i, a in enumerate(a for a in todo if "&" not in a):
        geo[a] = address(a)
        if i % 25 == 0:
            f.write_text(json.dumps(geo, indent=0))
    f.write_text(json.dumps(geo, indent=0))
    miss = [a for a, v in geo.items() if v is None]
    print(f"{len(geo) - len(miss)}/{len(geo)} addresses located; misses: {miss[:40]}")
