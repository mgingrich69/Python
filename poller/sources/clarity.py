"""Clarity ENR (Scytl/Civera) adapter — used by the Colorado Secretary of State
and many other states/counties.

Flow (public, no auth):
  1. GET  https://results.enr.clarityelections.com/<ST>/<electionId>/current_ver.txt  -> "<ver>"
  2. GET  https://results.enr.clarityelections.com/<ST>/<electionId>/<ver>/reports/detailxml.zip
     -> detail.xml  (<ElectionResult><Timestamp/>…<Contest><Choice><VoteType><County|Precinct/>)

Clarity reports counts only and never calls races.

NOTE: Clarity's CDN returned HTTP 403 to non-browser clients from the build
environment. We do NOT spoof browser headers. If it blocks the poller too, the
source is reported as `error` with the HTTP status and the UI links out.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional
from xml.etree import ElementTree as ET

from ..schema import Candidate, PrecinctsReporting, Race
from ..util import Http, classify_level, fill_percents, is_ballot_measure, slugify

BASE = "https://results.enr.clarityelections.com"
_TZ = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7}


def web_url(state: str, election_id: str) -> str:
    return f"{BASE}/{state}/{election_id}/web.345435/#/summary"


def fetch_detail_xml(http: Http, state: str, election_id: str) -> tuple[bytes, str]:
    ver = http.get(f"{BASE}/{state}/{election_id}/current_ver.txt").text.strip()
    if not re.fullmatch(r"\d+", ver):
        raise ValueError(f"unexpected current_ver.txt content: {ver[:40]!r}")
    z = http.get(f"{BASE}/{state}/{election_id}/{ver}/reports/detailxml.zip").content
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise ValueError("detailxml.zip contains no XML")
        return zf.read(names[0]), ver


def parse_timestamp(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    m = re.match(r"\s*(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)\s*([A-Z]{3})?", text)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%m/%d/%Y %I:%M:%S %p")
    offset = _TZ.get(m.group(2) or "", None)
    if offset is None:
        return None
    return dt.replace(tzinfo=timezone(timedelta(hours=offset)))


def _int(el, attr) -> Optional[int]:
    v = el.get(attr)
    return int(v) if v not in (None, "") and re.fullmatch(r"-?\d+", v) else None


def parse_detail_xml(xml: bytes, *, source: str, source_name: str, geo: str, source_url: str,
                     fetched_at: datetime, county_subtotal: Optional[str] = None,
                     county_geo: Optional[str] = None,
                     include: Optional[re.Pattern] = None) -> list[Race]:
    root = ET.fromstring(xml)
    if root.tag != "ElectionResult":
        raise ValueError(f"unexpected root element {root.tag}")
    updated = parse_timestamp(root.findtext("Timestamp"))
    races: list[Race] = []
    sub_races: list[Race] = []
    seen: dict[str, int] = {}
    for contest in root.iter("Contest"):
        name = (contest.get("text") or "").strip()
        if not name or (include and not include.search(name)):
            continue
        cands, sub = [], []
        for ch in contest.findall("Choice"):
            label = (ch.get("text") or "").strip()
            party = (ch.get("party") or "").strip() or None
            total = _int(ch, "totalVotes")
            if total is None:
                raise ValueError(f"{name}: choice {label!r} has no totalVotes")
            cands.append({"name": label, "party": party, "votes": total, "percent": None})
            if county_subtotal:
                v = 0
                found = False
                for vt in ch.findall("VoteType"):
                    for el in vt:
                        if (el.get("name") or "").strip().lower() == county_subtotal.lower():
                            v += _int(el, "votes") or 0
                            found = True
                if found:
                    sub.append({"name": label, "party": party, "votes": v, "percent": None})
        if not cands:
            continue
        slug = slugify(name)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        pr = None
        rep, part = _int(contest, "precinctsReported"), _int(contest, "precinctsParticipating")
        if rep is not None and part:
            pr = PrecinctsReporting(reporting=rep, total=part)
        common = dict(
            race_name=name, vote_for=_int(contest, "voteFor"),
            is_ballot_measure=(contest.get("isQuestion") == "true") or is_ballot_measure(name, [c["name"] for c in cands]),
            source=source, source_name=source_name, source_url=source_url,
            fetched_at=fetched_at, source_updated_at=updated,
        )
        computed = fill_percents(cands)
        races.append(Race(race_id=f"{geo}:{slug}", level=classify_level(name), geography=geo,
                          candidates=[Candidate(**c) for c in cands], votes=sum(c["votes"] for c in cands),
                          percent_computed=computed, precincts_reporting=pr, **common))
        if sub and county_geo:
            computed = fill_percents(sub)
            sub_races.append(Race(race_id=f"{county_geo}:{slug}", level=classify_level(name), geography=county_geo,
                                  candidates=[Candidate(**c) for c in sub], votes=sum(c["votes"] for c in sub),
                                  percent_computed=computed, **common))
    if not races:
        raise ValueError("no contests parsed from detail XML")
    return races + sub_races
