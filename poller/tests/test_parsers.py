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


# ------------------------------------------------------------------ Minnesota

from poller.sources import mnsos  # noqa: E402


def test_mn_2024_senate_final():
    races = mnsos.parse(read("mn/20241105_ussenate.txt"), election_date="20241105", fetched_at=NOW)
    assert len(races) == 1
    r = races[0]
    assert r.race_id == "mn:u-s-senator" and r.level == "national"
    k = next(c for c in r.candidates if c.name == "Amy Klobuchar")
    assert (k.votes, k.percent, k.party) == (1792441, 56.2, "Democratic-Farmer-Labor")
    assert r.votes == 3189323
    assert r.precincts_reporting.reporting == r.precincts_reporting.total == 4103
    assert r.called_status == "not_called"


def test_mn_2024_house_districts_are_separate_races():
    races = mnsos.parse(read("mn/20241105_ushouse.txt"), election_date="20241105", fetched_at=NOW)
    assert len(races) == 8
    assert len({r.race_id for r in races}) == 8
    d1 = races[0]
    assert {c.name: c.votes for c in d1.candidates}["Brad Finstad"] == 220929


def test_mn_2022_governor_is_state_level():
    races = mnsos.parse(read("mn/20221108_Governor.txt"), election_date="20221108", fetched_at=NOW)
    assert races[0].level == "state" and "Governor" in races[0].race_name
    assert races[0].votes == 2510661


def test_mn_2026_pre_results_lineup():
    races = mnsos.parse(read("mn/20261103_ussenate.txt"), election_date="20261103", fetched_at=NOW)
    names = [c.name for c in races[0].candidates]
    assert "Peggy Flanagan" in names and "Michele Tafoya" in names
    assert races[0].votes == 0 and races[0].precincts_reporting.reporting == 0


def test_mn_html_error_page_rejected():
    with pytest.raises(ValueError):
        mnsos.parse("<html><body>Not found</body></html>", election_date="20261103", fetched_at=NOW)


# ------------------------------------------------------------------ Wikipedia infobox

from poller.sources import wikipedia  # noqa: E402


def test_wikipedia_infobox_parse_senate_and_house():
    import json
    out = wikipedia.parse_api_response(json.loads(read("wikipedia/api_senate_house_2026-11-03.json")))
    sen = out["senate"]
    assert sen["majority"] == 51 and sen["total_seats"] == 100
    assert sum(p["seats_before"] for p in sen["parties"]) == 100
    assert out["house"]["majority"] == 218
    assert out["house"]["revision_url"].startswith("https://en.wikipedia.org/w/index.php?oldid=")


def test_wikipedia_clean_markup_and_change_markers():
    assert wikipedia._int("{{increase}} 4") == 4
    assert wikipedia._int("{{decrease}} 2") == -2
    assert wikipedia._int("45{{efn|name=Independents|Both independent {{nowrap|senators}}}}") == 45
    assert wikipedia._int("") is None


def test_wikipedia_seats_after_when_reported():
    text = "{{Infobox election\n| party1 = Republican Party (United States)\n| seats_before1 = 53\n| seats_after1 = 51\n| seat_change1 = {{decrease}} 2\n| party2 = Democratic Party (United States)\n| seats_before2 = 45\n| seats_after2 = 47\n| seat_change2 = {{increase}} 2\n}}"
    c = wikipedia.parse_chamber(text, "senate")
    assert c["results_reported"] is True
    assert [(p["party"], p["seats_after"], p["seat_change"]) for p in c["parties"]] == [("Republican", 51, -2), ("Democratic", 47, 2)]
