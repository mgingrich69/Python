"""Parser/normalizer tests against RECORDED real responses (see fixtures/README.md).

These fixtures are historical official results saved verbatim from the source
sites. They exist only for tests and are never shipped to the dashboard.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from poller.sources import boulder, ncsbe
from poller.util import classify_level, slugify

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def read(rel):
    return (FX / rel).read_text(encoding="utf-8", errors="replace")


# ------------------------------------------------------------------ Boulder

def test_boulder_2024_general_parses_all_contests():
    meta, races = boulder.parse_index(read("boulder/2024G_index.html"), fetched_at=NOW)
    assert meta["title"] == "2024 General Election"
    assert meta["subtitle"] == "Official Results"
    assert meta["ballots_counted"] == 199319
    assert meta["clarity_election_id"] == "122598"
    assert meta["source_updated_at"].isoformat() == "2024-12-05T15:05:00-07:00"
    assert len(races) == 65
    ids = [r.race_id for r in races]
    assert len(ids) == len(set(ids)), "race ids must be unique"

    cd2 = next(r for r in races if "Congress - District 2" in r.race_name)
    assert cd2.level == "national"
    neguse = next(c for c in cd2.candidates if c.name == "Joe Neguse")
    assert (neguse.party, neguse.votes) == ("Democratic", 145762)
    assert cd2.percent_computed is True
    assert abs(sum(c.percent for c in cd2.candidates) - 100) < 0.5


def test_boulder_official_results_map_to_certified_not_called_winner():
    _, races = boulder.parse_index(read("boulder/2024G_index.html"), fetched_at=NOW)
    for r in races:
        assert r.called_status == "certified"
        # The county never flags winners, so we must not invent any.
        assert not any(c.winner for c in r.candidates)


def test_boulder_ballot_measures_and_levels():
    _, races = boulder.parse_index(read("boulder/2024G_index.html"), fetched_at=NOW)
    by = {r.race_name: r for r in races}
    rtd = by["Regional Transportation District Ballot Issue 7A"]
    assert rtd.is_ballot_measure and [c.name for c in rtd.candidates] == ["Yes/For", "No/Against"]
    assert by["Amendment G (Constitutional)"].level == "state"
    assert by["Boulder County Commissioner - District 1"].level == "county"
    assert by["Town of Erie - Mayor"].level == "local"
    assert by["Presidential Electors"].level == "national"
    assert by["State Senator - District 17"].level == "state"


def test_boulder_2026_primary_unofficial_header_variant():
    meta, races = boulder.parse_index(read("boulder/2026P_index.html"), fetched_at=NOW)
    assert meta["ballots_counted"] == 100423
    assert len(races) == 44
    sen = next(r for r in races if r.race_name == "United States Senator - Democratic Party")
    assert {c.name: c.votes for c in sen.candidates} == {"Julie Gonzales": 41430, "John Hickenlooper": 43566}
    assert sen.votes == 84996  # matches the county's own aggregate in the page


def test_boulder_pre_results_page_is_not_yet_available():
    with pytest.raises(boulder.NotYetAvailable, match="after 7:00 PM on November 3, 2026"):
        boulder.parse_index(read("boulder/2026G_pre_results_index.html"), fetched_at=NOW)


def test_boulder_layout_change_raises_instead_of_publishing_garbage():
    html = read("boulder/2024G_index.html").replace("EnrCandidateVotes", "SomethingElse")
    with pytest.raises(ValueError):
        boulder.parse_index(html, fetched_at=NOW)


def test_boulder_tampered_vote_cell_detected():
    html = read("boulder/2024G_index.html").replace(">145,762<", ">145,763<", 1)
    with pytest.raises(ValueError, match="mismatch"):
        boulder.parse_index(html, fetched_at=NOW)


def test_boulder_precinct_turnout():
    t = boulder.parse_turnout(read("boulder/2024G_precinct_turnout.html"))
    assert len(t) == 190
    assert t["504"] == {"ballots": 2133, "registered": 2438}
    assert t["100"] == {"ballots": 1325, "registered": 1569}


# ------------------------------------------------------------------ NCSBE

def test_ncsbe_2024_final_federal_contests():
    races = ncsbe.parse(read("nc/20241105_results_0_final.json"), election_date="20241105", fetched_at=NOW)
    assert len(races) == 15
    pres = races[0]
    assert pres.race_id == "nc:us-president"
    assert pres.level == "national"
    trump = next(c for c in pres.candidates if c.name == "Donald J. Trump")
    assert (trump.votes, trump.percent, trump.party) == (2898423, 50.9, "Republican")
    assert pres.precincts_reporting.reporting == pres.precincts_reporting.total == 2658
    assert pres.called_status == "not_called"  # official counts carry no race calls


def test_ncsbe_2026_pre_results_has_candidates_but_no_votes():
    races = ncsbe.parse(read("nc/20261103_results_0_pre_results.json"), election_date="20261103", fetched_at=NOW)
    sen = next(r for r in races if r.race_id == "nc:us-senate")
    assert "Roy Cooper" in [c.name for c in sen.candidates]
    assert sen.votes == 0 and sen.precincts_reporting.reporting == 0


# ------------------------------------------------------------------ utils

@pytest.mark.parametrize("name,level", [
    ("US SENATE", "national"),
    ("Representative to the 120th United States Congress - District 2", "national"),
    ("State Board of Education Member - Congressional District 2", "state"),
    ("Governor - Democratic Party", "state"),
    ("Boulder County Sheriff", "county"),
])
def test_classify_level(name, level):
    assert classify_level(name, county_source=True) == level


def test_slugify_strips_vote_for():
    assert slugify("US SENATE (VOTE FOR 1)") == "us-senate"
