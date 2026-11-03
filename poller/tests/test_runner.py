"""Runner tests: last-good/stale handling and simulated source outages.

HTTP is served from RECORDED real fixtures through httpx.MockTransport; no
network access is used.
"""
import json
from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from poller import run
from poller.schema import Published
from poller.util import Http

FX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(Http, "get", Http.get.retry_with(wait=wait_none()))


def config(election_date="2024-11-05"):
    cfg = json.loads((run.STATIC / "config.json").read_text())
    cfg["election_date"] = election_date
    cfg["sources"]["co_sos"]["enabled"] = False
    cfg["sources"]["clarity_states"] = []
    return cfg


class Server:
    """Mock upstream. `mode` can be switched between polls to simulate outages."""

    def __init__(self):
        self.mode = "ok"
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        url = str(request.url)
        if self.mode == "down":
            raise httpx.ConnectError("simulated outage", request=request)
        if self.mode == "500":
            return httpx.Response(500, request=request)
        if url == "https://electionresults.bouldercounty.gov/index.html":
            name = "2026G_pre_results_index.html" if self.mode == "pre" else "2024G_index.html"
            return httpx.Response(200, text=(FX / "boulder" / name).read_text(errors="replace"), request=request)
        if url.endswith("Home/PrecinctTurnout.html"):
            return httpx.Response(200, text=(FX / "boulder/2024G_precinct_turnout.html").read_text(errors="replace"), request=request)
        if "er.ncsbe.gov/enr/20241105" in url:
            return httpx.Response(200, text=(FX / "nc/20241105_results_0_final.json").read_text(), request=request)
        return httpx.Response(404, request=request)


def http_for(server):
    return Http(httpx.Client(transport=httpx.MockTransport(server)))


def test_happy_path_writes_valid_file(tmp_path):
    srv = Server()
    pub = run.poll_once(tmp_path, http_for(srv), config())
    assert pub.sources["boulder"].status.status == "ok"
    assert len(pub.sources["boulder"].races) == 65
    assert pub.sources["ncsbe"].status.status == "ok"
    on_disk = Published.model_validate_json((tmp_path / "results.json").read_text())
    assert on_disk.sources.keys() == pub.sources.keys()
    z = pub.zip_80301
    assert z["label"] == "Approximate, based on precincts overlapping ZIP 80301."
    assert z["precinct_results_available"] is False
    assert z["turnout"]["core"]["precincts"] == 14
    assert z["turnout"]["core"]["ballots"] > 0


def test_outage_keeps_last_good_and_marks_stale(tmp_path):
    srv = Server()
    first = run.poll_once(tmp_path, http_for(srv), config())
    good_at = first.sources["boulder"].status.last_success_at

    srv.mode = "down"
    second = run.poll_once(tmp_path, http_for(srv), config())
    b = second.sources["boulder"]
    assert b.status.status == "stale"
    assert "simulated outage" in b.status.last_error
    assert b.status.last_success_at == good_at
    assert b.status.consecutive_failures == 1
    assert len(b.races) == 65 and all(r.stale for r in b.races)
    assert second.sources["ncsbe"].status.status == "stale"
    assert second.zip_80301["turnout"]["stale"] is True

    srv.mode = "500"
    third = run.poll_once(tmp_path, http_for(srv), config())
    assert third.sources["boulder"].status.consecutive_failures == 2
    assert "HTTP 500" in third.sources["boulder"].status.last_error

    srv.mode = "ok"
    fourth = run.poll_once(tmp_path, http_for(srv), config())
    assert fourth.sources["boulder"].status.status == "ok"
    assert not any(r.stale for r in fourth.sources["boulder"].races)


def test_retries_transient_errors(tmp_path):
    srv = Server()
    srv.mode = "500"
    run.poll_once(tmp_path, http_for(srv), config())
    # 2 sources hit (boulder, ncsbe) x 4 attempts each
    assert srv.calls == 8


def test_first_poll_failure_is_error_with_no_races(tmp_path):
    srv = Server()
    srv.mode = "down"
    pub = run.poll_once(tmp_path, http_for(srv), config())
    assert pub.sources["boulder"].status.status == "error"
    assert pub.sources["boulder"].races == []


def test_wrong_election_date_is_rejected(tmp_path):
    srv = Server()  # serves 2024 pages
    pub = run.poll_once(tmp_path, http_for(srv), config("2026-11-03"))
    b = pub.sources["boulder"]
    assert b.status.status == "error"
    assert "expected 2026-11-03" in b.status.last_error


def test_pre_results_page(tmp_path):
    srv = Server()
    srv.mode = "pre"
    pub = run.poll_once(tmp_path, http_for(srv), config("2026-11-03"))
    assert pub.sources["boulder"].status.status == "not_yet_available"


def test_results_regressing_to_placeholder_keeps_last_good(tmp_path):
    srv = Server()
    run.poll_once(tmp_path, http_for(srv), config())
    srv.mode = "pre"
    pub = run.poll_once(tmp_path, http_for(srv), config())
    assert pub.sources["boulder"].status.status == "stale"
    assert len(pub.sources["boulder"].races) == 65


def test_clarity_without_election_id_is_not_configured(tmp_path):
    cfg = config()
    cfg["sources"]["co_sos"]["enabled"] = True
    srv = Server()
    srv.mode = "pre"  # Boulder page has no Clarity link yet
    pub = run.poll_once(tmp_path, http_for(srv), cfg)
    assert pub.sources["co_sos"].status.status == "not_configured"
