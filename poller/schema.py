"""Normalized result schema shared by every source adapter.

Every adapter must emit `Race` objects. Validation happens here so that a
parser bug (or an upstream format change) fails loudly instead of publishing
garbage: the runner then keeps the last good data and marks the source stale.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Level = Literal["national", "state", "county", "local"]
CalledStatus = Literal["not_called", "called", "certified", "runoff"]
SourceState = Literal["ok", "stale", "error", "not_yet_available", "not_configured"]


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    party: Optional[str] = None
    votes: int = Field(ge=0)
    percent: Optional[float] = Field(default=None, ge=0, le=100)
    # True ONLY when the source itself flags this candidate as the winner.
    winner: bool = False


class PrecinctsReporting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reporting: int = Field(ge=0)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "PrecinctsReporting":
        if self.total and self.reporting > self.total:
            raise ValueError(f"precincts reporting {self.reporting} > total {self.total}")
        return self


class Race(BaseModel):
    model_config = ConfigDict(extra="forbid")

    race_id: str = Field(pattern=r"^[a-z0-9]+(?:[:\-][a-z0-9]+)*$")
    race_name: str = Field(min_length=1)
    level: Level
    geography: str  # e.g. "us", "co", "nc", "boulder-county"
    candidates: list[Candidate] = Field(min_length=1)
    votes: int = Field(ge=0)
    percent_computed: bool = False
    vote_for: Optional[int] = None
    is_ballot_measure: bool = False
    precincts_reporting: Optional[PrecinctsReporting] = None
    ballots_counted: Optional[int] = None
    called_status: CalledStatus = "not_called"
    called_by: Optional[str] = None
    source: str
    source_name: str
    source_url: str
    fetched_at: datetime
    source_updated_at: Optional[datetime] = None
    stale: bool = False
    approximate: bool = False
    approx_note: Optional[str] = None

    @field_validator("fetched_at", "source_updated_at")
    @classmethod
    def _tz_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> "Race":
        total = sum(c.votes for c in self.candidates)
        if total != self.votes:
            raise ValueError(f"{self.race_id}: votes {self.votes} != candidate sum {total}")
        winners = [c for c in self.candidates if c.winner]
        if winners and self.called_status == "not_called":
            raise ValueError(f"{self.race_id}: winner flagged but race not called by source")
        return self


class SourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    url: str
    status: SourceState
    message: Optional[str] = None
    last_attempt_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0


class SourceBlock(BaseModel):
    """What one source contributes to the published file."""

    model_config = ConfigDict(extra="forbid")

    status: SourceStatus
    races: list[Race] = []
    extras: dict = {}  # source-specific supplementary data (e.g. precinct turnout)


class Published(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    generated_at: datetime
    poll_interval_seconds: int
    sources: dict[str, SourceBlock]
    zip_80301: Optional[dict] = None
    links: list[dict] = []
