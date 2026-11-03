# Election Night 2026 dashboard

A mobile-first, static results dashboard for the **Nov 3, 2026** general election, with three tabs: **National**, **Colorado**, and **Boulder / 80301**. Results come straight from official election offices. They are **unofficial**, and a race shows **Not called** unless the source itself marks a winner or certifies it.

```
GitHub Actions (poll.yml, loops every 5 min)
  └─ python -m poller.run  → fetch → parse → validate (pydantic) → state/data/results.json
       └─ scripts/publish.sh → force-push branch `data` → trigger deploy.yml
GitHub Actions (deploy.yml) → web/ + data/results.json → GitHub Pages
Browser → data/results.json every 60 s (+ Refresh button)
```

## What's live, and what isn't (honest status, verified Nov 3 2026)

| Source | Tab | Status |
|---|---|---|
| Boulder County Elections (`electionresults.bouldercounty.gov`) | Boulder; Colorado (as "Boulder County votes only") | ✅ Works from GitHub runners. HTML parser tested on real 2024G/2026P pages. Results post after 7:00 PM MT. |
| Boulder County precinct turnout | Boulder (80301) | ✅ Same site, per-precinct ballots processed. |
| NC State Board of Elections (`er.ncsbe.gov`) | National (NC Senate + 14 House) | ✅ Official JSON, works from runners. Polls close 7:30 PM ET. |
| Minnesota Secretary of State (`electionresultsfiles.sos.mn.gov`) | National (MN Senate, Governor, 8 House) | ✅ Official media files. Polls close 8 PM CT. |
| Colorado Secretary of State (Clarity ENR) | Colorado | ❌ Clarity answers automated clients with a **bot challenge (HTTP 202, empty body)**. Confirmed from GitHub runners. We don't get around it, so the tab says so and links to the official page and news outlets. The adapter is built and tested, and starts working automatically if access is ever allowed. |
| Georgia SOS (Clarity) | National | ❌ Disabled for the same reason. |
| AP / Decision Desk HQ (race calls) | — | Paid, contract only. Not used. News sites (CNN, NYT, AP…) are **linked, not scraped**: their terms forbid scraping and their data is licensed from AP/DDHQ. |

**Ballot lineups.** Before results post, the Colorado and Boulder tabs show every contest and candidate. The data is in `data/static/ballot_2026.json`, built by `scripts/build_ballot.py` from the Secretary of State's official certified candidate list and Boulder County's compiled sample ballot. Boulder results replace the lineups as soon as the county posts them. Colorado statewide contests keep the official lineup and add Boulder County's votes once available, labeled as one county.

**Balance of power (Wikipedia).** National seat totals (seats before/after and seat change per party, Senate and House) come from the infoboxes of the Wikipedia articles on the 2026 Senate and House elections, read through the official MediaWiki API: one request per poll, identifying User-Agent, `maxlag`, no retry on HTTP 429. Editors update these as AP and other outlets call races. The content is community-edited (CC BY-SA 4.0), so the UI labels it and links the exact revision shown. Every change is kept in `extras.history` to draw the trend chart.

**Not shown by design:** judicial retention questions, state legislature, and RTD director contests. Statewide and federal contests appear only on the Colorado tab; the Boulder tab shows county and local contests.

**Senate watch list.** `config.json → watch.senate` lists 15 Senate races rated other than Solid/Safe before the election. Live numbers appear only for NC and MN; the other tiles link to the state's official results.

**U.S. House** is shown as a summary graphic (leaning D / leaning R / close under 5 points / awaiting) for the states with feeds, plus a list of close races. "Leaning" means the current leader, not a projection.

**ZIP 80301** is approximate. The county's address points with postal ZIP 80301 were mapped onto the county's precinct polygons, giving 23 overlapping precincts, 14 of them "core" (≥ 50% of addresses in 80301). See `data/static/precincts_80301.json`. Boulder County publishes **contest results countywide only** on election night. So the 80301 section shows **turnout** for those precincts and never estimates vote shares. Precinct-level vote results appear in the county's Statement of Votes after certification.

## One-time setup (do this before 7 PM MT)

1. **Make Pages possible.** The repo must be public (done 2026-11-03) or on a paid plan.
2. **Settings → Pages → Build and deployment → Source: _GitHub Actions_.**
3. **Settings → Actions → General → Workflow permissions:** "Read and write permissions" (the poller pushes the `data` branch and triggers `deploy`).
4. Run **Actions → deploy → Run workflow** once. The site URL appears in the run summary (normally `https://mgingrich69.github.io/Python/`).
5. Optional: **Actions → poll → Run workflow → mode `loop`** restarts polling immediately if no poll run is in progress.

Polling schedule: a 5-minute loop runs continuously Nov 3–6 (UTC), then one poll per hour through Nov 30. The repo is public, so Actions minutes are free.

## Environment variables / configuration

No API keys are required. Optional repository **Variables** (Settings → Secrets and variables → Actions → Variables):

| Name | Purpose |
|---|---|
| `CO_SOS_ELECTION_ID` | Force the Colorado Clarity election ID. Otherwise it's auto-discovered from the Boulder results page's "Statewide Contests" link. Only useful if Clarity ever allows automated access. |
| `GA_SOS_ELECTION_ID` | Same for Georgia (adapter disabled in `config.json`). |

Everything else lives in `data/static/config.json`: election date, poll interval, enabled sources, news links.

## Local development

```bash
pip install -r requirements-dev.txt
python -m pytest -q poller/tests              # parsers, normalizer, outage simulation
python -m poller.run --out web/data           # one live poll → web/data/results.json
python -m http.server -d web 8000             # open http://localhost:8000
```
`web/data/` is git-ignored output. The deploy job builds the site from the `data` branch instead.

Rebuild the 80301 mapping (only if the county changes precincts): `python scripts/build_80301_precincts.py`.

## Election-night runbook

**Is polling working?**
1. On the page header, "Data as of …" should be less than about 7 minutes old. Past 15 minutes, a yellow banner appears with a "Request an update" link.
2. **Actions → poll**: one run should be *in progress* (mode loop) and one *queued*. Open the run's "Poll" step. Every 5 minutes it logs a line like `poll complete: boulder=ok(65 races) | co_sos=error(0 races) ERR … | ncsbe=ok(15 races)`.
3. **Actions → deploy**: a successful run roughly every 5 minutes.
4. Each source's status is shown on its tab under **Source status** (Live / Waiting / Stale / Error, plus the last error).

**Manual update now:** Actions → poll → Run workflow → mode `once`. It runs separately from the loop, polls immediately and deploys.

**Polling stopped (no run in progress):** Run workflow → mode `loop`. Cron start-up can lag 10+ minutes on busy nights, so a manual start is faster.

**A source shows Stale/Error:**
- `HTTP 5xx` / `ConnectError` / timeout: the upstream site is struggling. The poller already retries 4× with backoff every cycle and keeps the last good data (marked stale). Usually nothing to do.
- `Boulder page is for <date>, expected 2026-11-03`: the county page is showing an old election. Wait, or check `https://electionresults.bouldercounty.gov/` by hand.
- `no contest grids found` / `vote cell mismatch` / `has no title`: **the county changed its page layout.** Fix fast:
  1. `curl -o /tmp/new.html https://electionresults.bouldercounty.gov/index.html`
  2. Compare its contest markup with `poller/tests/fixtures/boulder/2024G_index.html` (look for `EnrContestTitleHeader`, `EnrCandidateName`, `EnrCandidateVotes`).
  3. Adjust the selectors in `poller/sources/boulder.py`, save `/tmp/new.html` as a new fixture, add a test, run `pytest`, then commit and push. The next poll picks up the change (or start a `once` run).
- `NCSBE results file is empty`: NC hasn't posted yet, or the URL date is wrong (`data/static/config.json → election_date`).
- `bot challenge`: Clarity still blocks automated access. Expected; nothing to fix.

**Turn a source off fast:** set `"enabled": false` for it in `data/static/config.json`, then commit and push.

## Design rules enforced in code

- **No invented calls:** `Race` validation rejects a `winner` unless `called_status` is set by the source. Official sources here never call races, and "Official Results" on the Boulder page maps to `certified`.
- **No silent merges:** each record keeps its `source`. When two sources report the same `race_id`, the card shows both side by side with timestamps and a "Sources differ" badge.
- **Graceful degradation:** any fetch, parse or validation failure keeps the last good data, sets `stale`, and records `last_error`. Each poll also checks the election date, so a page for the wrong election is rejected.
- **Polite polling:** honest User-Agent, one request per source per 5 minutes, no browser spoofing, no bypassing bot checks.

## Limitations

- No statewide Colorado totals: the official feed blocks automated access. The Colorado tab shows Boulder County's votes on statewide races, clearly labeled, plus links.
- National coverage is limited to North Carolina and Minnesota. Feeds for TX, AZ, PA, OH, MI, FL, ME, NH (and Clarity states such as GA) refused automated requests when tested on Nov 3. Other states either block automated access, have no free machine-readable feed I could verify in time, or would need per-state adapters. There are no race calls without an AP/DDHQ license.
- 80301 shows turnout only. Its precinct overlap counts addresses, not voters.
- Updates take about 5 minutes for the poll plus about 1 minute for the Pages deploy. GitHub cron can start late. Use manual `once` runs if needed.

## Data sources and licenses

Boulder County Elections; Colorado Secretary of State; NC State Board of Elections (public records). Boulder County GIS precincts and address points (CC BY 4.0). Test fixtures in `poller/tests/fixtures/` are recorded official responses used only by tests.
