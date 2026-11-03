#!/usr/bin/env python3
"""Build data/static/ballot_2026.json: the 2026 General Election ballot lineups for
the Colorado and Boulder tabs, so contests show before results exist.

Official sources (fetched once, offline; commit the output):
  * Colorado Secretary of State – 2026 General Election Official Candidate List
    https://www.sos.state.co.us/pubs/elections/vote/generalCandidates.html
  * Boulder County Elections – Compiled Sample Ballot, 2026 General Election (PDF)
    https://assets.bouldercounty.gov/wp-content/uploads/2026/09/Boulder-County-2026-General-Sample-Ballot.pdf

Requires `pdftotext` (poppler-utils) for the PDF. Run:
  python scripts/build_ballot.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import httpx
from selectolax.lexbor import LexborHTMLParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from poller.util import classify_level, slugify  # noqa: E402

SOS_URL = "https://www.sos.state.co.us/pubs/elections/vote/generalCandidates.html"
BOULDER_PDF_URL = "https://assets.bouldercounty.gov/wp-content/uploads/2026/09/Boulder-County-2026-General-Sample-Ballot.pdf"
UA = "boulder-election-dashboard/1.0 (personal, non-commercial; one-off ballot extract)"

PARTIES = ["American Constitution", "Approval Voting", "Democratic", "Republican", "Libertarian", "Forward",
           "Unity", "Unaffiliated", "Green", "Colorado Center", "American Solidarity", "Socialism and Liberation"]

# ------------------------------------------------------------------ Secretary of State list

SOS_OFFICES_STATEWIDE = {"US Senate", "Governor", "Lt. Governor", "Secretary of State", "State Treasurer", "Attorney General"}
SOS_KEEP = SOS_OFFICES_STATEWIDE | {"US House of Representatives", "University of Colorado Board of Regents",
                                    "State Board of Education", "State Senate", "State House of Representatives",
                                    "RTD Board of Directors"}


def _party(p: str) -> str | None:
    p = re.sub(r"\s+Party$", "", (p or "").strip())
    return p or None


def sos_contest_name(office: str, district: str) -> str:
    d = district.strip()
    if office == "US Senate":
        return "U.S. Senate"
    if office == "US House of Representatives":
        return f"U.S. House – District {d}"
    if office == "Governor":
        return "Governor / Lieutenant Governor"
    if office == "University of Colorado Board of Regents":
        return "CU Regent – " + ("At Large" if d.lower() in ("state", "at large", "at-large") else f"Congressional District {d}")
    if office == "State Board of Education":
        return f"State Board of Education – District {d}"
    if office == "State Senate":
        return f"State Senate – District {d}"
    if office == "State House of Representatives":
        return f"State House – District {d}"
    if office == "RTD Board of Directors":
        return f"RTD Director – District {d}"
    return office


def sos_boulder_title(office: str, district: str) -> str | None:
    """The equivalent contest title on Boulder County's ballot/results (for attaching Boulder votes)."""
    d = district.strip()
    return {
        "US Senate": "United States Senator",
        "Governor": "Governor/Lieutenant Governor",
        "Secretary of State": "Secretary of State",
        "State Treasurer": "State Treasurer",
        "Attorney General": "Attorney General",
    }.get(office) or ({
        "US House of Representatives": f"Representative to the 120th United States Congress - District {d}",
        "State Senate": f"State Senator - District {d}",
        "State House of Representatives": f"State Representative - District {d}",
        "University of Colorado Board of Regents": f"Regent of the University of Colorado - Congressional District {d}",
        "RTD Board of Directors": f"Regional Transportation District Director - District {d}",
    }.get(office))


def parse_sos(html: str) -> list[dict]:
    tree = LexborHTMLParser(html)
    contests: "OrderedDict[str, dict]" = OrderedDict()
    lt: dict[str, str] = {}
    for tr in tree.css("table tr"):
        tds = tr.css("td")
        if len(tds) < 5:
            continue
        if tr.css_first("s, del, strike") or "line-through" in tr.html:
            continue  # withdrawn
        name, office, district, party, writein = (td.text(strip=True) for td in tds[:5])
        if writein.upper() == "Y" or office not in SOS_KEEP:
            continue
        if office == "Lt. Governor":
            lt[_party(party) or ""] = name
            continue
        key = f"{office}|{district}"
        c = contests.setdefault(key, {
            "contest": sos_contest_name(office, district),
            "office": office, "district": district,
            "boulder_title": sos_boulder_title(office, district),
            "candidates": [],
        })
        c["candidates"].append({"name": name, "party": _party(party)})
    for c in contests.values():  # pair governor tickets by party
        if c["office"] == "Governor":
            for cand in c["candidates"]:
                mate = lt.get(cand["party"] or "")
                if mate:
                    cand["name"] = f"{cand['name']} / {mate}"
    out = []
    for c in contests.values():
        lvl = "national" if c["office"].startswith("US ") else "state"
        out.append({"contest": c["contest"], "level": lvl, "is_ballot_measure": False,
                    "boulder_race_id": f"boulder-county:{slugify(c['boulder_title'])}" if c["boulder_title"] else None,
                    "candidates": c["candidates"]})
    if len(out) < 20:
        raise ValueError(f"only {len(out)} contests parsed from SOS list; layout change?")
    return out


# ------------------------------------------------------------------ Boulder sample ballot

_SKIP = re.compile(
    r"^(Federal Offices|State Offices|County Offices|Local Offices|Judicial Retention Questions|\(Vote YES or NO\)|"
    r"Write-in|\d+|[A-Za-z]|Not an official ballot\.?|every ballot - Not an official ballot\.|Instructions for Voting:.*|"
    r"Compiled Sample Ballot.*|Boulder County, Colorado - .*|Boulder County compiled ballot content.*|\d\).*|WARNING:.*|"
    r"falsely makes.*|upon conviction.*|will not be counted\.|Instructions.*|YES NO|YES/FOR NO/AGAINST|certified candidate on the line.*|"
    r"Local Ballot Measures|State Ballot Measures|County Ballot Measures|"
    r"Colorado Supreme Court Justice|Colorado Court of Appeals Judge|District Court Judge.*|County Court Judge.*)$")
_MEASURE = re.compile(r"^((Amendment|Proposition) [0-9A-Z]+ \((CONSTITUTIONAL|STATUTORY)\)|.*Ballot (Issue|Question) [0-9A-Z]+)$")
_VOTEFOR = re.compile(r"^(.*?)\s*\((Vote for [^)]*)\)$", re.I)


def _strip_party(line: str) -> tuple[str, str | None]:
    for p in sorted(PARTIES, key=len, reverse=True):
        if line.endswith(" " + p):
            return line[: -len(p) - 1].strip(), p
    return line.strip(), None


def parse_boulder_ballot(raw: str) -> list[dict]:
    lines = [l.strip() for l in raw.splitlines()]
    lines = [l for l in lines if l]
    # Merge wrapped header lines and split party names.
    merged: list[str] = []
    i = 0
    while i < len(lines):
        l = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (l.endswith(" -") or l.endswith("Congressional") or re.search(r"\(Vote for no more than$", l)
                or (nxt.startswith("(Vote for") and not _VOTEFOR.match(l))):
            merged.append(f"{l} {nxt}".replace("- District", "- District"))
            i += 2
            continue
        if l == "Constitution" and merged and merged[-1].endswith(" American"):
            merged[-1] += " Constitution"
            i += 1
            continue
        merged.append(l)
        i += 1

    contests: list[dict] = []
    cur: dict | None = None
    mode = None  # "cands" | "mayor" | "council" | "measure" | None
    for idx, l in enumerate(merged):
        m = _VOTEFOR.match(l)
        if m and mode == "measure" and cur is not None and not cur.get("retention"):
            cur.setdefault("text", []).append(m.group(1))
            cur["candidates"] = []
            mode = "measure_choices"
            continue
        if mode == "measure_choices":
            if _MEASURE.match(l) or _SKIP.match(l):
                mode = None
            else:
                if cur["candidates"] and not cur["candidates"][-1]["name"].endswith(("; or", ".")):
                    cur["candidates"][-1]["name"] += " " + l
                else:
                    cur["candidates"].append({"name": l, "party": None})
                continue
        if m:
            cur = {"contest": re.sub(r"\s+", " ", m.group(1)).strip(), "vote_for": m.group(2), "candidates": [], "is_ballot_measure": False}
            contests.append(cur)
            mode = "cands"
            continue
        if l == "City of Boulder Mayoral Candidates":
            cur = {"contest": "City of Boulder Mayor (ranked choice)", "vote_for": "Rank up to six", "candidates": [], "is_ballot_measure": False}
            contests.append(cur)
            mode = "mayor"
            continue
        if l == "City of Boulder Council Candidates":
            cur = {"contest": "City of Boulder City Council", "vote_for": "Vote for up to five", "candidates": [], "is_ballot_measure": False}
            contests.append(cur)
            mode = "council_intro"
            continue
        if _MEASURE.match(l):
            cur = {"contest": l.replace("(CONSTITUTIONAL)", "(Constitutional)").replace("(STATUTORY)", "(Statutory)"),
                   "candidates": [{"name": "Yes/For", "party": None}, {"name": "No/Against", "party": None}],
                   "is_ballot_measure": True}
            contests.append(cur)
            mode = "measure"
            continue
        rm = re.match(r"^Shall (Justice|Judge) (.+?) of the (.+?)(?: be retained in office\?)?$", l)
        if rm:
            text = l if l.endswith("?") else f"{l} {merged[idx + 1] if idx + 1 < len(merged) else ''}"
            rm2 = re.match(r"^Shall (Justice|Judge) (.+?) of the (.+?) be retained in office\?", text)
            if rm2:
                cur = {"contest": f"Retain {rm2.group(1)} {rm2.group(2)} ({rm2.group(3)})",
                       "candidates": [{"name": "Yes", "party": None}, {"name": "No", "party": None}],
                       "is_ballot_measure": True, "retention": True}
                contests.append(cur)
                mode = "measure"
                continue
        if mode == "measure" and cur is not None and cur["is_ballot_measure"] and not cur.get("retention"):
            if l.startswith(("YES/FOR", "YES NO")) or _SKIP.match(l):
                if l.startswith("YES"):
                    mode = "measure_done"
                continue
            cur.setdefault("text", []).append(l)
            continue
        if mode in ("measure", "measure_done") or cur is None:
            continue  # retention text, boilerplate
        if mode == "mayor":
            prev = merged[idx - 1] if idx else ""
            if prev == "1" and l != "2 3 4 5 6" and not _SKIP.match(l):
                cur["candidates"].append({"name": l, "party": None})
            continue
        if mode == "council_intro":
            if l.endswith("will not be counted."):
                mode = "cands_plain"
            continue
        if _SKIP.match(l) or l.startswith(("Following are", "five (5)", "race will", "Rank ", "Special Instructions",
                                           "In the ", "One to be Elected", "·", "Choice", "1st", "2nd", "3rd", "4th", "5th", "6th")):
            if l in ("Federal Offices", "State Offices", "County Offices", "Local Offices", "Judicial Retention Questions"):
                mode = None
            continue
        if mode in ("cands", "cands_plain"):
            name, party = _strip_party(l)
            cur["candidates"].append({"name": name, "party": party})

    out = []
    for c in contests:
        if c["is_ballot_measure"] and not c.get("retention"):
            c["summary"] = measure_summary(c.get("text", []))
            if re.fullmatch(r"Ballot (Issue|Question) \w+", c["contest"]):
                d = re.search(r"^SHALL (?:THE )?(.+? DISTRICT)\b", " ".join(c.get("text", [])), re.I)
                if d:
                    c["contest"] = f"{d.group(1).title()} {c['contest']}"
        if not c["candidates"]:
            raise ValueError(f"Boulder ballot contest without candidates: {c['contest']}")
        name = c["contest"]
        out.append({
            "contest": name,
            "race_id": f"boulder-county:{slugify(name)}",
            "level": "state" if c.get("retention") and "Supreme Court" in name or (c.get("retention") and "Court of Appeals" in name)
                     else classify_level(name, county_source=True),
            "is_ballot_measure": c["is_ballot_measure"],
            "vote_for": c.get("vote_for"),
            "summary": c.get("summary"),
            "candidates": c["candidates"],
        })
    if len(out) < 40:
        raise ValueError(f"only {len(out)} contests parsed from Boulder sample ballot; layout change?")
    return out


def _sentence(t: str) -> str:
    t = t.strip()
    if t.isupper():
        t = t.lower()
    return t[:1].upper() + t[1:]


def measure_summary(lines: list[str]) -> str | None:
    """A one-line description from the official ballot text (title line, or the 'concerning' clause)."""
    if not lines:
        return None
    title = []
    for l in lines:  # local measures start with an ALL-CAPS title (may wrap), before "SHALL ..."
        if l.upper() == l and not l.upper().startswith("SHALL"):
            title.append(l)
        else:
            break
    def cap(t: str) -> str:
        return t if len(t) <= 160 else t[:157].rsplit(" ", 1)[0] + "…"
    if title:
        t = " ".join(title).title().replace("(Tabor)", "(TABOR)")
        return cap(re.split(r",\s*Shall\b", t)[0])
    first = lines[0]
    if not re.match(r"^(shall|if)\b", first, re.I):
        return cap(first)  # e.g. "Bargaining Rights Union Firefighters in City Charter"
    text = " ".join(lines)
    text = re.sub(r"^Shall there be (an amendment to the Colorado Constitution|a change to the Colorado Revised Statutes)\s*", "", text, flags=re.I)
    text = re.split(r",\s*and,? in connection therewith|\?", text, maxsplit=1)[0]
    text = _sentence(text)
    return text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0] + "…"


def statewide_from_boulder(boulder: list[dict]) -> list[dict]:
    """Statewide measures and statewide judicial retention questions (they appear on every CO ballot)."""
    keep = []
    for c in boulder:
        n = c["contest"]
        if re.match(r"^(Amendment|Proposition) ", n) or (n.startswith("Retain") and ("Supreme Court" in n or "Court of Appeals" in n)):
            keep.append({"contest": n, "level": "state", "is_ballot_measure": True, "boulder_race_id": c["race_id"],
                         "summary": c.get("summary"), "candidates": c["candidates"]})
    return keep


def main() -> int:
    with httpx.Client(timeout=60, headers={"User-Agent": UA}, follow_redirects=True) as cl:
        sos_html = cl.get(SOS_URL).raise_for_status().text
        pdf = cl.get(BOULDER_PDF_URL).raise_for_status().content
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.pdf"
        p.write_bytes(pdf)
        raw = subprocess.run(["pdftotext", "-raw", str(p), "-"], check=True, capture_output=True, text=True).stdout
    boulder = parse_boulder_ballot(raw)
    colorado = parse_sos(sos_html) + statewide_from_boulder(boulder)
    doc = {
        "election_date": "2026-11-03",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Ballot lineups only (no votes). Shown until results for a contest are published.",
        "sources": {
            "colorado": [{"name": "Colorado Secretary of State – 2026 General Election Official Candidate List", "url": SOS_URL},
                         {"name": "Boulder County compiled sample ballot (statewide measures and retention questions)", "url": BOULDER_PDF_URL}],
            "boulder": [{"name": "Boulder County Elections – 2026 General Compiled Sample Ballot", "url": BOULDER_PDF_URL}],
        },
        "colorado": colorado,
        "boulder": boulder,
    }
    out = ROOT / "data/static/ballot_2026.json"
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out}: {len(colorado)} Colorado contests, {len(boulder)} Boulder contests", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
