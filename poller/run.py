"""Poll every configured source, normalize, validate, and write results.json.

Graceful degradation: if a source fails (network, HTTP error, parse error,
schema validation), the previous good races for that source are kept, marked
`stale`, and the source status records the error. Nothing from one source ever
overwrites another source's data.

Usage:
  python -m poller.run --out site/data            # one poll
  python -m poller.run --out site/data --loop --interval 300 --duration 20400 \
      --publish-cmd "bash scripts/publish.sh"     # election-night loop
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional

from .schema import Published, Race, SourceBlock, SourceStatus
from .sources import boulder, clarity, mnsos, ncsbe
from .util import Http, utcnow

log = logging.getLogger("poller")
ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "data" / "static"
APPROX_NOTE = "Approximate, based on precincts overlapping ZIP 80301."


class NotYetAvailable(Exception):
    pass


class NotConfigured(Exception):
    pass


# ---------------------------------------------------------------- fetchers
# Each fetcher returns (races, extras, source_updated_at, message).

def fetch_boulder(http: Http, cfg: dict, election_date: str):
    now = utcnow()
    r = http.get(boulder.INDEX_URL)
    try:
        meta, races = boulder.parse_index(r.text, fetched_at=now)
    except boulder.NotYetAvailable as e:
        raise NotYetAvailable(str(e)) from e
    _check_boulder_date(r.text, election_date)
    extras: dict = {"clarity_election_id": meta.get("clarity_election_id"),
                    "ballots_counted": meta.get("ballots_counted"),
                    "results_label": meta.get("subtitle")}
    try:
        t = http.get(boulder.TURNOUT_URL)
        extras["precinct_turnout"] = boulder.parse_turnout(t.text)
        extras["precinct_turnout_url"] = boulder.TURNOUT_URL
    except Exception as e:  # turnout is supplementary; don't fail the source
        extras["precinct_turnout_error"] = f"{type(e).__name__}: {e}"
    return races, extras, meta.get("source_updated_at"), meta.get("subtitle")


def _check_boulder_date(html: str, election_date: str) -> None:
    want = datetime.strptime(election_date, "%Y-%m-%d")
    m = re.search(r'class="EnrHeaderDate">\s*([A-Za-z]+ \d{1,2}, \d{4})', html)
    if not m:
        raise ValueError("Boulder page has no election date header")
    got = datetime.strptime(m.group(1), "%B %d, %Y")
    if got.date() != want.date():
        raise ValueError(f"Boulder page is for {got.date()}, expected {want.date()}")


def _check_clarity_date(xml: bytes, election_date: str) -> None:
    m = re.search(rb"<ElectionDate>\s*(\d{1,2}/\d{1,2}/\d{4})\s*</ElectionDate>", xml)
    if not m:
        raise ValueError("Clarity XML has no ElectionDate")
    got = datetime.strptime(m.group(1).decode(), "%m/%d/%Y").date()
    if got != datetime.strptime(election_date, "%Y-%m-%d").date():
        raise ValueError(f"Clarity election is dated {got}, expected {election_date}")


def make_clarity_fetcher(key: str, cfg: dict, discovered_id: Callable[[], Optional[str]] = lambda: None):
    def fetch(http: Http, _cfg: dict, election_date: str):
        env_id = os.environ.get(f"{key.upper()}_ELECTION_ID")
        eid = env_id or cfg.get("election_id") or discovered_id()
        if eid and eid in set(cfg.get("known_old_election_ids", [])):
            eid = None
        if not eid:
            raise NotConfigured(
                f"Clarity election ID for {cfg['state']} not known yet. Set {key.upper()}_ELECTION_ID "
                f"or sources.{key}.election_id in data/static/config.json.")
        xml, ver = clarity.fetch_detail_xml(http, cfg["state"], eid)
        _check_clarity_date(xml, election_date)
        include = re.compile(cfg["include"]) if cfg.get("include") else None
        races = clarity.parse_detail_xml(
            xml, source=key, source_name=cfg["name"], geo=cfg["state"].lower(),
            source_url=clarity.web_url(cfg["state"], eid), fetched_at=utcnow(),
            county_subtotal=cfg.get("county_subtotal"),
            county_geo=boulder.GEO if cfg.get("county_subtotal") == "Boulder" else None,
            include=include)
        upd = races[0].source_updated_at if races else None
        return races, {"election_id": eid, "version": ver}, upd, None
    return fetch


def fetch_ncsbe(http: Http, cfg: dict, election_date: str):
    d = election_date.replace("-", "")
    r = http.get(ncsbe.data_url(d))
    upd = None
    if r.headers.get("last-modified"):
        try:
            upd = parsedate_to_datetime(r.headers["last-modified"])
        except (TypeError, ValueError):
            upd = None
    races = ncsbe.parse(r.text, election_date=d, fetched_at=utcnow(), source_updated_at=upd,
                        office_groups=tuple(cfg.get("office_groups", ["FED"])))
    # Candidate lineups are published before polls close; show them with zero votes
    # (the UI labels them "Awaiting results") instead of hiding the races.
    msg = "No votes reported yet. NC polls close 7:30 PM ET." if all(rc.votes == 0 for rc in races) else None
    return races, {}, upd, msg


def _last_modified(r) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(r.headers["last-modified"]) if r.headers.get("last-modified") else None
    except (TypeError, ValueError):
        return None


def fetch_mnsos(http: Http, cfg: dict, election_date: str):
    d = election_date.replace("-", "")
    races, upds = [], []
    for name in cfg.get("files", list(mnsos.FILES)):
        r = http.get(mnsos.file_url(d, name))
        if "<html" in r.text[:200].lower():
            raise ValueError(f"{name}: got an HTML page instead of a results file")
        upd = _last_modified(r)
        upds.append(upd)
        races += mnsos.parse(r.text, election_date=d, fetched_at=utcnow(), source_updated_at=upd)
    msg = "No votes reported yet. MN polls close 8 PM CT." if all(rc.votes == 0 for rc in races) else None
    return races, {}, max([u for u in upds if u], default=None), msg


# ---------------------------------------------------------------- core

def poll_source(key: str, name: str, url: str, fetcher, http: Http, cfg: dict, election_date: str,
                prev: Optional[SourceBlock]) -> SourceBlock:
    now = utcnow()
    prev_status = prev.status if prev else None
    base = dict(key=key, name=name, url=url, last_attempt_at=now,
                last_success_at=prev_status.last_success_at if prev_status else None,
                source_updated_at=prev_status.source_updated_at if prev_status else None)
    try:
        races, extras, upd, message = fetcher(http, cfg, election_date)
        # Re-validate every race (adapters already build pydantic models; this guards dict paths).
        races = [Race.model_validate(r.model_dump()) for r in races]
        st = SourceStatus(**{**base, "status": "ok", "last_success_at": now,
                             "source_updated_at": upd, "message": message})
        return SourceBlock(status=st, races=races, extras=extras)
    except NotConfigured as e:
        return SourceBlock(status=SourceStatus(**{**base, "status": "not_configured", "message": str(e)}))
    except NotYetAvailable as e:
        if prev and prev.races:  # results were up before; treat a regression as a failure
            return _stale(prev, base, f"Source now says: {e}")
        return SourceBlock(status=SourceStatus(**{**base, "status": "not_yet_available", "message": str(e),
                                                  "last_success_at": now}))
    except Exception as e:  # noqa: BLE001 - any failure → keep last good, mark stale
        log.error("source %s failed: %s\n%s", key, e, traceback.format_exc())
        return _stale(prev, base, _describe(e))


def _describe(e: Exception) -> str:
    import httpx
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} from {e.request.url}"
    return f"{type(e).__name__}: {str(e)[:300]}"


def _stale(prev: Optional[SourceBlock], base: dict, err: str) -> SourceBlock:
    fails = (prev.status.consecutive_failures if prev else 0) + 1
    if prev and prev.races:
        races = [r.model_copy(update={"stale": True}) for r in prev.races]
        st = SourceStatus(**{**base, "status": "stale", "last_error": err, "consecutive_failures": fails,
                             "message": "Showing last good data; latest fetch failed."})
        return SourceBlock(status=st, races=races, extras=prev.extras)
    st = SourceStatus(**{**base, "status": "error", "last_error": err, "consecutive_failures": fails})
    return SourceBlock(status=st, races=[], extras=prev.extras if prev else {})


def aggregate_80301(boulder_block: Optional[SourceBlock], mapping: dict) -> dict:
    """Approximate 80301 view: turnout across precincts overlapping the ZIP.

    Boulder County publishes contest results countywide only, so vote shares for
    80301 are NOT computed (that would be fabrication). We aggregate precinct
    turnout, which the county does publish per precinct."""
    out = {
        "zip": mapping["zip"],
        "label": APPROX_NOTE,
        "method": mapping["method"],
        "mapping_generated_at": mapping["generated_at"],
        "mapping_sources": mapping["sources"],
        "precinct_results_available": False,
        "precinct_results_note": ("Boulder County posts contest results countywide on election night; "
                                  "precinct-by-precinct contest results appear in the official Statement of "
                                  "Votes after certification. Countywide results are shown below."),
        "precincts": [],
        "turnout": None,
    }
    turnout = (boulder_block.extras or {}).get("precinct_turnout") if boulder_block else None
    for p in mapping["precincts"]:
        row = dict(p)
        row["core"] = (p["share_in_zip"] or 0) >= 0.5
        if turnout and p["short_code"] in turnout:
            row.update(turnout[p["short_code"]])
        out["precincts"].append(row)
    if turnout:
        core = [r for r in out["precincts"] if r["core"] and "ballots" in r]
        allp = [r for r in out["precincts"] if "ballots" in r]
        def tot(rows):
            b = sum(r["ballots"] for r in rows)
            reg = sum(r["registered"] for r in rows)
            return {"precincts": len(rows), "ballots": b, "registered": reg,
                    "turnout_pct": round(100 * b / reg, 1) if reg else None}
        out["turnout"] = {"core": tot(core), "all_overlapping": tot(allp),
                          "source_url": boulder.TURNOUT_URL,
                          "stale": boulder_block.status.status == "stale",
                          "fetched_at": boulder_block.status.last_attempt_at.isoformat() if boulder_block.status.last_attempt_at else None}
    return out


def load_previous(path: Path) -> dict[str, SourceBlock]:
    if not path.exists():
        return {}
    try:
        pub = Published.model_validate_json(path.read_text())
        return dict(pub.sources)
    except Exception as e:  # corrupt/old file: start fresh but say so
        log.warning("could not read previous results (%s); starting fresh", e)
        return {}


def poll_once(out_dir: Path, http: Optional[Http] = None, config: Optional[dict] = None) -> Published:
    config = config or json.loads((STATIC / "config.json").read_text())
    mapping = json.loads((STATIC / "precincts_80301.json").read_text())
    out_file = out_dir / "results.json"
    prev = load_previous(out_file)
    http = http or Http()
    ed = config["election_date"]
    srcs = config["sources"]
    blocks: dict[str, SourceBlock] = {}

    if srcs["boulder"].get("enabled"):
        blocks["boulder"] = poll_source("boulder", srcs["boulder"]["name"], srcs["boulder"]["url"],
                                        fetch_boulder, http, srcs["boulder"], ed, prev.get("boulder"))

    def boulder_clarity_id() -> Optional[str]:
        b = blocks.get("boulder")
        return (b.extras or {}).get("clarity_election_id") if b and b.status.status == "ok" else None

    if srcs["co_sos"].get("enabled"):
        c = srcs["co_sos"]
        blocks["co_sos"] = poll_source("co_sos", c["name"], c["url"],
                                       make_clarity_fetcher("co_sos", c, boulder_clarity_id),
                                       http, c, ed, prev.get("co_sos"))
    if srcs["ncsbe"].get("enabled"):
        blocks["ncsbe"] = poll_source("ncsbe", srcs["ncsbe"]["name"], "https://er.ncsbe.gov/",
                                      fetch_ncsbe, http, srcs["ncsbe"], ed, prev.get("ncsbe"))
    if srcs.get("mnsos", {}).get("enabled"):
        blocks["mnsos"] = poll_source("mnsos", srcs["mnsos"]["name"], "https://electionresults.sos.mn.gov/",
                                      fetch_mnsos, http, srcs["mnsos"], ed, prev.get("mnsos"))
    for c in srcs.get("clarity_states", []):
        if c.get("enabled"):
            blocks[c["key"]] = poll_source(c["key"], c["name"], c["url"], make_clarity_fetcher(c["key"], c),
                                           http, c, ed, prev.get(c["key"]))

    pub = Published(
        generated_at=utcnow(),
        poll_interval_seconds=config["poll_interval_seconds"],
        sources=blocks,
        zip_80301=aggregate_80301(blocks.get("boulder"), mapping),
        links=config.get("links", []),
    )
    write_atomic(out_file, pub.model_dump_json(indent=None))
    return pub


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def summarize(pub: Published) -> str:
    parts = []
    for k, b in pub.sources.items():
        s = b.status
        parts.append(f"{k}={s.status}({len(b.races)} races){' ERR ' + s.last_error if s.last_error else ''}")
    return " | ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/data")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--duration", type=int, default=20400, help="loop for this many seconds then exit")
    ap.add_argument("--publish-cmd", default=None, help="shell command run after each poll (e.g. git push)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    out = Path(args.out)
    http = Http()
    start = time.monotonic()
    while True:
        t0 = time.monotonic()
        pub = poll_once(out, http)
        log.info("poll complete: %s", summarize(pub))
        if args.publish_cmd:
            rc = subprocess.call(args.publish_cmd, shell=True)
            if rc:
                log.error("publish command exited %s", rc)
        if not args.loop or time.monotonic() - start + args.interval > args.duration:
            break
        time.sleep(max(5, args.interval - (time.monotonic() - t0)))
    http.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
