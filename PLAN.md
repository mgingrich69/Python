# Election Results Dashboard — PLAN (Phase 1, awaiting approval)

_Drafted 2026-11-03 ~02:40 UTC (Mon Nov 2, 7:40 PM MT). Colorado polls close Tue Nov 3, 7:00 PM MT (Nov 4 02:00 UTC), roughly 23 hours from now._

## 1. Goal

A fast, mobile-first, static dashboard with three tabs (**National**, **Colorado**, **Boulder / 80301**). It shows live unofficial results from official or licensed sources, gives the source and "last updated" time on every card, and never shows a winner the source has not called.

## 2. Research findings (verified 2026-11-03 unless noted)

### 2.1 Colorado Secretary of State (state and federal races in CO)
- The SOS publishes election-night results through **Clarity ENR** (Civera/Scytl) at `results.enr.clarityelections.com/CO/<electionId>/...`. Boulder County's own results site links there: the 2024 General was `/CO/122598/`, the 2026 Primary `/CO/126592/`. The 2026 General ID is not published yet; it usually appears on election night.
- Clarity offers machine-readable reports on each election: `summary.zip` (CSV), `detailxml.zip` (XML, with county breakdowns on the state site), and JSON behind the web UI. The open-source `clarify` Python library (OpenNews) parses `detailxml`.
- **Access problem:** from this build container, Clarity's CloudFront returns **HTTP 403** to plain non-browser requests. I did **not** try to get around it (no faked browser User-Agent), because that would defeat a bot check. Whether the polling host (e.g. GitHub Actions runners) is allowed is **unverified**, and this is the main risk for the Colorado tab (see §6).
- Terms: Clarity pages are public government results. I found no published API terms. We will poll gently (≤ 1 request/min per file, using conditional GET via `current_ver.txt`) and attribute the source.
- **Calls:** the SOS never "calls" races. It reports unofficial counts until certification (late Nov/Dec). On this tab, `called_status` will be `not_called` until certification, then `certified`.

### 2.2 Boulder County Elections (county and local races)
- Live results: `https://electionresults.bouldercounty.gov/`. This is a static HTTrack mirror on S3/Cloudflare of the county's ASP.NET/Kendo app at `election.boco.solutions/ElectionResults2026G/`. It currently says _"Unofficial election results will be posted after 7:00 PM on November 3, 2026."_
- Format: **HTML only** (Kendo grids with the data inline). Each page has a "Last Updated" timestamp, a ballots-counted summary, and contest tables (candidate, votes, percent). Pages: `Home/IndexCategory/<n>.html`, `SelectContest.html`, `PrecinctTurnout.html`.
- **The county site shows countywide contest totals, not live precinct-by-precinct contest results.** Precinct-by-precinct results come in the **Statement of Votes** (PDF/Excel), published at certification, weeks later. Clarity may also carry Boulder precinct detail (`/CO/Boulder/...detailxml.zip`), but I could not verify that from here because of the 403.
- Real past pages (2026 Primary, 2024 General) are still online. They will be saved as **test fixtures** for the parser.
- No robots.txt (S3 returns 404). There are no stated scraping restrictions, but this is a public government site: we poll every ≥ 2 minutes, send an identifying User-Agent, and attribute the source.

### 2.3 National results
| Option | Cost | Update freq | Reliability | Race calls? | Licensing / notes |
|---|---|---|---|---|---|
| **AP Elections API** (`api.ap.org/v3/elections`) | Paid, by quote only, mainly sold to media and licensees (e.g. Kalshi 2026) | ~30–60 s | Industry standard | **Yes** (AP calls) | Needs `AP_API_KEY`. Same-day purchase is unrealistic unless you already have one. |
| **Decision Desk HQ API** | Paid, sales contact, no public price | Near-real-time | High | **Yes** | Contract required; DDHQ Votes website is free but has no public API. |
| **Democracy Works Elections API** | Request pricing | Unknown for live results | — | No | Mostly voter info, not tabulation. |
| **Official state feeds** (each state's SOS; many use Clarity, e.g. GA, KY, AR, WV, CO) | Free | 1–15 min | Varies a lot by state; one adapter per state format | **No** (counts only) | Legitimate, public. Large build effort: about one adapter per state; Clarity states share one parser. |
| News sites (NYT, CNN, Politico, 270toWin…) | — | — | — | — | **Excluded:** their terms forbid scraping and the data is licensed AP/DDHQ. |
| VoteHub / OpenElections / MIT EDSL | Free/low | Post-election or website-only | — | — | No live API. Not usable tonight. |

**Bottom line:** no free, legitimate, live, nationwide results API exists. Without an AP/DDHQ key, the National tab can only show (a) a curated set of key races from **official state feeds**, always marked `Not called`, and (b) link-out cards to official results pages.

### 2.4 Precinct-to-ZIP mapping for 80301
- **Boulder County GIS (official, CC BY 4.0):**
  - Precincts: `maps.bouldercounty.org/arcgis/rest/services/POLITICAL/PRECINCTS/MapServer/0` has **193 precincts**, fields `PRECINCT`, `DISTRICT`, and polygon geometry.
  - Address points: `.../PARCELS/ADDRESS_POINTS/MapServer/0` has 141,117 points, with **11,421 where `POSTAL_ZIP='80301'`**, plus lat/long.
- Method: point-in-polygon (shapely) assigns each 80301 address point to a precinct. That gives, per precinct, the **share of its addresses in 80301**. The precinct list (and weights) is generated **once, offline**, committed as `data/static/precincts_80301.json` with source and date, and re-checked before the election.
- USPS ZIPs are not polygons, so this is the most defensible approach: real 80301 mailing addresses from the county's own address file. It counts addresses, not voters, and the UI will say so.
- **Caveat:** usable 80301 *results* need precinct-level *results*. If none are published live (see §2.2), the 80301 section shows either precinct-level **turnout** (published live in `PrecinctTurnout.html`) or "precinct results not yet published," and switches to Statement-of-Votes data once that is released. It never fakes or estimates vote shares.

## 3. Architecture

```
            ┌──────────── GitHub Actions (poller job, loops every 2 min) ────────────┐
 sources →  │ fetch (timeout, retry+backoff, conditional GET) → parse → validate     │
 (CO SOS,   │ (pydantic schema) → normalize → merge-without-overwrite → write JSON   │
 Boulder,   │ if fetch/parse/validate fails: keep last good JSON, mark source stale  │
 AP/state)  └──────────────────────┬─────────────────────────────────────────────────┘
                                   │ commit data/*.json to `gh-pages` branch
                                   ▼
                    GitHub Pages (static HTML/CSS/vanilla JS, no build step)
                    browser fetches /data/*.json every 60 s + refresh button
```

- **Poller: Python 3.12.** Uses `httpx` (timeouts, HTTP/2), `tenacity` (retries with exponential backoff and jitter), `pydantic` (schema validation), `selectolax` or `lxml` (HTML), `clarify` (Clarity XML), and `shapely` (one-off precinct mapping only).
- **Scheduling:** GitHub's `schedule:` cron runs at most every 5 minutes and is often 10+ minutes late under election-night load. So one workflow run **loops internally** (fetch every 2 min, push changed JSON) for up to about 5h50m. A cron entry roughly every 5h starts a new run, guarded by `concurrency:` so only one runs at a time, and `workflow_dispatch` allows a manual restart.
- **Frontend:** a single `index.html` with plain JS modules and no framework (fast on phones, nothing to break at build time). Sunlight and dark themes come from CSS custom properties plus `prefers-color-scheme`, with a manual toggle.

## 4. Data model

```jsonc
// data/results.json
{
  "generated_at": "2026-11-04T03:12:00Z",
  "sources": {
    "co_sos":  {"name": "Colorado Secretary of State (Clarity ENR)", "url": "...", "status": "ok|stale|error|not_yet_available",
                "last_success_at": "...", "last_attempt_at": "...", "last_error": null, "source_updated_at": "..."},
    "boulder": {...}, "ap": {...}
  },
  "races": [ Race, ... ]
}
```

**Race** (one record per race *per source*; records from different sources are never merged):
| field | type | notes |
|---|---|---|
| `race_id` | str | stable key, e.g. `co-us-senate`, `boulder-county-commissioner-d2`. The same race from two sources shares a `race_id`. |
| `race_name` | str | as published by the source |
| `level` | enum | `national` \| `state` \| `county` \| `local` |
| `candidates[]` | list | `{name, party?, votes:int, percent:float, winner:bool}`. `winner` is true **only if the source flags it**. Ballot measures use Yes/No as candidates. |
| `votes` | int | total votes cast in the race |
| `percent` | — | per candidate (computed from votes when the source omits it, rounded to 1 dp, flagged `percent_computed`) |
| `precincts_reporting` | `{reporting:int, total:int} \| null` | null when the source doesn't report it. CO is all-mail, so "ballots counted" is shown instead when available. |
| `called_status` | enum | `not_called` \| `called` \| `certified` \| `runoff`. Set only from the source's own flags. |
| `source` | str | source key |
| `source_url` | str | human-viewable page for this race |
| `fetched_at` | ISO-8601 UTC | when **we** fetched it |
| `source_updated_at` | ISO-8601 \| null | the source's own "last updated" |
| `stale` | bool | true if the latest fetch failed and this is last-good data |
| `approximate` | bool + `approx_note` | used for 80301 aggregates |

**Disagreement handling:** when two sources report the same `race_id`, the card shows each source's numbers side by side with its own timestamp, and adds a "Sources differ" badge when totals or calls diverge. Nothing is merged silently.

## 5. UI
- **390px first.** One-column cards, ≥ 48px tap targets, system font, high-contrast "sunlight" palette (WCAG AA minimum, AAA for numbers), and dark mode.
- Tabs: a `<nav>` with `role="tablist"` and keyboard and screen-reader support. The last tab is remembered.
- Each card shows: race name, candidates sorted by votes (bar + number + %), `Not called` / `Called by AP` / `Certified` badge, ballots or precincts reporting, **Last updated** (source time and fetched time), **Source** link, and a STALE banner if applicable.
- Header: overall "Data as of …", a **Refresh** button, and auto-refresh every 60 s (paused when the tab is hidden). An `aria-live` region announces updates politely.
- Footer: data-source attribution and licenses (CO SOS, Boulder County Elections, Boulder County GIS CC BY 4.0, AP if used), plus an "Unofficial results" disclaimer.
- Before results exist, it shows "Results expected after 7:00 PM MT" from the source's status, not placeholder numbers.

## 6. Risks and mitigations
| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Clarity (CO SOS) 403s non-browser clients**, perhaps including GitHub runners | Colorado tab has no data | Test from a GitHub Actions runner in Milestone 1. If blocked, contact SOS (they offer media feeds) or use the user's AP key for CO races. We will **not** spoof browsers to get around a bot block. Fallback: link-out card with an honest "automated feed unavailable" message. |
| R2 | 2026G Clarity election ID unknown until tonight | Adapter can't start | ID is set by config/env var (`CO_CLARITY_ELECTION_ID`), with auto-discovery from Boulder's results page link. Runbook covers updating it. |
| R3 | No live precinct-level contest results for Boulder | 80301 aggregate impossible on election night | Show precinct turnout for 80301 precincts plus a clear notice. Add Clarity Boulder precinct detail if it exists. Add Statement of Votes ingestion when released. |
| R4 | Boulder HTML layout changes | Parser breaks | Strict validation, so a failure keeps last good data and marks it stale instead of showing garbage. Fixture tests. The runbook has a "fix parser fast" section. |
| R5 | GitHub Actions cron delays/outage | Data goes stale | Looping job, staleness shown in UI, manual `workflow_dispatch`, runbook. |
| R6 | No AP key | National tab can't show calls or broad coverage | Curated official-state-feed races marked `Not called` plus link-outs. Explained in UI. |
| R7 | Building tonight with ~23h to polls closing | Scope creep | Build order below. National state-feed adapters come last and are optional. |
| R8 | Commit churn on gh-pages (~30 commits/hr) | Repo bloat | Data on an orphan `gh-pages` branch, force-pushed as a single squashed commit each update, so `main` history stays clean. |

## 7. Build order and milestones (each ends with run + verify + commit)
1. **M0 — Skeleton (≈1h):** repo layout, `pyproject`, schema (pydantic), JSON writer with last-good/stale logic, CI test job. _Verify:_ unit tests pass.
2. **M1 — Source reachability probe (≈30m):** a GitHub Action that fetches each source URL and reports status. _Verify:_ confirms whether Clarity works from runners (R1) **before** we invest in that adapter.
3. **M2 — Boulder adapter (≈2h):** HTML parser + normalizer, tests against saved real 2026P/2024G pages. _Verify:_ parse fixtures, plus a live fetch of the current "not yet posted" page shows `not_yet_available`.
4. **M3 — CO SOS Clarity adapter (≈2h):** summary/detail parser, tests against saved real 2024G/2026P files (if reachable). _Verify:_ as M2.
5. **M4 — 80301 mapping (≈1h):** offline script builds `precincts_80301.json` from county GIS. Aggregator uses precinct results or turnout, labeled approximate. _Verify:_ map spot-check, tests.
6. **M5 — Frontend (≈3h):** tabs, cards, refresh, stale, dark/sunlight, a11y. _Verify:_ Playwright at 390×844, axe accessibility check, simulated outage.
7. **M6 — Poller workflow + Pages deploy (≈1h):** loop job, gh-pages publishing. _Verify:_ live URL updates end-to-end.
8. **M7 — National (≈1–3h, depends on answers):** AP adapter (if key), else curated Clarity-state races plus link-outs.
9. **M8 — Hardening + README + runbook (≈1h):** outage simulation, mobile test, docs.

Target: M0–M6 deployed before 5 PM MT Nov 3, so they can be checked against the live sources at 7 PM.

## 8. Repo layout (proposed)
```
poller/            # python package: sources/{boulder,co_sos,ap}.py, schema.py, normalize.py, store.py, run.py
poller/tests/      # pytest + fixtures/ (saved real responses, clearly labeled RECORDED)
scripts/build_80301_precincts.py
web/               # index.html, app.js, styles.css
data/static/       # precincts_80301.json (committed), races config
.github/workflows/ # test.yml, poll.yml, probe.yml
PLAN.md README.md
```
