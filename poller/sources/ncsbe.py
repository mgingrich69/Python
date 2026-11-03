"""North Carolina State Board of Elections adapter (official, free, public).

Statewide contest results JSON used by the NCSBE results site:
  https://er.ncsbe.gov/enr/<YYYYMMDD>/data/results_0.txt
Fields per candidate row: cid (contest id), cnm (contest name), vfr (vote for),
bnm (ballot name), pty (party), vct (votes), pct (fraction 0..1),
prt/ptl (precincts reported / total), ogl (office group: FED, NCS, NCH, JUD…).

NCSBE reports counts only; it never calls races.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime
from typing import Optional

from ..schema import Candidate, PrecinctsReporting, Race
from ..util import is_ballot_measure, slugify, to_int

KEY = "ncsbe"
NAME = "North Carolina State Board of Elections"
GEO = "nc"
PARTY = {"DEM": "Democratic", "REP": "Republican", "LIB": "Libertarian", "GRE": "Green",
         "CST": "Constitution", "JFA": "Justice for All", "UNA": "Unaffiliated", "WTP": "We the People"}


def data_url(election_date: str) -> str:
    return f"https://er.ncsbe.gov/enr/{election_date}/data/results_0.txt"


def page_url(election_date: str) -> str:
    return f"https://er.ncsbe.gov/?election_dt={election_date[4:6]}/{election_date[6:]}/{election_date[:4]}"


def parse(text: str, *, election_date: str, fetched_at: datetime,
          source_updated_at: Optional[datetime] = None, office_groups=("FED",)) -> list[Race]:
    rows = json.loads(text)
    if not isinstance(rows, list) or not rows:
        raise ValueError("NCSBE results file is empty or not a list")
    contests: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in rows:
        if r.get("ogl") in office_groups:
            contests.setdefault(r["cnm"], []).append(r)
    if not contests:
        raise ValueError(f"no contests for office groups {office_groups}")
    races: list[Race] = []
    for _cnm, items in contests.items():
        name = re.sub(r"\s*\(VOTE FOR \d+\)\s*$", "", items[0]["cnm"]).strip()
        cands = [Candidate(
            name=it["bnm"].strip(),
            party=PARTY.get(it.get("pty") or "", it.get("pty") or None),
            votes=to_int(it["vct"]),
            percent=round(float(it["pct"]) * 100, 1),
        ) for it in items]
        prt, ptl = to_int(items[0]["prt"]), to_int(items[0]["ptl"])
        races.append(Race(
            race_id=f"{GEO}:{slugify(name)}",
            race_name=_title(name),
            level="national" if items[0]["ogl"] == "FED" else "state",
            geography=GEO,
            candidates=cands,
            votes=sum(c.votes for c in cands),
            vote_for=int(items[0].get("vfr") or 1),
            is_ballot_measure=is_ballot_measure(name, [c.name for c in cands]),
            precincts_reporting=PrecinctsReporting(reporting=prt, total=ptl),
            source=KEY,
            source_name=NAME,
            source_url=page_url(election_date),
            fetched_at=fetched_at,
            source_updated_at=source_updated_at,
        ))
    return races


def _title(name: str) -> str:
    t = name.title()
    for a, b in (("Us ", "US "), (" Of ", " of "), (" 0", " "),):
        t = t.replace(a, b)
    return "North Carolina – " + t
