"""Small shared helpers: slugs, level classification, percentages, HTTP."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

USER_AGENT = (
    "boulder-election-dashboard/1.0 (personal, non-commercial results dashboard; "
    "polls every 5 min; +https://github.com/mgingrich69/python)"
)
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\(vote for \d+\)", "", text, flags=re.I)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


def to_int(s: str) -> int:
    s = (s or "").strip().replace(",", "")
    if not re.fullmatch(r"\d+", s):
        raise ValueError(f"not an integer vote count: {s!r}")
    return int(s)


def fill_percents(candidates: list[dict]) -> bool:
    """Compute percent from votes where the source omits it. Returns True if computed."""
    total = sum(c["votes"] for c in candidates)
    computed = False
    for c in candidates:
        if c.get("percent") is None:
            c["percent"] = round(100.0 * c["votes"] / total, 1) if total else 0.0
            computed = True
    return computed


_FEDERAL = re.compile(
    r"\b(united states|u\.?s\.?)\s+(senat|represent|house|congress)|representative to the .*congress|"
    r"\bpresident(ial)?\b(?! of)",
    re.I,
)
_STATEWIDE = re.compile(
    r"\b(governor|lieutenant governor|secretary of state|attorney general|state treasurer|treasurer|"
    r"state senat|state represent|state board of education|regent|supreme court|court of appeals|"
    r"amendment|proposition|council of state|commissioner of)",
    re.I,
)
_COUNTY = re.compile(r"\bcounty\b|\bdistrict attorney\b|\bcoroner\b|\bsheriff\b|\bclerk\b|\bassessor\b|\bsurveyor\b|\bjudge\b|\bjustice\b", re.I)


def classify_level(name: str, *, county_source: bool = False) -> str:
    if _FEDERAL.search(name):
        return "national"
    if _STATEWIDE.search(name) and not re.search(r"\bcounty\b", name, re.I):
        return "state"
    if _COUNTY.search(name):
        return "county"
    return "local" if county_source else "state"


def is_ballot_measure(name: str, candidate_names: list[str]) -> bool:
    if re.search(r"\b(ballot (issue|question)|amendment|proposition|measure|referred|initiative)\b", name, re.I):
        return True
    names = {n.strip().lower() for n in candidate_names}
    return bool(names) and names <= {"yes", "no", "yes/for", "no/against", "for", "against"}


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class Http:
    """httpx client with timeouts, retry+backoff on transient errors, and an honest UA."""

    def __init__(self, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
        )

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(multiplier=2, max=20),
        reraise=True,
    )
    def get(self, url: str, **kw) -> httpx.Response:
        r = self.client.get(url, **kw)
        r.raise_for_status()
        return r

    def close(self) -> None:
        self.client.close()
