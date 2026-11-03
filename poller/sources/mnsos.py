"""Minnesota Secretary of State election results media files (official, free, public).

  https://electionresultsfiles.sos.mn.gov/<YYYYMMDD>/<file>.txt
Semicolon-delimited, one row per candidate:
  0 state;1 county;2 precinct;3 office id;4 office name;5 district;6 candidate order;
  7 candidate name;8 suffix;9 incumbent;10 party;11 precincts reporting;
  12 total precincts;13 votes;14 percent;15 total votes in race

The SOS reports counts only; it never calls races.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Optional

from ..schema import Candidate, PrecinctsReporting, Race
from ..util import slugify, to_int

KEY = "mnsos"
NAME = "Minnesota Secretary of State"
GEO = "mn"
FILES = ("ussenate.txt", "ushouse.txt", "Governor.txt")
PARTY = {"DFL": "Democratic-Farmer-Labor", "R": "Republican", "LIB": "Libertarian", "GP": "Green",
         "IND": "Independent", "WI": None, "LMN": "Legal Marijuana Now", "GLC": "Grassroots-Legalize Cannabis",
         "IA": "Independence-Alliance", "SWP": "Socialist Workers", "SCP": "Socialist Workers"}


def file_url(election_date: str, name: str) -> str:
    return f"https://electionresultsfiles.sos.mn.gov/{election_date}/{name}"


def page_url(election_date: str) -> str:
    return "https://electionresults.sos.mn.gov/"


def parse(text: str, *, election_date: str, fetched_at: datetime,
          source_updated_at: Optional[datetime] = None) -> list[Race]:
    contests: "OrderedDict[str, list[list[str]]]" = OrderedDict()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        f = line.split(";")
        if len(f) < 16 or f[0] != "MN":
            raise ValueError(f"unexpected MN results row: {line[:80]!r}")
        contests.setdefault(f"{f[3]}|{f[4]}|{f[5]}", []).append(f)
    if not contests:
        raise ValueError("MN results file has no rows")
    races = []
    for rows in contests.values():
        office = rows[0][4].strip()
        cands = [Candidate(
            name="Write-in" if r[10] == "WI" else r[7].strip(),
            party=PARTY.get(r[10], r[10] or None),
            votes=to_int(r[13]),
            percent=float(r[14]),
        ) for r in rows]
        rep, tot = to_int(rows[0][11]), to_int(rows[0][12])
        federal = office.startswith("U.S.")
        races.append(Race(
            race_id=f"{GEO}:{slugify(office)}",
            race_name=f"Minnesota – {office}",
            level="national" if federal else "state",
            geography=GEO,
            candidates=cands,
            votes=sum(c.votes for c in cands),
            precincts_reporting=PrecinctsReporting(reporting=rep, total=tot),
            source=KEY, source_name=NAME, source_url=page_url(election_date),
            fetched_at=fetched_at, source_updated_at=source_updated_at,
        ))
    return races
