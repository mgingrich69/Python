"""Boulder County Elections results adapter.

Source: https://electionresults.bouldercounty.gov/ (static mirror of the county's
ENR app). HTML only. The "Complete Results - All Contests" page (index.html)
contains one Kendo grid per contest; "Home/PrecinctTurnout.html" holds
per-precinct ballots processed.

The county reports countywide totals only (no live precinct-by-contest data),
and never "calls" races. "Official Results" in the header means certified.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..schema import Candidate, Race
from ..util import classify_level, fill_percents, is_ballot_measure, slugify, to_int

KEY = "boulder"
NAME = "Boulder County Elections"
BASE_URL = "https://electionresults.bouldercounty.gov/"
INDEX_URL = BASE_URL + "index.html"
TURNOUT_URL = BASE_URL + "Home/PrecinctTurnout.html"
GEO = "boulder-county"
DENVER = ZoneInfo("America/Denver")


class NotYetAvailable(Exception):
    """The page is up but results have not been posted yet."""


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ") if node else "").strip()


def parse_meta(tree: HTMLParser) -> dict:
    meta: dict = {
        "title": _text(tree.css_first(".EnrHeaderTitle")),
        "subtitle": _text(tree.css_first(".EnrHeaderSubtitle")) or None,
        "message": _text(tree.css_first(".EnrHeaderMessage")) or None,
        "source_updated_at": None,
        "ballots_counted": None,
        "clarity_election_id": None,
    }
    lu = tree.css_first(".EnrHeaderLastUpdatedDateTime")
    if lu and _text(lu):
        meta["source_updated_at"] = datetime.strptime(_text(lu), "%m/%d/%Y %I:%M %p").replace(tzinfo=DENVER)
    for row in tree.css(".EnrHeaderSummaryTable tr"):
        cells = [_text(td) for td in row.css("td")]
        for i, c in enumerate(cells):
            if c.lower().lstrip("*").startswith("total ballots counted"):
                following = [x for x in cells[i + 1:] if x]
                if following:
                    meta["ballots_counted"] = to_int(following[0])
    for a in tree.css("a[href]"):
        m = re.search(r"clarityelections\.com/CO/(\d+)/", a.attributes.get("href") or "")
        if m:
            meta["clarity_election_id"] = m.group(1)
            break
    return meta


def parse_index(html: str, *, fetched_at: datetime, source_url: str = INDEX_URL) -> tuple[dict, list[Race]]:
    tree = HTMLParser(html)
    meta = parse_meta(tree)
    grids = tree.css('div.k-grid[id^="gridContest"]')
    if not grids:
        if meta["message"] and "will be posted" in meta["message"].lower():
            raise NotYetAvailable(meta["message"])
        raise ValueError("no contest grids found on Boulder results page (layout change?)")

    certified = (meta["subtitle"] or "").strip().lower() == "official results"
    races: list[Race] = []
    seen: dict[str, int] = {}
    for grid in grids:
        title = _text(grid.css_first("th.EnrContestTitleHeader"))
        if not title:
            raise ValueError(f"contest grid {grid.attributes.get('id')} has no title")
        cands: list[dict] = []
        for tr in grid.css("tbody tr"):
            name_td = tr.css_first("td.EnrCandidateName")
            votes_td = tr.css_first("td.EnrCandidateVotes")
            if not name_td or not votes_td:
                continue
            votes = to_int(_text(votes_td))
            # The "percent" cell holds the raw vote count (percent is computed in-browser);
            # use it as an integrity check on the displayed count.
            raw_td = tr.css_first("td.EnrCandidatePercent")
            if raw_td and _text(raw_td) and to_int(_text(raw_td)) != votes:
                raise ValueError(f"{title}: vote cell mismatch for {_text(name_td)}")
            name, party = _split_party(_text(name_td))
            cands.append({"name": name, "party": party, "votes": votes, "percent": None})
        if not cands:
            raise ValueError(f"contest {title!r} has no candidate rows")
        computed = fill_percents(cands)
        slug = slugify(title)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        races.append(Race(
            race_id=f"{GEO}:{slug}",
            race_name=title,
            level=classify_level(title, county_source=True),
            geography=GEO,
            candidates=[Candidate(**c) for c in cands],
            votes=sum(c["votes"] for c in cands),
            percent_computed=computed,
            is_ballot_measure=is_ballot_measure(title, [c["name"] for c in cands]),
            ballots_counted=meta["ballots_counted"],
            called_status="certified" if certified else "not_called",
            called_by=NAME if certified else None,
            source=KEY,
            source_name=NAME,
            source_url=source_url,
            fetched_at=fetched_at,
            source_updated_at=meta["source_updated_at"],
        ))
    return meta, races


_PARTY_SUFFIX = re.compile(
    r"\s+-\s+(Democratic|Republican|Libertarian|Green|Unity|Unaffiliated|American Constitution|"
    r"Approval Voting|Forward|Colorado Center|American Solidarity|Socialism and Liberation|"
    r"Constitution|Independent|Socialist Workers|Socialist|Party for Socialism and Liberation)$",
    re.I,
)


def _split_party(label: str) -> tuple[str, Optional[str]]:
    m = _PARTY_SUFFIX.search(label)
    if m:
        return label[: m.start()].strip(), m.group(1)
    return label, None


def parse_turnout(html: str) -> dict[str, dict]:
    """Return {short_precinct_code: {"ballots": int, "registered": int}}."""
    tree = HTMLParser(html)
    table = tree.css_first("table.EnrTurnoutTable")
    if table is None:
        raise ValueError("precinct turnout table not found")
    out: dict[str, dict] = {}
    for tr in table.css("tr"):
        cls = tr.attributes.get("class") or ""
        if not cls.startswith("EnrCandidateRow"):
            continue
        cells = [_text(td) for td in tr.css("td")]
        if len(cells) < 3:
            continue
        out[cells[0]] = {"ballots": to_int(cells[1]), "registered": to_int(cells[2])}
    if not out:
        raise ValueError("precinct turnout table has no rows")
    return out
