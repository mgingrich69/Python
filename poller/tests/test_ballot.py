"""Ballot lineup parsers, tested on RECORDED official sources (see fixtures/README.md)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FX = Path(__file__).parent / "fixtures" / "ballot"
spec = importlib.util.spec_from_file_location("build_ballot", ROOT / "scripts" / "build_ballot.py")
bb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bb)


def boulder():
    return bb.parse_boulder_ballot((FX / "boulder_2026G_sample_ballot.pdftotext-raw.txt").read_text())


def sos():
    return bb.parse_sos((FX / "co_sos_2026G_candidates.html").read_text())


def test_boulder_contest_count_and_top_of_ticket():
    b = boulder()
    assert len(b) == 89
    sen = b[0]
    assert sen["contest"] == "United States Senator" and sen["race_id"] == "boulder-county:united-states-senator"
    assert {"name": "John Hickenlooper", "party": "Democratic"} in sen["candidates"]
    assert {"name": "Mark Baisley", "party": "Republican"} in sen["candidates"]
    assert all(c["name"] != "Write-in" for c in sen["candidates"])


def test_boulder_wrapped_headers_and_split_parties():
    by = {c["contest"]: c for c in boulder()}
    assert "Representative to the 120th United States Congress - District 2" in by
    assert "Regional Transportation District Director - District O" in by
    gov = by["Governor/Lieutenant Governor"]
    assert {"name": "Stephen T. Hamilton / James K Treibert", "party": "American Constitution"} in gov["candidates"]
    assert [c["name"] for c in by["State Treasurer"]["candidates"]] == ["Jeff Bridges", "Kevin Grantham", "Jodie Barr", "Marilee Langner Sturgis"]


def test_boulder_rcv_council_measures_retention():
    by = {c["contest"]: c for c in boulder()}
    assert [c["name"] for c in by["City of Boulder Mayor (ranked choice)"]["candidates"]][:3] == ["Lisa Ann Jacobs", "Taishya Adams", "Aaron Brockett"]
    assert len(by["City of Boulder City Council"]["candidates"]) == 13
    assert by["Amendment 81 (Constitutional)"]["is_ballot_measure"]
    q201 = by["Boulder County Ballot Question 201"]["candidates"]
    assert len(q201) == 2 and q201[0]["name"].startswith("Five commissioners")
    assert by["Retain Justice William W. Hood, III (Colorado Supreme Court)"]["level"] == "state"


def test_sos_list_federal_and_governor_pairs():
    s = {c["contest"]: c for c in sos()}
    assert len([k for k in s if k.startswith("U.S. House")]) == 8
    gov = s["Governor / Lieutenant Governor"]
    assert {"name": "Phil Weiser / Lesley Dahlkemper", "party": "Democratic"} in gov["candidates"]
    assert s["U.S. Senate"]["boulder_race_id"] == "boulder-county:united-states-senator"
    # Certified write-ins are excluded (not printed on the ballot).
    assert all(c["name"] != "Donald Willoughby" for c in s["U.S. Senate"]["candidates"])


def test_measure_summaries_from_official_text():
    by = {c["contest"]: c for c in boulder()}
    assert by["City of Boulder Ballot Issue 2J"]["summary"] == "Residential Vacancy Excise Tax (TABOR)"
    assert by["Proposition 132 (Statutory)"]["summary"] == "Concerning criminal penalties for fentanyl and certain synthetic opioids"
    assert by["Boulder County Ballot Question 201"]["summary"].startswith("If the membership of the Boulder County Board of Commissioners")
    assert "Front Range Passenger Rail District Ballot Issue 7A" in by  # district name recovered from ballot text
    assert all(len(c["summary"] or "") <= 160 for c in by.values() if c["is_ballot_measure"])
