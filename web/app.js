// Election Night 2026: static frontend. Reads data/results.json written by the poller.
// Never derives winners: shows a winner only if the source flags one.

const DATA_URL = "data/results.json";
const AUTO_REFRESH_MS = 60_000;          // re-read the published file every minute
const STALE_AFTER_MS = 15 * 60_000;      // whole file older than this → warning banner
const POLL_WORKFLOW_URL = "https://github.com/mgingrich69/python/actions/workflows/poll.yml";
const TOP_N = 4;                          // candidates shown before "Show all"

const SOURCE_TAB = { co_sos: "colorado", boulder: "boulder", ncsbe: "national", ga_sos: "national" };
const GEO_LABEL = { co: "Colorado", nc: "North Carolina", ga: "Georgia", "boulder-county": "Boulder County" };
const LEVEL_LABEL = { national: "Federal", state: "State", county: "County", local: "Local" };

let data = null;
let lastFetchOk = null;
let timer = null;
const state = {
  tab: load("tab", "national"),
  pins: new Set(JSON.parse(load("pins", "[]"))),
  filter: { national: "", colorado: "", boulder: "" },
  expanded: new Set(),
};

// ------------------------------------------------------------ utils
function load(k, d) { try { return localStorage.getItem("en26:" + k) ?? d; } catch { return d; } }
function save(k, v) { try { localStorage.setItem("en26:" + k, v); } catch { /* private mode */ } }

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // only used with static strings
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const nf = new Intl.NumberFormat("en-US");
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Denver", timeZoneName: "short" });
}
function ago(iso) {
  if (!iso) return "";
  const s = Math.round((Date.now() - new Date(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`;
  return `${Math.round(s / 86400)} days ago`;
}
function partyClass(c, isMeasure) {
  const n = (c.name || "").toLowerCase();
  if (isMeasure) return n.startsWith("yes") || n.startsWith("for") ? "yes" : n.startsWith("no") || n.startsWith("against") ? "no" : "";
  const p = (c.party || "").toLowerCase();
  if (p.startsWith("dem")) return "dem";
  if (p.startsWith("rep")) return "rep";
  return "";
}
function partyAbbr(p) {
  if (!p) return "";
  const m = { democratic: "Dem", republican: "Rep", libertarian: "Lib", green: "Grn", unaffiliated: "Una" };
  return m[p.toLowerCase()] || p;
}

// ------------------------------------------------------------ data
async function fetchData({ manual = false } = {}) {
  const btn = document.getElementById("refresh");
  btn.setAttribute("aria-busy", "true");
  try {
    const r = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (r.status === 404) throw new Error("No results have been published yet.");
    if (!r.ok) throw new Error(`Could not load results (HTTP ${r.status}).`);
    const j = await r.json();
    if (!j || !j.sources) throw new Error("Results file is malformed.");
    data = j;
    lastFetchOk = new Date();
    render();
    announce(manual ? "Results refreshed." : null);
  } catch (e) {
    showBanner(`${e.message} ${data ? "Showing the last results loaded." : ""} Retrying automatically.`);
    if (!data) renderEmpty(e.message);
  } finally {
    btn.removeAttribute("aria-busy");
  }
}

function announce(msg) {
  const asof = document.getElementById("asof");
  if (!data) return;
  const gen = data.generated_at;
  asof.textContent = `Data as of ${fmtTime(gen)} (${ago(gen)}) · updates every ${Math.round((data.poll_interval_seconds || 300) / 60)} min`;
  const age = Date.now() - new Date(gen);
  if (age > STALE_AFTER_MS) {
    showBanner(`Heads up: the published data is ${ago(gen).replace(" ago", "")} old. The poller may be delayed.`, true);
  } else {
    hideBanner();
  }
  if (msg) asof.setAttribute("aria-label", `${msg} ${asof.textContent}`);
}
function showBanner(text, withLink = false) {
  const b = document.getElementById("banner");
  b.replaceChildren(text, withLink ? h("span", {}, " ", h("a", { href: POLL_WORKFLOW_URL }, "Request an update")) : "");
  b.hidden = false;
}
function hideBanner() { document.getElementById("banner").hidden = true; }

function allRaces() {
  const out = [];
  for (const [key, block] of Object.entries(data.sources)) {
    for (const r of block.races || []) out.push({ ...r, _src: key });
  }
  return out;
}

// Group race records by race_id, keeping every source's version (never merged).
function groupByRace(races) {
  const m = new Map();
  for (const r of races) {
    if (!m.has(r.race_id)) m.set(r.race_id, []);
    m.get(r.race_id).push(r);
  }
  return [...m.entries()].map(([id, recs]) => ({ id, recs }));
}

function racesForTab(tab) {
  const races = allRaces();
  if (tab === "national") return races.filter(r => r.level === "national" && r.geography !== "boulder-county");
  if (tab === "colorado") return races.filter(r => r.geography === "co");
  return races.filter(r => r.geography === "boulder-county");
}

// ------------------------------------------------------------ rendering
function render() {
  for (const tab of ["national", "colorado", "boulder"]) {
    const panel = document.getElementById(`panel-${tab}`);
    panel.replaceChildren(...renderTab(tab));
  }
}

function renderEmpty(msg) {
  for (const tab of ["national", "colorado", "boulder"]) {
    document.getElementById(`panel-${tab}`).replaceChildren(
      h("div", { class: "notice" }, h("h2", {}, "No results yet"), h("p", {}, msg)),
      ...linksSection(tab, [])
    );
  }
}

function renderTab(tab) {
  const nodes = [];
  const groups = groupByRace(racesForTab(tab));
  const q = state.filter[tab].trim().toLowerCase();
  const visible = q ? groups.filter(g => g.recs[0].race_name.toLowerCase().includes(q)) : groups;

  if (tab === "national") nodes.push(nationalIntro());
  if (tab === "colorado") nodes.push(coloradoIntro());
  if (tab === "boulder") nodes.push(...zipSection());
  nodes.push(...sourceNotices(tab));

  const pinned = visible.filter(g => state.pins.has(g.id));
  if (pinned.length) {
    nodes.push(sectionHead("Pinned", pinned.length), h("div", { class: "grid" }, pinned.map(raceCard)));
  }

  if (groups.length > 6) {
    nodes.push(h("div", { class: "filter" },
      h("label", { class: "visually-hidden", for: `filter-${tab}` }, "Filter races"),
      h("input", {
        id: `filter-${tab}`, type: "search", placeholder: "Filter races (e.g. senate, boulder, 2C)", value: state.filter[tab],
        oninput: e => { state.filter[tab] = e.target.value; rerenderTab(tab, `filter-${tab}`); },
      })));
  }

  const rest = visible.filter(g => !state.pins.has(g.id));
  if (tab === "national") {
    for (const geo of uniq(rest.map(g => g.recs[0].geography))) {
      const gs = rest.filter(g => g.recs[0].geography === geo);
      nodes.push(sectionHead(GEO_LABEL[geo] || geo.toUpperCase(), gs.length), h("div", { class: "grid" }, gs.map(raceCard)));
    }
  } else {
    const order = tab === "boulder" ? ["national", "state", "county", "local"] : ["national", "state", "county", "local"];
    const title = { national: "Federal", state: tab === "boulder" ? "State (Boulder County votes)" : "Statewide & legislative", county: "County", local: "Local & ballot issues" };
    if (rest.length) nodes.push(sectionHead(tab === "boulder" ? "Boulder County results" : "Colorado results", rest.length));
    for (const lvl of order) {
      const gs = rest.filter(g => g.recs[0].level === lvl);
      if (!gs.length) continue;
      nodes.push(h("h3", { class: "sub-h" }, title[lvl]), h("div", { class: "grid" }, gs.map(raceCard)));
    }
  }
  if (tab === "colorado") nodes.push(...boulderStatewide(q));
  if (!groups.length && tab !== "colorado") nodes.push(h("p", { class: "empty" }, "No race results from these sources yet. See source status above and news links below."));
  else if (!visible.length) nodes.push(h("p", { class: "empty" }, "No races match that filter."));

  nodes.push(...linksSection(tab, data?.links || []));
  return nodes;
}

function rerenderTab(tab, focusId) {
  const panel = document.getElementById(`panel-${tab}`);
  const pos = focusId ? document.getElementById(focusId)?.selectionStart : null;
  panel.replaceChildren(...renderTab(tab));
  if (focusId) {
    const el = document.getElementById(focusId);
    if (el) { el.focus(); if (pos != null) el.setSelectionRange(pos, pos); }
  }
}

const uniq = a => [...new Set(a)];
function sectionHead(title, n) {
  return h("div", { class: "section-h" }, h("h2", {}, title), n != null ? h("span", { class: "count" }, `${n} race${n === 1 ? "" : "s"}`) : null);
}

function nationalIntro() {
  return h("details", { class: "notice" },
    h("summary", {}, "About these numbers (official counts, no race calls)"),
    h("p", {}, "National numbers come directly from official state election offices that publish free, machine-readable results. Right now that's North Carolina. Colorado's statewide feed blocks automated readers, so Colorado races appear only as Boulder County votes."),
    h("p", {}, "Official offices count votes but never call races. For calls and other states, use the news links at the bottom. Most outlets get their calls from the Associated Press, which this dashboard can't republish."));
}

function coloradoIntro() {
  const co = data?.sources?.co_sos;
  const live = co && co.status.status === "ok";
  if (live) return h("div", {});
  return h("div", { class: "notice warn" },
    h("h2", {}, "Statewide totals: automated feed unavailable"),
    h("p", {}, "The Colorado Secretary of State publishes results through Clarity, which blocks automated readers with a bot check. This dashboard doesn't get around that check. For statewide totals, open the official page or Colorado news links below."),
    h("p", {}, "Below are Boulder County's own official counts for statewide races and measures. These are one county's votes, not statewide totals."));
}

// Boulder County's votes on statewide contests, clearly labeled as one county only.
function boulderStatewide(q) {
  const recs = allRaces().filter(r => r.source === "boulder" && (r.level === "national" || r.level === "state")
    && !/district|regent/i.test(r.race_name) && (!q || r.race_name.toLowerCase().includes(q)));
  if (!recs.length) return [];
  const groups = groupByRace(recs).map(g => ({ ...g, recs: g.recs.map(r => ({ ...r, race_name: r.race_name + " (Boulder County votes only)" })) }));
  return [sectionHead("Statewide contests: Boulder County votes only", groups.length), h("div", { class: "grid" }, groups.map(raceCard))];
}

function sourceNotices(tab) {
  if (!data) return [];
  const items = Object.entries(data.sources).filter(([k]) => SOURCE_TAB[k] === tab || (tab === "boulder" && k === "co_sos"));
  if (!items.length) return [];
  const list = h("ul", { class: "sources", "aria-label": "Source status" }, items.map(([k, b]) => {
    const s = b.status;
    const label = { ok: "Live", stale: "Stale", error: "Error", not_yet_available: "Waiting", not_configured: "Not set up" }[s.status] || s.status;
    return h("li", { class: "src" },
      h("span", { class: `st ${s.status}` }, label),
      h("a", { href: s.url }, s.name),
      h("div", {}, s.status === "ok" || s.status === "stale"
        ? `Last good fetch ${fmtTime(s.last_success_at)} · source updated ${fmtTime(s.source_updated_at)}`
        : (s.message || "")),
      s.last_error ? h("div", { class: "err" }, `Latest attempt ${fmtTime(s.last_attempt_at)} failed: ${s.last_error}`) : null);
  }));
  return [h("details", { class: "src-details", open: items.some(([, b]) => b.status.status !== "ok") ? true : null },
    h("summary", {}, "Source status"), list)];
}

function raceCard(group) {
  const recs = group.recs;
  const first = recs[0];
  const pinned = state.pins.has(group.id);
  const anyStale = recs.some(r => r.stale);
  const differ = recs.length > 1 && disagree(recs);

  const badges = h("div", { class: "badges" },
    statusBadge(first),
    anyStale ? h("span", { class: "badge stale" }, "Stale data") : null,
    differ ? h("span", { class: "badge differ" }, "Sources differ") : null,
    first.approximate ? h("span", { class: "badge approx" }, "Approximate") : null);

  const body = recs.length > 1
    ? h("div", { class: "compare" }, recs.map(r => h("div", { class: "src-block" },
        h("p", { class: "src-name" }, r.source_name), candList(r, group.id + r.source), meta(r))))
    : [candList(first, group.id), meta(first)];

  return h("article", { class: `card${anyStale ? " is-stale" : ""}`, "aria-labelledby": `t-${cssId(group.id)}` },
    h("div", { class: "card-head" },
      h("h3", { class: "card-title", id: `t-${cssId(group.id)}` }, first.race_name),
      h("button", {
        class: "pin", type: "button", "aria-pressed": String(pinned),
        "aria-label": `${pinned ? "Unpin" : "Pin"} ${first.race_name}`,
        onclick: () => togglePin(group.id),
        html: pinned
          ? '<svg aria-hidden="true" viewBox="0 0 24 24" width="24" height="24"><path fill="currentColor" d="M12 2l3 6.9 7.5.6-5.7 4.9 1.8 7.3L12 17.8 5.4 21.7l1.8-7.3L1.5 9.5 9 8.9z"/></svg>'
          : '<svg aria-hidden="true" viewBox="0 0 24 24" width="24" height="24"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 3.5l2.4 5.6 6.1.5-4.6 4 1.4 5.9L12 16.4l-5.3 3.1 1.4-5.9-4.6-4 6.1-.5z"/></svg>',
      })),
    badges, body);
}

const cssId = s => s.replace(/[^a-z0-9]/gi, "-");

function statusBadge(r) {
  if (r.called_status === "certified") return h("span", { class: "badge certified" }, "Certified");
  if (r.called_status === "called") return h("span", { class: "badge called" }, `Called${r.called_by ? " by " + r.called_by : ""}`);
  if (r.called_status === "runoff") return h("span", { class: "badge called" }, "Runoff");
  return h("span", { class: "badge not-called" }, "Not called");
}

function disagree(recs) {
  const key = r => JSON.stringify([r.called_status, r.candidates.map(c => [c.name.toLowerCase(), c.votes]).sort()]);
  return new Set(recs.map(key)).size > 1;
}

function candList(r, expandKey) {
  const sorted = [...r.candidates].sort((a, b) => b.votes - a.votes);
  const showAll = state.expanded.has(expandKey) || sorted.length <= TOP_N + 1;
  const shown = showAll ? sorted : sorted.slice(0, TOP_N);
  const nodes = [];
  if (r.precincts_reporting && r.precincts_reporting.total) {
    const p = r.precincts_reporting;
    nodes.push(h("p", { class: "reporting" }, `${nf.format(p.reporting)} of ${nf.format(p.total)} precincts reporting (${Math.round(100 * p.reporting / p.total)}%)`));
  } else if (r.ballots_counted) {
    nodes.push(h("p", { class: "reporting" }, `${nf.format(r.ballots_counted)} ballots counted countywide`));
  }
  nodes.push(h("ul", { class: "cands" }, shown.map(c => {
    const pct = c.percent ?? 0;
    const cls = partyClass(c, r.is_ballot_measure);
    return h("li", { class: `cand ${cls}` },
      h("span", { class: "chip", "aria-hidden": "true" }),
      h("span", { class: "name" },
        c.winner ? h("span", { class: "winner-mark", "aria-label": "Winner, as reported by source" }, "✓ ") : null,
        c.name, c.party ? h("span", { class: "party" }, ` · ${partyAbbr(c.party)}`) : null),
      h("span", { class: "pct" }, `${pct.toFixed(1)}%`),
      h("span", { class: "bar", role: "img", "aria-label": `${c.name}: ${pct.toFixed(1)} percent, ${nf.format(c.votes)} votes` },
        h("span", { style: `width:${Math.min(100, Math.max(0, pct))}%` })),
      h("span", { class: "votes" }, nf.format(c.votes)));
  })));
  if (!showAll) {
    nodes.push(h("button", { class: "more", type: "button", onclick: () => { state.expanded.add(expandKey); rerenderTab(state.tab); } },
      `Show all ${sorted.length} candidates`));
  }
  return nodes;
}

function meta(r) {
  return h("div", { class: "meta" },
    h("span", {}, `Last updated: ${fmtTime(r.source_updated_at || r.fetched_at)}${r.source_updated_at ? " (source)" : ""} · fetched ${fmtTime(r.fetched_at)}`),
    h("span", {}, "Source: ", h("a", { href: r.source_url }, r.source_name)),
    r.percent_computed ? h("span", {}, "Percentages calculated from the source's vote counts.") : null,
    r.stale ? h("span", {}, "⚠ Latest fetch failed; showing last good data.") : null,
    r.approx_note ? h("span", {}, r.approx_note) : null);
}

function zipSection() {
  const z = data?.zip_80301;
  if (!z) return [];
  const t = z.turnout;
  const hero = h("div", { class: "zip-hero" },
    h("h3", {}, `ZIP ${z.zip}`),
    h("div", { class: "badges" }, h("span", { class: "badge approx" }, "Approximate"), t?.stale ? h("span", { class: "badge stale" }, "Stale data") : null),
    h("p", {}, h("strong", {}, z.label)),
    t ? h("div", { class: "stat-row" },
          h("div", { class: "stat" }, h("b", {}, nf.format(t.core.ballots)), h("small", {}, "ballots processed")),
          h("div", { class: "stat" }, h("b", {}, t.core.turnout_pct != null ? `${t.core.turnout_pct}%` : "—"), h("small", {}, "turnout")),
          h("div", { class: "stat" }, h("b", {}, t.core.precincts), h("small", {}, "core precincts")))
      : h("p", {}, "Precinct turnout hasn't been published yet. Boulder County posts it with results after 7:00 PM MT."),
    t ? h("p", { class: "reporting" }, `Core precincts are those with at least half their addresses in ${z.zip}. All ${t.all_overlapping.precincts} overlapping precincts: ${nf.format(t.all_overlapping.ballots)} ballots of ${nf.format(t.all_overlapping.registered)} active voters.`) : null,
    h("div", { class: "notice warn" }, h("p", {}, z.precinct_results_note)),
    precinctTable(z),
    h("div", { class: "meta" },
      t ? h("span", {}, "Turnout source: ", h("a", { href: t.source_url }, "Boulder County Elections – Voter Turnout by Precinct"), ` · fetched ${fmtTime(t.fetched_at)}`) : null,
      h("span", {}, `Precinct mapping: ${z.method}`),
      h("span", {}, "Mapping data: ", z.mapping_sources.map((s, i) => [i ? ", " : "", h("a", { href: s.url }, s.name), ` (${s.license})`]))));
  return [sectionHead(`ZIP ${z.zip} (approximate)`, null), hero];
}

function precinctTable(z) {
  const rows = z.precincts;
  return h("details", {},
    h("summary", {}, `Precincts overlapping ${z.zip} (${rows.length})`),
    h("table", {},
      h("caption", {}, `Share of each precinct's addresses that are in ${z.zip}. Turnout is from Boulder County Elections.`),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Precinct"), h("th", { scope: "col" }, "In ZIP"), h("th", { scope: "col" }, "Ballots"), h("th", { scope: "col" }, "Turnout"))),
      h("tbody", {}, rows.map(p => h("tr", {},
        h("td", {}, `${p.short_code}${p.core ? "" : " (fringe)"}`),
        h("td", {}, `${Math.round(100 * (p.share_in_zip || 0))}%`),
        h("td", {}, p.ballots != null ? nf.format(p.ballots) : "—"),
        h("td", {}, p.ballots != null && p.registered ? `${(100 * p.ballots / p.registered).toFixed(1)}%` : "—"))))));
}

function linksSection(tab, links) {
  const mine = links.filter(l => l.tab === tab);
  if (!mine.length) return [];
  const groups = uniq(mine.map(l => l.group));
  return [sectionHead(tab === "national" ? "Race calls & more results" : "More coverage", null),
    h("ul", { class: "links" }, groups.map(g => [
      h("li", { class: "group" }, g),
      mine.filter(l => l.group === g).map(l => h("li", {}, h("a", { href: l.url, rel: "noopener", target: "_blank" }, l.name))),
    ]))];
}

function togglePin(id) {
  state.pins.has(id) ? state.pins.delete(id) : state.pins.add(id);
  save("pins", JSON.stringify([...state.pins]));
  rerenderTab(state.tab);
  const btn = [...document.querySelectorAll(`#panel-${state.tab} .pin`)].find(b => b.closest("article")?.querySelector(`#t-${cssId(id)}`));
  btn?.focus();
}

// ------------------------------------------------------------ tabs, theme, refresh
function selectTab(tab, focus = false) {
  state.tab = tab;
  save("tab", tab);
  for (const btn of document.querySelectorAll('[role="tab"]')) {
    const on = btn.dataset.tab === tab;
    btn.setAttribute("aria-selected", String(on));
    btn.tabIndex = on ? 0 : -1;
    if (on && focus) btn.focus();
    document.getElementById(btn.getAttribute("aria-controls")).hidden = !on;
  }
}

function initTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  tabs.forEach((btn, i) => {
    btn.addEventListener("click", () => { selectTab(btn.dataset.tab); window.scrollTo({ top: 0 }); });
    btn.addEventListener("keydown", e => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (e.key === "Home") selectTab(tabs[0].dataset.tab, true);
      else if (e.key === "End") selectTab(tabs.at(-1).dataset.tab, true);
      else if (d) selectTab(tabs[(i + d + tabs.length) % tabs.length].dataset.tab, true);
    });
  });
  selectTab(["national", "colorado", "boulder"].includes(state.tab) ? state.tab : "national");
}

function initTheme() {
  const btn = document.getElementById("theme");
  const modes = ["auto", "light", "dark"];
  let mode = load("theme", "auto");
  const apply = () => {
    if (mode === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    btn.setAttribute("aria-label", `Color theme: ${mode === "auto" ? "automatic" : mode}. Change theme`);
  };
  btn.addEventListener("click", () => { mode = modes[(modes.indexOf(mode) + 1) % modes.length]; save("theme", mode); apply(); });
  apply();
}

function schedule() {
  clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) fetchData(); }, AUTO_REFRESH_MS);
}

document.getElementById("refresh").addEventListener("click", () => fetchData({ manual: true }));
document.addEventListener("visibilitychange", () => { if (!document.hidden) fetchData(); });
initTabs();
initTheme();
fetchData();
schedule();
setInterval(() => { if (data) announce(); }, 30_000); // keep "x min ago" honest
