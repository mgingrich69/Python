// Election Night 2026: static frontend. Reads data/results.json written by the poller.
// Never derives winners: a winner is shown only if the source itself flags one.

const DATA_URL = "data/results.json";
const AUTO_REFRESH_MS = 60_000;
const STALE_AFTER_MS = 15 * 60_000;
const POLL_WORKFLOW_URL = "https://github.com/mgingrich69/python/actions/workflows/poll.yml";
const TOP_N = 5;

// Poll closing times (all polls in the state), UTC.
const CLOSINGS = [
  { st: "NC", name: "North Carolina", at: "2026-11-04T00:30:00Z" },
  { st: "CO", name: "Colorado", at: "2026-11-04T02:00:00Z" },
  { st: "MN", name: "Minnesota", at: "2026-11-04T02:00:00Z" },
];
const CO_CLOSE = "2026-11-04T02:00:00Z";
const GEO_LABEL = { nc: "North Carolina", mn: "Minnesota", co: "Colorado", ga: "Georgia", "boulder-county": "Boulder County" };
const SOURCE_TABS = { wikipedia: ["national"], ncsbe: ["national"], mnsos: ["national"], ga_sos: ["national"], co_sos: ["colorado"], boulder: ["boulder", "colorado"] };
// Not shown anywhere (per user preference): judicial retention, state legislature, RTD director.
const EXCLUDE_RE = /\b(retain|justice|judge|court|regents?)\b|^state (senate|house)\b|\bstate senator\b|\bstate representative\b|^rtd\b|regional transportation district director/i;
// Regional measures on 80301 ballots that are kept on the Boulder tab despite not naming Boulder.
const REGIONAL_RE = /front range passenger rail|^ballot issue 7a$/i;
// Single-candidate (uncontested) races are hidden; write-in lines don't count as opponents.
const uncontested = r => !r.is_ballot_measure && r.candidates.filter(c => !/^write-?in/i.test(c.name)).length <= 1;
const KEY_RE = /^(U\.S\. Senate|Governor)\b/;
const isKey = (g, tab) => !g.r.is_ballot_measure && (KEY_RE.test(shortName(g.r)) || (tab === "boulder" && /city of boulder mayor/i.test(g.r.race_name)));

let data = null;
const state = {
  tab: load("tab", "national"),
  pins: new Set(safeJSON(load("pins", "[]"), [])),
  filter: { national: "", colorado: "", boulder: "" },
  expanded: new Set(),
  open: new Set(),
};

// ------------------------------------------------------------ utils
function load(k, d) { try { return localStorage.getItem("en26:" + k) ?? d; } catch { return d; } }
function save(k, v) { try { localStorage.setItem("en26:" + k, v); } catch { /* storage unavailable */ } }
function safeJSON(s, d) { try { return JSON.parse(s); } catch { return d; } }

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "svg") el.innerHTML = v; // static icon markup only
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const nf = new Intl.NumberFormat("en-US");
const uniq = a => [...new Set(a)];
const cssId = s => s.replace(/[^a-z0-9]/gi, "-");
function fmtTime(iso, withDay = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("en-US", { ...(withDay ? { month: "short", day: "numeric" } : {}), hour: "numeric", minute: "2-digit", timeZone: "America/Denver", timeZoneName: "short" });
}
function ago(iso) {
  if (!iso) return "";
  const s = Math.round((Date.now() - new Date(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`;
  return `${Math.round(s / 86400)} days ago`;
}
function partyKey(c, isMeasure) {
  const n = (c.name || "").toLowerCase();
  if (isMeasure) return /^(yes|for)/.test(n) ? "yes" : /^(no|against)/.test(n) ? "no" : "";
  const p = (c.party || "").toLowerCase();
  if (p.startsWith("dem")) return "dem";
  if (p.startsWith("rep")) return "rep";
  return "";
}
function partyAbbr(p) {
  if (!p) return "";
  const m = { democratic: "DEM", "democratic-farmer-labor": "DFL", republican: "REP", libertarian: "LIB", green: "GRN", unaffiliated: "UNA", independent: "IND" };
  return m[p.toLowerCase()] || p.slice(0, 3).toUpperCase();
}
function shortName(r) {
  let n = r.race_name.replace(/^(North Carolina|Minnesota|Georgia) – /, "");
  n = n.replace(/^Us House Of Representatives District (\d+)/i, "U.S. House · District $1")
       .replace(/^U\.S\. Representative District (\d+)/i, "U.S. House · District $1")
       .replace(/^Representative to the \d+\w* United States Congress - District (\d+)/i, "U.S. House · District $1")
       .replace(/^(Us Senate|U\.S\. Senator|United States Senator)/i, "U.S. Senate")
       .replace(/^Governor & Lt Governor/i, "Governor")
       .replace(/^Governor\s*\/\s*Lieutenant Governor/i, "Governor");
  return n.replace(/\s+-\s+(Democratic|Republican|Libertarian|Unity) Party$/i, " ($1 primary)");
}
const awaiting = r => r.votes === 0;
function sortCands(r) {
  const c = [...r.candidates];
  if (awaiting(r)) {
    const rank = x => ({ dem: 0, rep: 1 }[partyKey(x, false)] ?? (x.name === "Write-in" ? 3 : 2));
    return c.sort((a, b) => rank(a) - rank(b));
  }
  return c.sort((a, b) => b.votes - a.votes);
}

// ------------------------------------------------------------ data
async function fetchData({ manual = false } = {}) {
  const btn = document.getElementById("refresh");
  btn.setAttribute("aria-busy", "true");
  try {
    const r = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (r.status === 404) throw new Error("No results have been published yet.");
    if (!r.ok) throw new Error(`Couldn't load results (HTTP ${r.status}).`);
    const j = await r.json();
    if (!j || !j.sources) throw new Error("The results file is malformed.");
    data = j;
    render();
    updateAsOf(manual);
  } catch (e) {
    showBanner(`${e.message} ${data ? "Showing the last results loaded." : ""} Retrying automatically.`);
    if (!data) renderEmpty(e.message);
  } finally {
    btn.removeAttribute("aria-busy");
  }
}

function updateAsOf(manual) {
  if (!data) return;
  const gen = data.generated_at;
  const el = document.getElementById("asof");
  el.replaceChildren("Updated ", h("b", {}, fmtTime(gen, false)), ` · ${ago(gen)} · checks every ${Math.round((data.poll_interval_seconds || 300) / 60)} min`);
  if (manual) el.setAttribute("aria-label", `Results refreshed. Updated ${fmtTime(gen)}.`);
  if (Date.now() - new Date(gen) > STALE_AFTER_MS) {
    showBanner(`Heads up: the published data is ${ago(gen).replace(" ago", "")} old. The poller may be delayed.`, true);
  } else hideBanner();
}
function showBanner(text, withLink = false) {
  const b = document.getElementById("banner");
  b.replaceChildren(text, withLink ? h("span", {}, " ", h("a", { href: POLL_WORKFLOW_URL }, "Request an update")) : "");
  b.hidden = false;
}
function hideBanner() { document.getElementById("banner").hidden = true; }

function summaryFor(r) {
  if (r.summary) return r.summary;
  const b = data?.ballot;
  if (!b) return null;
  if (!summaryFor.map || summaryFor.src !== b) {
    summaryFor.src = b;
    summaryFor.map = new Map([...(b.boulder || []).map(c => [c.race_id, c.summary]), ...(b.colorado || []).filter(c => c.boulder_race_id).map(c => [c.boulder_race_id, c.summary])]);
  }
  const hit = summaryFor.map.get(r.race_id);
  if (hit) return hit;
  const code = r.race_id.match(/ballot-(issue|question)-[0-9a-z]+$/)?.[0];
  if (!code) return null;
  const cands = [...summaryFor.map.entries()].filter(([k]) => k.endsWith(code) && k.startsWith(r.race_id.split(":")[0]));
  return cands.length === 1 ? cands[0][1] : null;
}
function allRaces() {
  const out = [];
  for (const [key, block] of Object.entries(data.sources)) for (const r of block.races || []) out.push({ ...r, _src: key });
  return out;
}
function groupByRace(races) {
  const m = new Map();
  for (const r of races) { if (!m.has(r.race_id)) m.set(r.race_id, []); m.get(r.race_id).push(r); }
  return [...m.entries()].map(([id, recs]) => ({ id, recs, r: recs[0] }));
}
function racesForTab(tab) {
  const races = allRaces();
  if (tab === "national") return []; // national view = balance of power + watch list (no single-state detail)
  if (tab === "colorado") return races.filter(r => r.geography === "co");
  return races.filter(r => r.geography === "boulder-county");
}

// ------------------------------------------------------------ render
function render() {
  for (const tab of ["national", "colorado", "boulder"]) {
    const panel = document.getElementById(`panel-${tab}`);
    const focusId = panel.contains(document.activeElement) ? document.activeElement.id : null;
    panel.replaceChildren(...renderTab(tab));
    if (focusId) document.getElementById(focusId)?.focus();
  }
}

function renderEmpty(msg) {
  for (const tab of ["national", "colorado", "boulder"]) {
    document.getElementById(`panel-${tab}`).replaceChildren(
      strip(h("p", {}, h("strong", {}, "No results yet. "), msg)), ...linksSection(tab, []));
  }
}

function renderTab(tab) {
  const nodes = [];
  let groups = groupByRace(racesForTab(tab));
  if (tab === "colorado") groups = coloradoGroups(groups);
  if (tab === "boulder" && !groups.some(g => g.r.source === "boulder")) groups = groups.concat(ballotGroups("boulder"));
  groups = groups.filter(g => !EXCLUDE_RE.test(g.r.race_name) && !uncontested(g.r));
  // Statewide and federal contests live on the Colorado tab only; Boulder shows City of Boulder
  // and Boulder County contests (not other towns or districts).
  if (tab === "boulder") groups = groups.filter(g => (g.r.level === "county" || g.r.level === "local") && (/\bBoulder\b/.test(g.r.race_name) || REGIONAL_RE.test(g.r.race_name)));
  const q = state.filter[tab].trim().toLowerCase();
  const match = g => !q || g.r.race_name.toLowerCase().includes(q) || shortName(g.r).toLowerCase().includes(q);
  const visible = groups.filter(match);

  if (tab === "national") nodes.push(...balanceOfPower(), ...senateBoard());
  if (tab === "colorado") nodes.push(coloradoStrip());
  if (tab === "boulder") nodes.push(...zipSection(), boulderStrip());

  const pinned = visible.filter(g => state.pins.has(g.id));
  const keys = visible.filter(g => !state.pins.has(g.id) && isKey(g, tab));
  if (pinned.length) nodes.push(sectionHead("Pinned", pinned.length), h("div", { class: "keys" }, pinned.map(keyCard)));
  if (keys.length) nodes.push(sectionHead(tab === "boulder" ? "Top of the ballot" : "Key races", keys.length), h("div", { class: "keys" }, keys.map(keyCard)));

  if (groups.length > 8 && tab !== "national") {
    nodes.push(h("div", { class: "filter" },
      h("label", { class: "visually-hidden", for: `filter-${tab}` }, "Filter races"),
      h("input", { id: `filter-${tab}`, type: "search", placeholder: "Find a race (e.g. District 2, 2C, sheriff)", value: state.filter[tab], autocomplete: "off",
        oninput: e => { state.filter[tab] = e.target.value; rerenderKeepFocus(tab, e.target); } })));
  }

  const rest = visible.filter(g => !state.pins.has(g.id) && !keys.includes(g));
  for (const [title, gs] of sectionize(tab, rest)) {
    if (gs.length > 10 && !q && !/measure/i.test(title)) {
      const key = `sec:${tab}:${title}`;
      nodes.push(h("details", { class: "bigsec", open: state.open.has(key) || null, ontoggle: e => toggleOpen(key, e) },
        h("summary", {}, h("span", { class: "bs-title" }, title), h("span", { class: "count" }, `${gs.length} races`), h("span", { class: "chev", "aria-hidden": "true" }, "›")),
        h("ul", { class: "rows" }, gs.map(raceRow))));
    } else nodes.push(sectionHead(title, gs.length), h("ul", { class: "rows" }, gs.map(raceRow)));
  }

  if (!groups.length) nodes.push(h("p", { class: "empty" }, tab === "boulder"
    ? "Boulder County posts results after 7:00 PM MT. Races appear here automatically."
    : "No race data from these sources yet."));
  else if (!visible.length) nodes.push(h("p", { class: "empty" }, "No races match that search."));

  nodes.push(...linksSection(tab, data?.links || []), ...sourcesSection(tab));
  return nodes;
}

function rerenderKeepFocus(tab, input) {
  const pos = input.selectionStart;
  document.getElementById(`panel-${tab}`).replaceChildren(...renderTab(tab));
  const el = document.getElementById(`filter-${tab}`);
  if (el) { el.focus(); el.setSelectionRange(pos, pos); }
}

function sectionize(tab, groups) {
  const out = [];
  if (tab === "national") {
    for (const geo of uniq(groups.map(g => g.r.geography))) out.push([`${GEO_LABEL[geo] || geo.toUpperCase()} · House`, groups.filter(g => g.r.geography === geo)]);
    return out;
  }
  if (tab === "colorado") {
    const n = g => g.r.race_name;
    const cb = [
      ["Federal", g => /^U\.S\./.test(n(g))],
      ["Statewide offices", g => /^(Governor|Secretary of State|State Treasurer|Attorney General)/.test(n(g))],
      ["Statewide ballot measures", g => /^(Amendment|Proposition) /.test(n(g))],
      ["Judicial retention (statewide)", g => /^Retain /.test(n(g))],
      ["State Board of Education", g => /^State Board of Education/.test(n(g))],
      ["State Senate", g => /^State Senate/.test(n(g))],
      ["State House", g => /^State House/.test(n(g))],
      ["RTD Board", g => /^RTD/.test(n(g))],
    ];
    const used = new Set();
    for (const [title, f] of cb) { const gs = groups.filter(g => !used.has(g) && f(g)); gs.forEach(g => used.add(g)); if (gs.length) out.push([title, gs]); }
    const other = groups.filter(g => !used.has(g));
    if (other.length) out.push(["Other", other]);
    return out;
  }
  if (tab === "boulder") {
    const isM = g => g.r.is_ballot_measure, n = g => g.r.race_name;
    const bb = [
      ["City of Boulder races", g => !isM(g) && /^City of Boulder/.test(n(g))],
      ["City of Boulder ballot measures", g => isM(g) && /^City of Boulder/.test(n(g))],
      ["Boulder County ballot measures", g => isM(g) && /Boulder County/.test(n(g))],
      ["Boulder County races", g => !isM(g) && /Boulder County/.test(n(g))],
    ];
    const used = new Set();
    for (const [title, f] of bb) { const gs = groups.filter(g => !used.has(g) && f(g)); gs.forEach(g => used.add(g)); if (gs.length) out.push([title, gs]); }
    const other = groups.filter(g => !used.has(g));
    if (other.length) out.push([other.every(g => g.r.is_ballot_measure) ? "Regional ballot measures" : "Other Boulder contests", other]);
    return out;
  }
  const buckets = [
    ["Federal", g => g.r.level === "national"],
    ["Statewide measures & judicial retention", g => g.r.level === "state" && g.r.is_ballot_measure],
    ["State offices & legislature", g => g.r.level === "state" && !g.r.is_ballot_measure],
    ["County", g => g.r.level === "county"],
    ["Local ballot issues", g => g.r.level === "local" && g.r.is_ballot_measure],
    ["Local offices", g => g.r.level === "local" && !g.r.is_ballot_measure],
  ];
  for (const [title, f] of buckets) { const gs = groups.filter(f); if (gs.length) out.push([title, gs]); }
  return out;
}

const sectionHead = (title, n) => h("div", { class: "section-h" }, h("h2", {}, title), n != null ? h("span", { class: "count" }, `${n} race${n === 1 ? "" : "s"}`) : null);
const strip = (p, warn = false) => h("div", { class: `strip${warn ? " warn" : ""}` }, h("span", { class: "ico", "aria-hidden": "true" }, warn ? "!" : "i"), p);

function closingStrip() {
  const now = Date.now();
  return h("ul", { class: "closings", "aria-label": "Poll closing times" }, CLOSINGS.map(c => {
    const closed = now >= new Date(c.at);
    return h("li", { class: "closing" },
      h("span", { class: `st${closed ? " closed" : ""}` }, closed ? "Polls closed" : "Polls close"),
      h("b", {}, c.name), h("small", {}, fmtTime(c.at, false)));
  }));
}

function coloradoStrip() {
  const co = data?.sources?.co_sos;
  if (co && co.status.status === "ok") return h("div", {});
  return strip(h("p", {}, h("strong", {}, "No statewide totals. "),
    "The Secretary of State's results site blocks automated readers, and this dashboard doesn't get around that. Below is the full statewide ballot from the official candidate list. Once Boulder County posts results, contests on Boulder ballots show ",
    h("strong", {}, "Boulder County's votes"), " (one county, not statewide). For statewide numbers, use the links below."), true);
}
function boulderStrip() {
  const b = data?.sources?.boulder;
  if (!b) return h("div", {});
  const s = b.status;
  if (s.status === "not_yet_available") return strip(h("p", {}, h("strong", {}, "Waiting on Boulder County. "), s.message || ""));
  if (s.status === "ok" && b.extras?.results_label) return strip(h("p", {}, h("strong", {}, `${b.extras.results_label}. `), b.extras.ballots_counted != null ? `${nf.format(b.extras.ballots_counted)} ballots counted countywide.` : ""));
  return h("div", {});
}

// ---------- ballot lineups (official candidate lists; no votes)
function ballotRace(c, geo, src) {
  return {
    race_id: c.race_id || `${geo}:ballot:${c.contest.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
    race_name: c.contest, level: c.level, geography: geo, is_ballot_measure: !!c.is_ballot_measure,
    candidates: c.candidates.map(x => ({ name: x.name, party: x.party, votes: 0, percent: null, winner: false })),
    votes: 0, called_status: "not_called", ballot: true, summary: c.summary || null,
    source: "ballot", source_name: src?.name || "Official ballot", source_url: src?.url || "#",
    fetched_at: data?.ballot?.generated_at, source_updated_at: null,
  };
}
function ballotGroups(which) {
  const b = data?.ballot;
  if (!b) return [];
  const geo = which === "boulder" ? "boulder-county" : "co";
  const src = (b.sources?.[which] || [])[0];
  return b[which].map(c => { const r = ballotRace(c, geo, src); return { id: r.race_id, recs: [r], r, ballot: true }; });
}
// Colorado: the statewide ballot. Where Boulder County has posted votes for a contest, show those
// (clearly labeled as one county) instead of the bare lineup. Statewide totals are not available.
function coloradoGroups(liveCo) {
  const b = data?.ballot;
  if (!b) return liveCo;
  const boulderRaces = new Map(allRaces().filter(r => r.source === "boulder").map(r => [r.race_id, r]));
  const src = (b.sources?.colorado || [])[0];
  return b.colorado.map(c => {
    const live = c.boulder_race_id && boulderRaces.get(c.boulder_race_id);
    if (live) {
      const r = { ...live, race_name: c.contest, geography: "co", summary: c.summary || null };
      return { id: "co:" + live.race_id, recs: [r], r, boulderOnly: true };
    }
    const r = ballotRace(c, "co", src);
    return { id: r.race_id, recs: [r], r, ballot: true };
  });
}

// ---------- Balance of power (Wikipedia infobox) + trend
const PCLASS = p => /^dem/i.test(p) ? "dem" : /^rep/i.test(p) ? "rep" : "oth";
function balanceOfPower() {
  const w = data?.sources?.wikipedia;
  const ch = w?.extras?.chambers;
  if (!ch) {
    return [sectionHead("Balance of power", null), strip(h("p", {}, h("strong", {}, "Seat totals unavailable. "),
      w?.status?.last_error ? `Latest check failed: ${w.status.last_error}` : "Waiting for the first check."), true)];
  }
  const stale = w.status.status === "stale";
  const cards = ["senate", "house"].map(c => chamberCard(ch[c], w.extras.history || [], stale)).filter(Boolean);
  return [
    sectionHead("Balance of power", null),
    h("div", { class: "bop" }, cards),
    h("p", { class: "note" }, "Seat counts as recorded on Wikipedia, which editors update as AP and other outlets call races. Community-edited: may lag, change, or contain errors. ",
      ...["senate", "house"].map((c, i) => [i ? " · " : "", h("a", { href: ch[c].revision_url }, `${c === "senate" ? "Senate" : "House"} revision ${fmtTime(ch[c].revision_time)}`)]),
      " · CC BY-SA 4.0"),
  ];
}
function chamberCard(c, history, stale) {
  if (!c) return null;
  const name = c.chamber === "senate" ? "Senate" : "House";
  const order = [...c.parties].sort((a, b) => (PCLASS(a.party) === "dem" ? -1 : PCLASS(b.party) === "dem" ? 1 : PCLASS(a.party) === "rep" ? 1 : -1));
  const reported = c.results_reported;
  const val = p => reported ? p.seats_after : p.seats_before;
  const filled = order.reduce((n, p) => n + (val(p) || 0), 0);
  const open = Math.max(0, c.total_seats - filled);
  const dem = order.filter(p => PCLASS(p.party) === "dem"), rep = order.filter(p => PCLASS(p.party) === "rep"), oth = order.filter(p => PCLASS(p.party) === "oth");
  const segs = [...dem, ...oth, { party: "open", n: open }, ...rep];
  const bar = h("div", { class: "seatbar", role: "img", "aria-label": `${name}: ` + order.map(p => `${p.party} ${val(p) ?? "unknown"}`).join(", ") + (open ? `, ${open} not yet decided or vacant` : "") },
    segs.map(p => {
      const n = p.party === "open" ? p.n : (val(p) || 0);
      if (!n) return null;
      return h("span", { class: `sb ${p.party === "open" ? "open" : PCLASS(p.party)}`, style: `flex:${n}` }, n >= c.total_seats * 0.06 ? String(n) : "");
    }),
    h("span", { class: "maj", style: `left:${(100 * c.majority / c.total_seats).toFixed(2)}%`, "aria-hidden": "true" }));
  const chips = order.map(p => {
    const ch = p.seat_change;
    const n = val(p);
    const txt = !reported ? "before election" : ch == null ? "change not recorded" : ch === 0 ? "no change" : `${ch > 0 ? "gained" : "lost"} ${Math.abs(ch)}`;
    return h("li", { class: `chip-p ${PCLASS(p.party)}${ch > 0 ? " up" : ch < 0 ? " down" : ""}` }, h("b", {}, p.party),
      h("span", {}, n == null ? "—" : String(n), h("small", {}, n === 1 ? " seat" : " seats")), h("em", {}, txt));
  });
  return h("article", { class: `bopcard${stale ? " is-stale" : ""}` },
    h("div", { class: "bop-head" }, h("h3", {}, name), h("span", { class: "bop-sub" }, `${c.majority} for majority`),
      stale ? h("span", { class: "badge stale" }, "Stale") : null),
    h("p", { class: "bop-state" }, reported ? "Seats after the election, as recorded so far" : "No results recorded yet · showing seats before the election"),
    bar,
    h("ul", { class: "chips-p" }, chips),
    trendChart(c.chamber, history));
}
function trendChart(chamber, history) {
  const pts = history.map(p => ({ t: Date.parse(p.t), v: p[chamber] || {} }))
    .filter(p => Object.values(p.v).some(x => x != null));
  if (pts.length < 2) return h("p", { class: "trend-empty" }, "Trend line appears once seat counts start changing tonight.");
  const parties = [...new Set(pts.flatMap(p => Object.keys(p.v)))].filter(k => PCLASS(k) !== "oth");
  const W = 320, H = 110, P = 26;
  const t0 = pts[0].t, t1 = pts.at(-1).t || t0 + 1;
  const vals = pts.flatMap(p => parties.map(k => p.v[k]).filter(x => x != null));
  const lo = Math.min(...vals) - 1, hi = Math.max(...vals) + 1;
  const x = t => P + (W - P - 6) * (t - t0) / Math.max(1, t1 - t0);
  const y = v => 6 + (H - 22) * (1 - (v - lo) / Math.max(1, hi - lo));
  const svgNS = "http://www.w3.org/2000/svg";
  const el = (n, a) => { const e = document.createElementNS(svgNS, n); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); return e; };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "trend", role: "img", "aria-label": `Seat count trend for ${chamber}` });
  for (const v of [lo + 1, hi - 1]) { svg.append(el("line", { x1: P, x2: W - 6, y1: y(v), y2: y(v), class: "grid" })); const tx = el("text", { x: 2, y: y(v) + 4, class: "ax" }); tx.textContent = Math.round(v); svg.append(tx); }
  for (const k of parties) {
    const d = pts.filter(p => p.v[k] != null).map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v[k]).toFixed(1)}`).join(" ");
    svg.append(el("path", { d, class: `ln ${PCLASS(k)}` }));
  }
  const tl = el("text", { x: P, y: H - 4, class: "ax" }); tl.textContent = fmtTime(new Date(t0).toISOString(), false);
  const tr = el("text", { x: W - 6, y: H - 4, class: "ax", "text-anchor": "end" }); tr.textContent = fmtTime(new Date(t1).toISOString(), false);
  svg.append(tl, tr);
  return h("div", { class: "trendwrap" }, h("p", { class: "trend-title" }, "Seats recorded over the night"), svg);
}

// ---------- Senate watch board
const lastName = n => n.split(" / ")[0].replace(/,? (Jr\.|Sr\.|III|II)$/, "").split(" ").pop();
function senateBoard() {
  const w = data?.watch?.senate;
  if (!w) return [];
  const live = new Map(allRaces().map(r => [r.race_id, r]));
  const tiles = w.races.map(x => {
    const r = x.race_id && live.get(x.race_id);
    let cls = "", line1, line2;
    if (r && !awaiting(r)) {
      const [a, b] = sortCands(r);
      const tied = b && a.votes === b.votes;
      cls = tied ? "" : partyKey(a, false);
      line1 = tied ? "Tied" : `${lastName(a.name)} leads ${(a.percent ?? 0).toFixed(1)}%`;
      line2 = (reportingText(r) || "Official count") + " ↗";
    } else { line1 = "Official results"; line2 = "Open state site ↗"; }
    const link = { href: x.official_url, target: "_blank", rel: "noopener" };
    return h("li", { class: `stile ${cls}` },
      h("a", link,
        h("span", { class: "sst" }, x.st, x.special ? h("span", { class: "spec" }, "Special") : null),
        h("span", { class: "sname" }, x.state),
        h("b", {}, line1), h("small", {}, line2)));
  });
  return [sectionHead(w.title, w.races.length), h("ul", { class: "stiles" }, tiles), h("p", { class: "note" }, w.note)];
}

// ---------- House summary graphic (instead of every district)
const CLOSE_PTS = 5;
function houseSummary(groups) {
  const hs = groups.filter(g => /^U\.S\. House/.test(shortName(g.r)));
  const other = groups.filter(g => !hs.includes(g));
  const otherNodes = other.length ? [sectionHead("Other races", other.length), h("ul", { class: "rows" }, other.map(raceRow))] : [];
  if (!hs.length) return otherNodes;
  const cat = g => {
    const r = g.r;
    if (awaiting(r)) return "wait";
    const [a, b] = sortCands(r);
    if ((a.percent ?? 0) - (b?.percent ?? 0) < CLOSE_PTS) return "close";
    const k = partyKey(a, false);
    return k === "dem" || k === "rep" ? k : "oth";
  };
  const cats = [["dem", "Leaning Democratic"], ["rep", "Leaning Republican"], ["close", `Close (under ${CLOSE_PTS} pts)`], ["oth", "Leaning other"], ["wait", "Awaiting results"]];
  const count = list => Object.fromEntries(cats.map(([k]) => [k, list.filter(g => cat(g) === k)]));
  const by = count(hs);
  const geos = uniq(hs.map(g => g.r.geography));
  const shown = cats.filter(([k]) => by[k].length);
  const seg = k => by[k].length ? h("span", { class: `seg ${k}`, style: `flex:${by[k].length}` }, String(by[k].length)) : null;
  return [
    sectionHead("U.S. House", hs.length),
    h("div", { class: "house" },
      h("p", { class: "house-sub" }, `${hs.length} districts in ${geos.map(g => GEO_LABEL[g] || g).join(" and ")}: the states with free official feeds. Not a national tally of all 435 seats.`),
      h("div", { class: "hbar", role: "img", "aria-label": shown.map(([k, l]) => `${l}: ${by[k].length}`).join(", ") }, cats.map(([k]) => seg(k))),
      h("ul", { class: "hlegend" }, shown.map(([k, l]) => h("li", {}, h("span", { class: `sw ${k}`, "aria-hidden": "true" }), h("b", {}, String(by[k].length)), ` ${l}`))),
      h("table", { class: "htable" },
        h("caption", { class: "visually-hidden" }, "House districts by state and current lean"),
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "State"), ...cats.map(([k, l]) => h("th", { scope: "col", title: l }, h("span", { class: `sw ${k}`, "aria-hidden": "true" }), h("span", { class: "visually-hidden" }, l))))),
        h("tbody", {}, geos.map(geo => {
          const c = count(hs.filter(g => g.r.geography === geo));
          return h("tr", {}, h("td", {}, GEO_LABEL[geo] || geo), ...cats.map(([k]) => h("td", {}, String(c[k].length))));
        }))),
      h("p", { class: "note" }, `"Leaning" means that party's candidate leads by at least ${CLOSE_PTS} points in the official count so far. It is not a projection or a race call.`)),
    by.close.length ? [sectionHead("Close House races", by.close.length), h("ul", { class: "rows" }, by.close.map(raceRow))] : null,
    h("details", { class: "alld", open: state.open.has("all-house") || null, ontoggle: e => toggleOpen("all-house", e) },
      h("summary", { class: "plain" }, h("span", { class: "chev", "aria-hidden": "true" }, "›"), `See all ${hs.length} districts`),
      h("ul", { class: "rows" }, hs.map(raceRow))),
    ...otherNodes,
  ];
}

// ---------- shared bits
function badges(g) {
  const r = g.r;
  const out = [];
  if (r.called_status === "certified") out.push(h("span", { class: "badge certified" }, "Certified"));
  else if (r.called_status === "called") out.push(h("span", { class: "badge called" }, `Called${r.called_by ? " · " + r.called_by : ""}`));
  else out.push(h("span", { class: "badge not-called" }, "Not called"));
  if (r.ballot && r.geography === "co" && Date.now() >= Date.parse(CO_CLOSE)) out.push(h("span", { class: "badge wait" }, "No statewide feed"));
  else if (awaiting(r)) out.push(h("span", { class: "badge wait" }, r.ballot ? "Ballot · awaiting results" : "Awaiting results"));
  if (g.recs.some(x => x.stale)) out.push(h("span", { class: "badge stale" }, "Stale"));
  if (g.recs.length > 1 && disagree(g.recs)) out.push(h("span", { class: "badge differ" }, "Sources differ"));
  if (g.boulderOnly) out.push(h("span", { class: "badge approx" }, "Boulder Co. only"));
  return out;
}
function disagree(recs) {
  const key = r => JSON.stringify([r.called_status, r.candidates.map(c => [c.name.toLowerCase(), c.votes]).sort()]);
  return new Set(recs.map(key)).size > 1;
}
function reportingText(r) {
  const p = r.precincts_reporting;
  if (p && p.total) return `${Math.round(100 * p.reporting / p.total)}% of precincts reporting`;
  if (r.ballots_counted) return `${nf.format(r.ballots_counted)} ballots counted`;
  return null;
}
function pinButton(g) {
  const pinned = state.pins.has(g.id);
  return h("button", {
    class: "pin", type: "button", "aria-pressed": String(pinned), id: `pin-${cssId(g.id)}`,
    "aria-label": `${pinned ? "Unpin" : "Pin"} ${shortName(g.r)}`,
    onclick: () => { pinned ? state.pins.delete(g.id) : state.pins.add(g.id); save("pins", JSON.stringify([...state.pins])); render(); document.getElementById(`pin-${cssId(g.id)}`)?.focus(); },
    svg: pinned
      ? '<svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M12 2l3 6.9 7.5.6-5.7 4.9 1.8 7.3L12 17.8 5.4 21.7l1.8-7.3L1.5 9.5 9 8.9z"/></svg>'
      : '<svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 3.5l2.4 5.6 6.1.5-4.6 4 1.4 5.9L12 16.4l-5.3 3.1 1.4-5.9-4.6-4 6.1-.5z"/></svg>',
  });
}
const kickerFor = g => g.boulderOnly ? "Colorado · Boulder County votes" : (GEO_LABEL[g.r.geography] || g.r.geography.toUpperCase());

// ---------- key race card (head-to-head)
function keyCard(g) {
  const r = g.r;
  const c = sortCands(r);
  const a = c[0], b = c[1];
  const stale = g.recs.some(x => x.stale);
  const side = (x, right, lead) => {
    if (!x) return h("div", { class: "side" });
    const k = partyKey(x, r.is_ballot_measure);
    return h("div", { class: `side ${k}${right ? " right" : ""}${lead ? " lead" : ""}` },
      x.party ? h("span", { class: "party-tag" }, partyAbbr(x.party)) : null,
      h("span", { class: "nm" }, x.winner ? h("span", { class: "winner-mark", "aria-label": "Winner, as reported by source" }, "✓ ") : null, x.name),
      h("span", { class: "big" }, awaiting(r) ? "—" : `${(x.percent ?? 0).toFixed(1)}%`),
      h("span", { class: "vt" }, awaiting(r) ? "No votes yet" : `${nf.format(x.votes)} votes`));
  };
  const lead = !awaiting(r) && a && b && a.votes > b.votes;
  const kL = a ? partyKey(a, r.is_ballot_measure) : "", kR = b ? partyKey(b, r.is_ballot_measure) : "";
  const exKey = "k:" + g.id;
  return h("article", { class: `kcard${stale ? " is-stale" : ""}`, "aria-labelledby": `kt-${cssId(g.id)}` },
    h("div", { class: "kcard-top" },
      h("div", { class: "kcard-titles" }, h("span", { class: "kicker" }, kickerFor(g)), h("h3", { id: `kt-${cssId(g.id)}` }, shortName(r))),
      pinButton(g)),
    h("div", { class: "h2h" }, side(a, false, lead), side(b, true, false)),
    awaiting(r) ? null : h("div", { class: "mbar", role: "img", "aria-label": `${a?.name} ${(a?.percent ?? 0).toFixed(1)}%, ${b?.name ?? ""} ${(b?.percent ?? 0).toFixed(1)}%` },
      h("span", { class: `l ${kL}`, style: `width:${Math.min(100, a?.percent ?? 0)}%` }),
      h("span", { class: `r ${kR}`, style: `width:${Math.min(100, b?.percent ?? 0)}%` })),
    h("div", { class: "kfoot" },
      h("div", { class: "row" }, badges(g), reportingText(r) ? h("span", {}, reportingText(r)) : null),
      h("details", { open: state.open.has(exKey) || null, ontoggle: e => toggleOpen(exKey, e) },
        h("summary", { class: "plain" }, h("span", { class: "chev", "aria-hidden": "true" }, "›"),
          g.recs.length > 1 ? "Compare sources & all candidates" : `All ${c.length} candidates & source`),
        detailBody(g))));
}

// ---------- compact row
function raceRow(g) {
  const r = g.r;
  const lead = sortCands(r)[0];
  const k = awaiting(r) ? "wait" : partyKey(lead, r.is_ballot_measure);
  const exKey = "r:" + g.id;
  const stale = g.recs.some(x => x.stale);
  return h("li", { class: `rowi ${k}${stale ? " is-stale" : ""}` },
    h("details", { open: state.open.has(exKey) || null, ontoggle: e => toggleOpen(exKey, e) },
      h("summary", {},
        h("span", { class: "rname" }, shortName(r)),
        h("span", { class: "rlead" },
          awaiting(r) ? h("span", { class: "awaiting" }, "—") : [h("b", {}, `${(lead.percent ?? 0).toFixed(1)}%`), h("small", {}, lead.name)]),
        r.is_ballot_measure && summaryFor(r) ? h("span", { class: "rmatch rsum" }, summaryFor(r))
          : awaiting(r) && !r.is_ballot_measure ? h("span", { class: "rmatch" }, matchup(r)) : null,
        h("span", { class: "rsub" }, badges(g), reportingText(r) ? h("span", {}, reportingText(r)) : null)),
      h("div", { class: "detail" }, detailBody(g))),
    pinButton(g));
}

function matchup(r) {
  const c = sortCands(r).filter(x => x.name !== "Write-in");
  const fmt = x => `${x.name}${x.party ? ` (${partyAbbr(x.party).slice(0, 1) === "D" ? "D" : partyAbbr(x.party).slice(0, 1) === "R" ? "R" : partyAbbr(x.party)})` : ""}`;
  if (c.length === 1) return `${fmt(c[0])}, unopposed`;
  return c.slice(0, 2).map(fmt).join(" vs. ") + (c.length > 2 ? ` +${c.length - 2}` : "");
}
function toggleOpen(key, e) { e.target.open ? state.open.add(key) : state.open.delete(key); }

function detailBody(g) {
  if (g.recs.length === 1) return [candList(g.r, g.id, false), metaFor(g.r)];
  return g.recs.map(r => [h("p", { class: "cmp-src" }, r.source_name), candList(r, g.id + r.source, true), metaFor(r)]);
}

function candList(r, expandKey, showReporting) {
  const sorted = sortCands(r);
  const showAll = state.expanded.has(expandKey) || sorted.length <= TOP_N + 1;
  const shown = showAll ? sorted : sorted.slice(0, TOP_N);
  return [
    showReporting && reportingText(r) ? h("p", { class: "reporting" }, reportingText(r)) : null,
    h("ul", { class: "cands" }, shown.map(c => {
      const pct = c.percent ?? 0;
      return h("li", { class: `cand ${partyKey(c, r.is_ballot_measure)}` },
        h("span", { class: "chip", "aria-hidden": "true" }),
        h("span", { class: "name" }, c.winner ? h("span", { class: "winner-mark", "aria-label": "Winner, as reported by source" }, "✓ ") : null,
          c.name, c.party ? h("span", { class: "party" }, ` · ${partyAbbr(c.party)}`) : null),
        h("span", { class: "pct" }, awaiting(r) ? "—" : `${pct.toFixed(1)}%`),
        h("span", { class: "bar", role: "img", "aria-label": `${c.name}: ${pct.toFixed(1)} percent, ${nf.format(c.votes)} votes` },
          h("span", { style: `width:${Math.min(100, Math.max(0, pct))}%` })),
        h("span", { class: "votes" }, nf.format(c.votes)));
    })),
    showAll ? null : h("button", { class: "more", type: "button", onclick: () => { state.expanded.add(expandKey); render(); } }, `Show all ${sorted.length} candidates`),
  ];
}

function metaFor(r) {
  if (r.ballot) return h("div", { class: "meta" },
    h("span", {}, "Candidates from the official ballot: ", h("a", { href: r.source_url }, r.source_name)),
    r.geography === "co" ? h("span", {}, "Statewide results: ", h("a", { href: "https://www.sos.state.co.us/pubs/elections/main.html" }, "Colorado Secretary of State"), " (no automated feed available).")
      : h("span", {}, "Results appear here when Boulder County posts them (after 7:00 PM MT)."));
  return h("div", { class: "meta" },
    h("span", {}, `Last updated ${fmtTime(r.source_updated_at || r.fetched_at)}${r.source_updated_at ? " (source)" : ""} · checked ${fmtTime(r.fetched_at)}`),
    h("span", {}, "Source: ", h("a", { href: r.source_url }, r.source_name)),
    r.percent_computed ? h("span", {}, "Percentages calculated from the source's vote counts.") : null,
    r.stale ? h("span", {}, "⚠ Latest check failed; showing last good data.") : null);
}

// ---------- 80301
function zipSection() {
  const z = data?.zip_80301;
  if (!z) return [];
  const t = z.turnout;
  const coreN = z.precincts.filter(p => (p.share_in_zip || 0) >= 0.5).length;
  return [sectionHead(`ZIP ${z.zip}`, null), h("div", { class: "zip" },
    h("div", { class: "zip-head" }, h("span", { class: "kicker" }, "Approximate"), h("h3", {}, `ZIP ${z.zip} turnout`), h("p", {}, z.label)),
    h("div", { class: "stats" },
      h("div", { class: "stat" }, h("b", {}, t ? nf.format(t.core.ballots) : "—"), h("small", {}, "ballots processed")),
      h("div", { class: "stat" }, h("b", {}, t && t.core.turnout_pct != null ? `${t.core.turnout_pct}%` : "—"), h("small", {}, "turnout")),
      h("div", { class: "stat" }, h("b", {}, t ? t.core.precincts : coreN), h("small", {}, "core precincts"))),
    h("div", { class: "zip-body" },
      t ? h("p", {}, `All ${t.all_overlapping.precincts} overlapping precincts: ${nf.format(t.all_overlapping.ballots)} ballots of ${nf.format(t.all_overlapping.registered)} active voters.${t.stale ? " ⚠ Stale." : ""}`)
        : h("p", {}, "Turnout appears when Boulder County posts results (after 7:00 PM MT)."),
      h("p", {}, z.precinct_results_note),
      h("details", {}, h("summary", { class: "plain" }, h("span", { class: "chev", "aria-hidden": "true" }, "›"), `Precincts and method (${z.precincts.length})`),
        precinctTable(z),
        h("p", {}, z.method),
        h("p", {}, "Mapping data: ", z.mapping_sources.map((s, i) => [i ? ", " : "", h("a", { href: s.url }, s.name), ` (${s.license})`])),
        t ? h("p", {}, "Turnout: ", h("a", { href: t.source_url }, "Boulder County Elections – Voter Turnout by Precinct"), ` · checked ${fmtTime(t.fetched_at)}`) : null)))];
}
function precinctTable(z) {
  return h("table", {},
    h("caption", {}, `Core precincts have at least half their addresses in ${z.zip}.`),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Precinct"), h("th", { scope: "col" }, "In ZIP"), h("th", { scope: "col" }, "Ballots"), h("th", { scope: "col" }, "Turnout"))),
    h("tbody", {}, z.precincts.map(p => h("tr", {},
      h("td", {}, `${p.short_code}${(p.share_in_zip || 0) >= 0.5 ? "" : " (fringe)"}`),
      h("td", {}, `${Math.round(100 * (p.share_in_zip || 0))}%`),
      h("td", {}, p.ballots != null ? nf.format(p.ballots) : "—"),
      h("td", {}, p.ballots != null && p.registered ? `${(100 * p.ballots / p.registered).toFixed(1)}%` : "—")))));
}

// ---------- links & sources
function linksSection(tab, links) {
  const mine = links.filter(l => l.tab === tab);
  if (!mine.length) return [];
  return [sectionHead(tab === "national" ? "Race calls & other states" : "More coverage", null),
    h("ul", { class: "links" }, mine.map(l => h("li", {}, h("a", { href: l.url, rel: "noopener", target: "_blank" }, h("span", {}, l.name, h("small", {}, l.group))))))];
}
function sourcesSection(tab) {
  if (!data) return [];
  const items = Object.entries(data.sources).filter(([k]) => (SOURCE_TABS[k] || []).includes(tab));
  if (!items.length) return [];
  const label = { ok: "Live", stale: "Stale", error: "Error", not_yet_available: "Waiting", not_configured: "Not available" };
  return [sectionHead("Data sources", null), h("ul", { class: "srcs" }, items.map(([, b]) => {
    const s = b.status;
    return h("li", {},
      h("span", { class: `dot ${s.status}`, "aria-hidden": "true" }),
      h("span", { class: "nm" }, `${label[s.status] || s.status} · `), h("a", { href: s.url }, s.name),
      h("span", { style: "display:block" }, s.status === "ok" || s.status === "stale"
        ? `Last good check ${fmtTime(s.last_success_at)}${s.message ? " · " + s.message : ""}`
        : (s.message || "")),
      s.last_error ? h("span", { class: "err" }, `Latest attempt ${fmtTime(s.last_attempt_at)} failed: ${s.last_error}`) : null);
  }))];
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

document.getElementById("refresh").addEventListener("click", () => fetchData({ manual: true }));
document.addEventListener("visibilitychange", () => { if (!document.hidden) fetchData(); });
initTabs();
initTheme();
fetchData();
setInterval(() => { if (!document.hidden) fetchData(); }, AUTO_REFRESH_MS);
setInterval(() => { if (data) updateAsOf(); }, 30_000);
