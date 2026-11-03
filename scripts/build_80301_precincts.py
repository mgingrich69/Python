#!/usr/bin/env python3
"""Build data/static/precincts_80301.json from official Boulder County GIS data.

Method (documented in the output file too):
  * Precinct polygons: Boulder County GIS POLITICAL/PRECINCTS layer.
  * Address points:    Boulder County GIS PARCELS/ADDRESS_POINTS layer (POSTAL_ZIP field).
  * Each address point is assigned to the precinct polygon that contains it.
  * For each precinct we record how many of its address points have POSTAL_ZIP 80301.
    Any precinct with at least one 80301 address "overlaps" ZIP 80301.

This counts *addresses*, not registered voters. It is an approximation and the
dashboard labels it as such. Run once, offline; commit the output.

Usage: python scripts/build_80301_precincts.py [--zip 80301]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

BASE = "https://maps.bouldercounty.org/arcgis/rest/services"
PRECINCTS = f"{BASE}/POLITICAL/PRECINCTS/MapServer/0"
ADDRESSES = f"{BASE}/PARCELS/ADDRESS_POINTS/MapServer/0"
UA = "boulder-election-dashboard/1.0 (personal, non-commercial; one-off GIS extract)"
PAGE = 1000


def get(client: httpx.Client, url: str, params: dict) -> dict:
    for attempt in range(5):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            return data
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            if attempt == 4:
                raise
            wait = 2 ** attempt
            print(f"  retry in {wait}s after {exc!r}", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_precincts(client: httpx.Client) -> list[dict]:
    data = get(client, f"{PRECINCTS}/query", {
        "where": "1=1", "outFields": "PRECINCT,ABBREVIATI", "outSR": 4326,
        "returnGeometry": "true", "f": "geojson",
    })
    feats = data["features"]
    if len(feats) >= PAGE:
        raise RuntimeError("precinct query hit the page limit; add paging")
    return feats


def fetch_addresses(client: httpx.Client) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    offset = 0
    while True:
        data = get(client, f"{ADDRESSES}/query", {
            "where": "LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL",
            "outFields": "OBJECTID,LATITUDE,LONGITUDE,POSTAL_ZIP",
            "returnGeometry": "false", "orderByFields": "OBJECTID",
            "resultOffset": offset, "resultRecordCount": PAGE, "f": "json",
        })
        feats = data.get("features", [])
        for f in feats:
            a = f["attributes"]
            out.append((a["LONGITUDE"], a["LATITUDE"], (a.get("POSTAL_ZIP") or "").strip()))
        offset += len(feats)
        if offset % 20000 < PAGE:
            print(f"  {offset} address points", file=sys.stderr)
        if not feats or not data.get("exceededTransferLimit"):
            break
        time.sleep(0.2)  # be gentle with the county server
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default="80301")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out_path = Path(args.out or Path(__file__).resolve().parents[1] / "data/static" / f"precincts_{args.zip}.json")

    with httpx.Client(timeout=60, headers={"User-Agent": UA}) as client:
        print("Fetching precincts…", file=sys.stderr)
        precincts = fetch_precincts(client)
        print(f"  {len(precincts)} precincts", file=sys.stderr)
        print("Fetching address points…", file=sys.stderr)
        addresses = fetch_addresses(client)

    geoms = [shape(f["geometry"]) for f in precincts]
    tree = STRtree(geoms)
    total = Counter()
    in_zip = Counter()
    unassigned_zip = 0
    for lon, lat, z in addresses:
        pt = Point(lon, lat)
        hits = [i for i in tree.query(pt) if geoms[i].covers(pt)]
        if not hits:
            if z == args.zip:
                unassigned_zip += 1
            continue
        idx = hits[0]
        total[idx] += 1
        if z == args.zip:
            in_zip[idx] += 1

    rows = []
    for idx, f in enumerate(precincts):
        if in_zip[idx] == 0:
            continue
        p = f["properties"]
        rows.append({
            "precinct": str(p["PRECINCT"]),
            "short_code": str(p["ABBREVIATI"]),
            "addresses_total": total[idx],
            "addresses_in_zip": in_zip[idx],
            "share_in_zip": round(in_zip[idx] / total[idx], 4) if total[idx] else None,
        })
    rows.sort(key=lambda r: -r["addresses_in_zip"])
    zip_total = sum(r["addresses_in_zip"] for r in rows)

    doc = {
        "zip": args.zip,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": ("Boulder County GIS address points with POSTAL_ZIP=%s assigned to Boulder County "
                   "GIS precinct polygons by point-in-polygon. A precinct 'overlaps' the ZIP if it "
                   "contains at least one such address. Counts are addresses, not voters." % args.zip),
        "sources": [
            {"name": "Boulder County GIS – Precincts", "url": PRECINCTS, "license": "CC BY 4.0"},
            {"name": "Boulder County GIS – Address Points", "url": ADDRESSES, "license": "CC BY 4.0"},
        ],
        "addresses_in_zip_total": zip_total + unassigned_zip,
        "addresses_in_zip_unassigned": unassigned_zip,
        "precincts": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"Wrote {out_path}: {len(rows)} precincts overlap {args.zip} "
          f"({zip_total} addresses assigned, {unassigned_zip} unassigned)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
