"""Wikipedia (MediaWiki API) adapter for national seat totals.

Reads the {{Infobox election}} of the 2026 Senate and House election articles:
seats before, seats after, and seat change per party. On election night
editors update these as AP and other outlets call races.

Access: official MediaWiki Action API, one request per poll for both pages,
identifying User-Agent, maxlag, and Retry-After respected. Content license:
CC BY-SA 4.0. It is community-edited, so the UI labels it as such and links
the exact revision shown.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

API = "https://en.wikipedia.org/w/api.php"
PAGES = {
    "senate": "2026 United States Senate elections",
    "house": "2026 United States House of Representatives elections",
}
TOTAL = {"senate": 100, "house": 435}
PARTY = {"republican party (united states)": "Republican", "democratic party (united states)": "Democratic",
         "independent politician": "Independent", "independent (politician)": "Independent"}


def _party_name(name: str) -> str:
    n = re.sub(r"\s*\((United States|US|U\.S\.)\)\s*$", "", name).strip()
    n = re.sub(r"\s+Party$", "", n)
    return {"Independent politician": "Independent"}.get(n, PARTY.get(name.lower(), n))


def page_url(title: str, revid: Optional[int] = None) -> str:
    base = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    return f"https://en.wikipedia.org/w/index.php?oldid={revid}" if revid else base


def _strip_templates(s: str) -> str:
    """Remove nested {{...}} templates, keeping simple change markers."""
    s = re.sub(r"\{\{\s*(increase|gain)\s*\}\}", "+", s, flags=re.I)
    s = re.sub(r"\{\{\s*(decrease|loss)\s*\}\}", "-", s, flags=re.I)
    s = re.sub(r"\{\{\s*steady\s*\}\}", "", s, flags=re.I)
    out, depth, i = [], 0, 0
    while i < len(s):
        if s.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if s.startswith("}}", i) and depth:
            depth -= 1
            i += 2
            continue
        if not depth:
            out.append(s[i])
        i += 1
    return "".join(out)


def clean(v: str) -> str:
    v = re.sub(r"<ref[^>]*/>", "", v)
    v = re.sub(r"<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    v = re.sub(r"<!--.*?-->", "", v, flags=re.S)
    v = _strip_templates(v)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"<[^>]+>", "", v)
    return v.replace("&nbsp;", " ").strip()


def _int(v: str) -> Optional[int]:
    v = clean(v).replace(",", "").replace("−", "-").replace("–", "-").replace(" ", "")
    m = re.fullmatch(r"([+-]?)(\d+)", v)
    if not m:
        return None
    n = int(m.group(2))
    return -n if m.group(1) == "-" else n


def infobox_params(wikitext: str) -> dict[str, str]:
    start = wikitext.find("{{Infobox election")
    if start < 0:
        raise ValueError("no {{Infobox election}} found")
    depth, i = 0, start
    while i < len(wikitext):
        if wikitext.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if wikitext.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                break
            continue
        i += 1
    body = wikitext[start + 2:i - 2]
    params: dict[str, str] = {}
    # split on top-level pipes only
    depth, cur, parts = 0, [], []
    j = 0
    while j < len(body):
        two = body[j:j + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            j += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            cur.append(two)
            j += 2
            continue
        if body[j] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
            j += 1
            continue
        cur.append(body[j])
        j += 1
    parts.append("".join(cur))
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip()] = v.strip()
    return params


def parse_chamber(wikitext: str, chamber: str) -> dict:
    p = infobox_params(wikitext)
    parties = []
    for n in range(1, 9):
        name = clean(p.get(f"party{n}", ""))
        if not name:
            continue
        parties.append({
            "party": _party_name(name),
            "seats_before": _int(p.get(f"seats_before{n}", "")),
            "seats_after": _int(p.get(f"seats_after{n}", "")),
            "seat_change": _int(p.get(f"seat_change{n}", "")),
            "popular_vote": _int(p.get(f"popular_vote{n}", "")),
        })
    if not any(x["seats_before"] is not None for x in parties):
        raise ValueError(f"{chamber}: infobox has no seats_before values; format change?")
    reported = any(x["seats_after"] is not None for x in parties)
    return {
        "chamber": chamber,
        "total_seats": TOTAL[chamber],
        "majority": TOTAL[chamber] // 2 + 1 if chamber == "house" else 51,
        "ongoing": clean(p.get("ongoing", "")).lower() == "yes",
        "results_reported": reported,
        "parties": parties,
    }


def parse_api_response(payload: dict) -> dict:
    pages = payload.get("query", {}).get("pages", [])
    by_title = {p.get("title"): p for p in pages}
    out = {}
    for chamber, title in PAGES.items():
        page = by_title.get(title)
        if not page or "revisions" not in page:
            raise ValueError(f"Wikipedia page missing: {title}")
        rev = page["revisions"][0]
        data = parse_chamber(rev["slots"]["main"]["content"], chamber)
        data.update(title=title, revid=rev.get("revid"), revision_time=rev.get("timestamp"),
                    url=page_url(title), revision_url=page_url(title, rev.get("revid")))
        out[chamber] = data
    return out


def snapshot(chambers: dict, at: datetime) -> dict:
    """Compact point for the trend chart: seats_after per party (None if not reported)."""
    return {"t": at.isoformat(), **{c: {p["party"]: p["seats_after"] for p in v["parties"]} for c, v in chambers.items()}}
