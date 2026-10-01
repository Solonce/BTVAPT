/* BTV Rentals — single-page UI over the JSON API. No build step. */
"use strict";

// ============================================================ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString());
const moneyShort = (n) => (n == null ? "?" : n >= 1000 ? "$" + (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1).replace(/\.0$/, "") + "k" : "$" + n);
const fmtDate = (iso, opts = { month: "short", day: "numeric", year: "numeric" }) => {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return isNaN(d) ? "—" : d.toLocaleDateString(undefined, opts);
};
const ago = (iso) => {
  if (!iso) return "never";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
const dur = (s) => (s == null ? "" : s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`);
const bedsLabel = (b) => (b == null ? "—" : b === 0 ? "Studio" : `${+b} bed`);
const initials = (n) => (n || "?").replace(/[^A-Za-z ]/g, " ").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
const store = {
  get(k, d) { try { const v = localStorage.getItem("btv." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("btv." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.append(t);
  setTimeout(() => t.remove(), 2400);
}
async function req(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { "content-type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    toast(msg);
    throw new Error(msg);
  }
  return r.json();
}
const api = { get: (u) => req("GET", u), put: (u, b) => req("PUT", u, b), post: (u, b) => req("POST", u, b || {}), del: (u) => req("DELETE", u) };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const ICON = {
  search: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`,
  down: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2.4" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></svg>`,
  close: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`,
  ext: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>`,
};

const MATCH = { exact: "On target", near: "Near miss", early: "Too early", late: "Too late", unknown: "Date unknown" };
const MATCH_ORDER = { exact: 0, near: 1, late: 2, early: 3, unknown: 4 };
const TAG_PRESETS = ["w/d", "balcony", "roof access", "character", "quiet", "top floor", "parking", "yard"];
const MY_STATUSES = ["interested", "toured", "applied", "passed"];

function deltaText(a) {
  if (a.kind === "now") return "Available now";
  if (a.delta_days == null) return a.kind === "flexible" ? "Flexible move-in" : "Move-in date unknown";
  const d = a.delta_days;
  return d === 0 ? "Exactly your move-in date" : `${Math.abs(d)} days ${d < 0 ? "before" : "after"} your move-in`;
}
function matchPill(a, short = false) {
  const delta = a.delta_days != null && a.kind !== "now" && a.match !== "exact" ? ` ${a.delta_days > 0 ? "+" : "−"}${Math.abs(a.delta_days)}d` : "";
  return `<span class="pill m-${a.match}" title="${esc(deltaText(a))}">${short && a.match === "exact" ? "On target" : MATCH[a.match]}${delta}</span>`;
}
function riskPill(r) {
  if (!r) return "";
  if (r.level === "high") return `<span class="pill r-high" title="${esc(r.reasons.map((x) => x.reason).join("; "))}">⚠ Likely scam</span>`;
  if (r.level === "medium") return `<span class="pill r-medium" title="${esc(r.reasons.map((x) => x.reason).join("; "))}">Check carefully</span>`;
  return "";
}
function availText(a) {
  if (a.kind === "now") return "Available now";
  return a.effective_date ? "Avail. " + fmtDate(a.effective_date, { month: "short", day: "numeric", year: "numeric" }) : "Date unknown";
}
function haversineKm(a, b) {
  const R = 6371, dLat = ((b[0] - a[0]) * Math.PI) / 180, dLon = ((b[1] - a[1]) * Math.PI) / 180;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos((a[0] * Math.PI) / 180) * Math.cos((b[0] * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
function distanceText(u) {
  if (!state.anchor || u.lat == null) return "";
  const km = haversineKm(state.anchor.latlng, [u.lat, u.lon]) * 1.25; // street-network fudge
  const walk = Math.round((km / 4.8) * 60);
  return walk <= 40 ? `${walk} min walk` : `${km.toFixed(1)} km`;
}

// ============================================================ state
const DEFAULT_FILTERS = { q: "", near: false, minRent: "", maxRent: "", beds: "", towns: [], pets: "", hideRisky: true, privateOnly: false, includeGone: false, myStatus: "", tags: [], sort: "match", inView: false };
const state = {
  settings: null,
  units: [],
  filters: Object.assign({}, DEFAULT_FILTERS, store.get("filters2", {})),
  selectedUnit: null,
  map: null, cluster: null, markers: new Map(), fitted: false, baseLayer: null, anchorLayer: null,
  anchor: store.get("anchor", null),
  timers: [],
};
const saveFilters = () => store.set("filters2", state.filters);

// ============================================================ popovers
let openPop = null;
function closePop() { if (openPop) { openPop.remove(); openPop = null; } }
function popover(anchor, html, mount, align = "left") {
  closePop();
  const p = document.createElement("div");
  p.className = "popover"; p.innerHTML = html; document.body.append(p);
  const r = anchor.getBoundingClientRect();
  p.style.top = `${r.bottom + window.scrollY + 6}px`;
  const left = align === "right" ? r.right - p.offsetWidth : r.left;
  p.style.left = `${Math.max(8, Math.min(left, window.innerWidth - p.offsetWidth - 8))}px`;
  openPop = p;
  mount && mount(p);
  setTimeout(() => {
    const off = (e) => { if (!p.contains(e.target) && e.target !== anchor) { closePop(); document.removeEventListener("mousedown", off); } };
    document.addEventListener("mousedown", off);
  });
  return p;
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (openPop) return closePop();
  if ($(".lightbox")) return;
  if ($("#drawer").classList.contains("open")) closeDrawer();
});

// ============================================================ settings
async function loadSettings() {
  state.settings = await api.get("/api/settings");
  $("#moveinLabel").textContent = `${fmtDate(state.settings.target_move_in, { month: "short", day: "numeric", year: "numeric" })} ± ${state.settings.near_miss_days}d`;
}
$("#moveinBtn").addEventListener("click", (e) => {
  const s = state.settings;
  popover(e.currentTarget, `
    <h4>Target move-in</h4>
    <div style="display:flex;flex-direction:column;gap:12px;width:260px">
      <input type="date" class="field" id="pTarget" value="${esc(s.target_move_in)}">
      <div><label class="label">Near-miss window: <b id="pWinV">±${s.near_miss_days} days</b></label>
      <input type="range" id="pWin" min="0" max="120" step="5" value="${s.near_miss_days}" style="width:100%;accent-color:var(--accent)"></div>
      <div class="faint" style="font-size:12px">Only changes what's highlighted. Every listing is still collected.</div>
      <button class="btn primary" id="pSave">Apply</button>
    </div>`, (p) => {
    $("#pWin", p).addEventListener("input", (ev) => { $("#pWinV", p).textContent = `±${ev.target.value} days`; });
    $("#pSave", p).addEventListener("click", async () => {
      state.settings = await api.put("/api/settings", { target_move_in: $("#pTarget", p).value, near_miss_days: Number($("#pWin", p).value) });
      closePop(); await loadSettings(); toast("Move-in target updated");
      if (currentRoute().view === "explore") { await loadUnits(); renderResults(); }
      if (state.selectedUnit) openUnit(state.selectedUnit, true);
    });
  }, "right");
});

async function refreshChrome() {
  try {
    const [h, leads] = await Promise.all([api.get("/api/health"), api.get("/api/leads?triage=new")]);
    $("#healthDot").className = "dot " + (h.ok ? "ok" : "bad");
    $("#healthDot").title = h.ok ? "All sources healthy" : "Needs attention: " + h.problem_sources.join(", ");
    const n = leads.length;
    $("#inboxCount").textContent = n; $("#inboxCount").classList.toggle("hidden", !n);
  } catch { $("#healthDot").className = "dot bad"; }
}

// ============================================================ routing
function currentRoute() {
  let parts = location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean);
  if (parts[0] === "map") parts[0] = "explore";
  if (parts[0] === "contacts") parts = ["people", ...(parts[1] ? ["person", parts[1]] : [])];
  return { view: parts[0] || "explore", sub: parts[1], id: parts[2] ? Number(parts[2]) : null };
}
let renderedView = null;
async function route() {
  const r = currentRoute();
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.tab === r.view));
  state.timers.forEach(clearInterval); state.timers = [];
  closePop();
  if (renderedView !== r.view) {
    renderedView = r.view;
    if (r.view === "people") await renderPeople();
    else if (r.view === "inbox") await renderInbox();
    else if (r.view === "health") await renderHealth();
    else await renderExplore();
  }
  if (r.view === "explore" && r.sub === "unit" && r.id) openUnit(r.id);
  else if (r.view === "people" && r.sub === "person" && r.id) openContact(r.id);
  else if (r.view === "people" && r.sub === "owner" && r.id) openOwner(r.id);
  else closeDrawer(false);
}
window.addEventListener("hashchange", route);

// ============================================================ explore
async function loadUnits() {
  const data = await api.get(`/api/units?include_gone=${state.filters.includeGone}`);
  state.units = data.units;
}

function inMapView(u) {
  if (!state.filters.inView || !state.map || u.lat == null) return !state.filters.inView || u.lat != null;
  return state.map.getBounds().contains([u.lat, u.lon]);
}
function filteredUnits({ ignoreView = false } = {}) {
  const f = state.filters;
  const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
  const us = state.units.filter((u) => {
    if (f.near && !["exact", "near"].includes(u.availability.match)) return false;
    if (f.minRent && u.rent != null && u.rent < Number(f.minRent)) return false;
    if (f.maxRent && u.rent != null && u.rent > Number(f.maxRent)) return false;
    if (f.beds !== "" && (u.beds == null || u.beds < Number(f.beds))) return false;
    if (f.towns.length && !f.towns.includes(u.city || "")) return false;
    if (f.pets === "cats" && !/cat|pet/i.test(`${u.pets} ${u.amenities.join(" ")}`)) return false;
    if (f.pets === "dogs" && !/dog|pet/i.test(`${u.pets} ${u.amenities.join(" ")}`)) return false;
    if (f.pets && /no pets|not allowed/i.test(u.pets || "") && !/cats? allowed|dogs? allowed/i.test(u.pets || "")) return false;
    if (f.hideRisky && u.risk && u.risk.level === "high") return false;
    if (f.privateOnly && !u.private_landlord) return false;
    if (f.tags.length && !f.tags.every((t) => u.tags.includes(t))) return false;
    if (f.myStatus === "any" && !u.prefs.status && !u.prefs.rating) return false;
    if (f.myStatus && f.myStatus !== "any" && u.prefs.status !== f.myStatus) return false;
    if (!f.myStatus && u.prefs.status === "passed") return false;
    if (words.length) {
      const hay = [u.address, u.title, u.unit, u.city, ...u.amenities, ...u.text_amenities.map((a) => a.tag), ...u.tags,
        ...u.sources.map((s) => s.source), ...u.owners.map((o) => o.name)].join(" ").toLowerCase();
      if (!words.every((w) => hay.includes(w))) return false;
    }
    return ignoreView || inMapView(u);
  });
  const sorters = {
    match: (a, b) => MATCH_ORDER[a.availability.match] - MATCH_ORDER[b.availability.match] || Math.abs(a.availability.delta_days ?? 9999) - Math.abs(b.availability.delta_days ?? 9999) || (a.rent ?? 1e9) - (b.rent ?? 1e9),
    rent: (a, b) => (a.rent ?? 1e9) - (b.rent ?? 1e9),
    rentDesc: (a, b) => (b.rent ?? 0) - (a.rent ?? 0),
    newest: (a, b) => Date.parse(b.posted_at || b.first_seen) - Date.parse(a.posted_at || a.first_seen),
    rating: (a, b) => (b.prefs.rating ?? 0) - (a.prefs.rating ?? 0) || MATCH_ORDER[a.availability.match] - MATCH_ORDER[b.availability.match],
    distance: (a, b) => (state.anchor && a.lat != null && b.lat != null ? haversineKm(state.anchor.latlng, [a.lat, a.lon]) - haversineKm(state.anchor.latlng, [b.lat, b.lon]) : 0),
  };
  return us.sort(sorters[f.sort] || sorters.match);
}

async function renderExplore() {
  closeDrawer(false);
  $("#view").innerHTML = `
  <div class="explore" id="explore">
    <section class="listpane">
      <div class="listhead">
        <div class="title"><h1 id="lhTitle">Explore</h1>
          <select class="sortsel" id="fSort" title="Sort">
            <option value="match">Best match for move-in</option><option value="rent">Rent: low to high</option><option value="rentDesc">Rent: high to low</option>
            <option value="newest">Newest</option><option value="rating">My rating</option><option value="distance">Closest to my pin</option>
          </select></div>
        <label class="search">${ICON.search}<input class="field" id="fQ" placeholder="Search street, amenity, landlord…" value="${esc(state.filters.q)}"></label>
        <div class="filterbar" id="filterbar"></div>
      </div>
      <div class="results" id="results"></div>
    </section>
    <section class="mappane">
      <div id="map"></div>
      <div class="mapctl tc glass"><label class="mapbtn"><input type="checkbox" id="fInView" ${state.filters.inView ? "checked" : ""}> Only show what's on the map</label></div>
      <div class="mapctl tr">
        <div class="glass layers" id="layers"><button data-l="clean">Clean</button><button data-l="detailed">Detailed</button><button data-l="satellite">Satellite</button></div>
        <div class="glass maphint" id="anchorHint"></div>
      </div>
      <div class="mapctl bl glass legend">
        <span><i style="background:var(--exact)"></i>On target</span><span><i style="background:var(--near)"></i>Near miss</span>
        <span><i style="background:var(--early)"></i>Early</span><span><i style="background:var(--late)"></i>Late</span>
        <span><i style="background:var(--unknown)"></i>Unknown</span><span>● private landlord</span>
      </div>
    </section>
    <button class="btn primary mobile-toggle" id="mobileToggle">Map</button>
  </div>`;
  $("#fSort").value = state.filters.sort;
  $("#fQ").addEventListener("input", debounce((e) => { state.filters.q = e.target.value; saveFilters(); renderResults(); }, 150));
  $("#fSort").addEventListener("change", (e) => { state.filters.sort = e.target.value; saveFilters(); renderResults(); });
  $("#fInView").addEventListener("change", (e) => { state.filters.inView = e.target.checked; saveFilters(); renderResults(); });
  $("#mobileToggle").addEventListener("click", () => {
    const ex = $("#explore"); ex.classList.toggle("show-map");
    $("#mobileToggle").textContent = ex.classList.contains("show-map") ? "List" : "Map";
    state.map && setTimeout(() => state.map.invalidateSize(), 50);
  });
  initMap();
  await loadUnits();
  renderResults();
}

function renderFilterbar() {
  const f = state.filters;
  const chip = (id, label, on) => `<button class="chip ${on ? "on" : ""}" id="${id}">${label}</button>`;
  const rentLbl = f.minRent || f.maxRent ? `${f.minRent ? moneyShort(+f.minRent) : "$0"}–${f.maxRent ? moneyShort(+f.maxRent) : "any"}` : "Price";
  const bedsLbl = f.beds === "" ? "Beds" : f.beds === "0" ? "Studio+" : `${f.beds}+ beds`;
  const townLbl = f.towns.length ? (f.towns.length === 1 ? f.towns[0] : `${f.towns.length} towns`) : "Town";
  const moreN = [f.pets, f.privateOnly, f.includeGone, !f.hideRisky, f.myStatus, ...f.tags].filter(Boolean).length;
  $("#filterbar").innerHTML =
    chip("fbNear", "Near my move-in", f.near) +
    chip("fbPrice", `${rentLbl} ${ICON.down}`, f.minRent || f.maxRent) +
    chip("fbBeds", `${bedsLbl} ${ICON.down}`, f.beds !== "") +
    chip("fbTown", `${esc(townLbl)} ${ICON.down}`, f.towns.length) +
    chip("fbMore", `More${moreN ? ` · ${moreN}` : ""} ${ICON.down}`, moreN) +
    (JSON.stringify({ ...f, q: "", sort: "", inView: false }) !== JSON.stringify({ ...DEFAULT_FILTERS, sort: "" }) ? `<button class="chip" id="fbReset" style="border-style:dashed">Reset</button>` : "");
  const rerender = () => { saveFilters(); renderResults(); };
  $("#fbNear").onclick = () => { f.near = !f.near; rerender(); };
  $("#fbPrice").onclick = (e) => popover(e.currentTarget, `<h4>Monthly rent</h4><div class="row" style="width:240px">
      <input class="field" type="number" step="50" min="0" id="pMin" placeholder="Min" value="${esc(f.minRent)}">
      <span class="muted">–</span><input class="field" type="number" step="50" min="0" id="pMax" placeholder="Max" value="${esc(f.maxRent)}"></div>`,
    (p) => { const up = debounce(() => { f.minRent = $("#pMin", p).value; f.maxRent = $("#pMax", p).value; rerender(); }, 300); $$("input", p).forEach((i) => i.addEventListener("input", up)); $("#pMax", p).focus(); });
  $("#fbBeds").onclick = (e) => popover(e.currentTarget, `<h4>Bedrooms</h4><div class="seg">${[["", "Any"], ["0", "Studio+"], ["1", "1+"], ["2", "2+"], ["3", "3+"], ["4", "4+"]].map(([v, l]) => `<button data-v="${v}" class="${f.beds === v ? "on" : ""}">${l}</button>`).join("")}</div>`,
    (p) => $$("button", p).forEach((b) => b.addEventListener("click", () => { f.beds = b.dataset.v; closePop(); rerender(); })));
  $("#fbTown").onclick = (e) => {
    const counts = {};
    state.units.forEach((u) => { if (u.city) counts[u.city] = (counts[u.city] || 0) + 1; });
    const towns = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    popover(e.currentTarget, `<h4>Towns</h4><div style="display:flex;flex-direction:column;gap:6px;max-height:300px;overflow:auto;min-width:220px">${towns.map(([t, n]) => `<label style="display:flex;gap:8px;align-items:center;cursor:pointer"><input type="checkbox" value="${esc(t)}" ${f.towns.includes(t) ? "checked" : ""} style="accent-color:var(--accent)"> ${esc(t)} <span class="faint" style="margin-left:auto">${n}</span></label>`).join("")}</div>`,
      (p) => $$("input", p).forEach((i) => i.addEventListener("change", () => { f.towns = $$("input:checked", p).map((x) => x.value); rerender(); })));
  };
  $("#fbMore").onclick = (e) => {
    const allTags = [...new Set([...TAG_PRESETS, ...state.units.flatMap((u) => u.tags)])];
    popover(e.currentTarget, `<div style="display:flex;flex-direction:column;gap:14px;width:280px">
      <div><span class="label">Pets</span><div class="seg" id="pPets">${[["", "Any"], ["cats", "Cats"], ["dogs", "Dogs"]].map(([v, l]) => `<button data-v="${v}" class="${f.pets === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div><span class="label">My list</span><select class="field" id="pMine"><option value="">Everything (hide passed)</option><option value="any">Rated or tracked</option>${MY_STATUSES.map((s) => `<option value="${s}">${s[0].toUpperCase() + s.slice(1)}</option>`).join("")}</select></div>
      <div><span class="label">My tags</span><div class="chips" id="pTags">${allTags.map((t) => `<button class="chip ${f.tags.includes(t) ? "on" : ""}" data-t="${esc(t)}">${esc(t)}</button>`).join("")}</div></div>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="pPriv" ${f.privateOnly ? "checked" : ""} style="accent-color:var(--accent)"> Private landlords only</label>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="pRisk" ${f.hideRisky ? "checked" : ""} style="accent-color:var(--accent)"> Hide likely scams</label>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="pGone" ${f.includeGone ? "checked" : ""} style="accent-color:var(--accent)"> Include rented / gone</label>
    </div>`, (p) => {
      $$("#pPets button", p).forEach((b) => b.addEventListener("click", () => { f.pets = b.dataset.v; $$("#pPets button", p).forEach((x) => x.classList.toggle("on", x === b)); rerender(); }));
      $("#pMine", p).value = f.myStatus; $("#pMine", p).addEventListener("change", (ev) => { f.myStatus = ev.target.value; rerender(); });
      $$("#pTags .chip", p).forEach((c) => c.addEventListener("click", () => { const t = c.dataset.t; f.tags = f.tags.includes(t) ? f.tags.filter((x) => x !== t) : [...f.tags, t]; c.classList.toggle("on"); rerender(); }));
      $("#pPriv", p).addEventListener("change", (ev) => { f.privateOnly = ev.target.checked; rerender(); });
      $("#pRisk", p).addEventListener("change", (ev) => { f.hideRisky = ev.target.checked; rerender(); });
      $("#pGone", p).addEventListener("change", async (ev) => { f.includeGone = ev.target.checked; saveFilters(); await loadUnits(); renderResults(); });
    });
  };
  const reset = $("#fbReset");
  if (reset) reset.onclick = () => { state.filters = { ...DEFAULT_FILTERS, sort: f.sort, q: f.q, inView: f.inView }; rerender(); };
}

function renderResults() {
  if (!$("#results")) return;
  renderFilterbar();
  const us = filteredUnits();
  const near = us.filter((u) => ["exact", "near"].includes(u.availability.match)).length;
  $("#lhTitle").innerHTML = `${us.length} place${us.length === 1 ? "" : "s"} <small>· ${near} near move-in</small>`;
  $("#results").innerHTML = us.length ? us.map(cardHTML).join("") : `<div class="empty"><h3>Nothing matches</h3>Try widening the price, town or move-in window.</div>`;
  $$("#results .lc").forEach((el) => {
    const id = Number(el.dataset.id);
    el.addEventListener("click", () => { location.hash = `#/explore/unit/${id}`; });
    el.addEventListener("mouseenter", () => hlMarker(id, true));
    el.addEventListener("mouseleave", () => hlMarker(id, false));
  });
  renderMarkers(filteredUnits({ ignoreView: true }));
}

function cardHTML(u) {
  const a = u.availability, walk = distanceText(u);
  const src = u.sources.length > 1 ? `${u.sources.length} sources` : u.sources[0]?.source || "";
  return `<article class="lc ${state.selectedUnit === u.id ? "sel" : ""}" data-id="${u.id}">
    <div class="ph ${u.photo ? "" : "none"}" style="${u.photo ? `background-image:url('${esc(u.photo)}')` : ""}">${u.prefs.rating ? `<span class="fav">${"★".repeat(u.prefs.rating)}</span>` : ""}</div>
    <div class="body">
      <div class="r1"><span class="price num">${money(u.rent)}<small> /mo</small></span>${matchPill(a, true)}</div>
      <div class="addr ${u.status === "gone" ? "gone" : ""}" title="${esc(u.address)}">${esc((u.address || "Address not given").split(",")[0])}${u.unit ? ` <span class="muted">#${esc(u.unit)}</span>` : ""}</div>
      <div class="meta"><span>${bedsLabel(u.beds)}</span><span>${u.baths ?? "?"} bath</span>${u.sqft ? `<span>${u.sqft} ft²</span>` : ""}<span>${esc(u.city || "")}</span></div>
      <div class="meta"><span>${availText(a)}</span>${walk ? `<span class="walk">${walk}</span>` : ""}</div>
      <div class="foot">${riskPill(u.risk)}${u.private_landlord ? `<span class="pill accent">Private landlord</span>` : ""}${u.status !== "available" ? `<span class="pill neutral s-${u.status}">${u.status}</span>` : ""}${u.prefs.status ? `<span class="pill neutral">${esc(u.prefs.status)}</span>` : ""}<span class="faint">${esc(src)}</span></div>
    </div>
  </article>`;
}

// ---------------------------------------------------------------- map
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const BASEMAPS = {
  clean: [{ url: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`, attr: "Tiles &copy; Esri — Esri, HERE, Garmin, &copy; OpenStreetMap contributors", max: 16, cls: "tiles-light" },
          { url: `${ESRI}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, max: 16, cls: "tiles-light" }],
  detailed: [{ url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", attr: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', max: 19, cls: "tiles-light" }],
  satellite: [{ url: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, attr: "Imagery &copy; Esri, Maxar, Earthstar Geographics", max: 19, cls: "" },
              { url: `${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, max: 19, cls: "" }],
};
function setBasemap(name) {
  if (!BASEMAPS[name]) name = "clean";
  if (state.baseLayer) state.map.removeLayer(state.baseLayer);
  // Esri canvas tiles stop at z16; let Leaflet upscale them past that.
  state.baseLayer = L.layerGroup(BASEMAPS[name].map((b) => L.tileLayer(b.url, { maxZoom: 20, maxNativeZoom: b.max, attribution: b.attr, className: b.cls }))).addTo(state.map);
  $$("#layers button").forEach((x) => x.classList.toggle("on", x.dataset.l === name));
  store.set("basemap", name);
}
function initMap() {
  if (typeof L === "undefined") { $("#map").innerHTML = `<div class="empty">Map library failed to load. The list still works.</div>`; return; }
  if (state.map) { state.map.remove(); state.map = null; state.markers.clear(); state.fitted = false; }
  state.map = L.map("map", { zoomControl: false, attributionControl: true }).setView([44.4759, -73.2121], 13);
  L.control.zoom({ position: "bottomright" }).addTo(state.map);
  setBasemap(store.get("basemap", "clean"));
  $$("#layers button").forEach((b) => b.addEventListener("click", () => setBasemap(b.dataset.l)));
  const clusterColor = (markers) => {
    const best = markers.map((m) => m.options.match).sort((a, b) => MATCH_ORDER[a] - MATCH_ORDER[b])[0];
    return css("--" + (best || "unknown"));
  };
  state.cluster = L.markerClusterGroup
    ? L.markerClusterGroup({ maxClusterRadius: 38, showCoverageOnHover: false, spiderfyOnMaxZoom: true, disableClusteringAtZoom: 17,
        iconCreateFunction: (c) => L.divIcon({ html: `<div style="--cc:${clusterColor(c.getAllChildMarkers())}">${c.getChildCount()}</div>`, className: "cluster", iconSize: [40, 40] }) })
    : L.layerGroup();
  state.cluster.addTo(state.map);
  state.map.on("moveend", () => { if (state.filters.inView) renderResults(); });
  state.map.on("contextmenu", (e) => setAnchor([e.latlng.lat, e.latlng.lng]));
  drawAnchor();
}
function setAnchor(latlng) {
  state.anchor = latlng ? { latlng } : null;
  store.set("anchor", state.anchor);
  drawAnchor(); renderResults();
  toast(latlng ? "Pin set — cards show walking time to it" : "Pin cleared");
}
function drawAnchor() {
  if (!state.map) return;
  if (state.anchorLayer) state.map.removeLayer(state.anchorLayer);
  state.anchorLayer = null;
  const hint = $("#anchorHint");
  if (state.anchor) {
    const ll = state.anchor.latlng;
    state.anchorLayer = L.layerGroup([
      L.circle(ll, { radius: 800, color: css("--ink"), weight: 1, opacity: .5, fillOpacity: .04, dashArray: "4 4" }),
      L.circle(ll, { radius: 1600, color: css("--ink"), weight: 1, opacity: .35, fillOpacity: .02, dashArray: "4 4" }),
      L.marker(ll, { icon: L.divIcon({ className: "", html: '<div class="anchor-pin"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }), draggable: true })
        .on("dragend", (e) => { const p = e.target.getLatLng(); setAnchor([p.lat, p.lng]); })
        .bindTooltip("Your pin (drag to move). Rings: ~10 and ~20 min walk.", { direction: "top" }),
    ]).addTo(state.map);
    if (hint) hint.innerHTML = `Walking times from your pin · <a href="#" id="clearAnchor">clear</a>`;
    const c = $("#clearAnchor"); if (c) c.onclick = (e) => { e.preventDefault(); setAnchor(null); };
  } else if (hint) hint.textContent = "Right-click the map to drop a pin (work, campus…)";
}
function renderMarkers(units) {
  if (!state.map) return;
  const colors = Object.fromEntries(Object.keys(MATCH).map((k) => [k, css("--" + k)]));
  state.cluster.clearLayers(); state.markers.clear();
  const pts = [], perBuilding = new Map(), layers = [];
  for (const u of units) {
    if (u.lat == null || u.lon == null) continue;
    const n = perBuilding.get(u.building_id) || 0; perBuilding.set(u.building_id, n + 1);
    const lat = u.lat + n * 0.00007, lon = u.lon + n * 0.0001;
    const cls = ["pin", u.prefs.rating >= 4 ? "fav" : "", u.risk && ["high", "medium"].includes(u.risk.level) ? "risk" : "", u.private_landlord ? "private" : "", state.selectedUnit === u.id ? "sel" : ""].join(" ");
    const icon = L.divIcon({ className: cls, html: `<b style="background:${colors[u.availability.match]}">${moneyShort(u.rent)}</b>`, iconSize: [60, 24], iconAnchor: [30, 12] });
    const m = L.marker([lat, lon], { icon, match: u.availability.match, riseOnHover: true, zIndexOffset: 400 - MATCH_ORDER[u.availability.match] * 100 })
      .on("click", () => { location.hash = `#/explore/unit/${u.id}`; })
      .on("mouseover", () => hlCard(u.id, true)).on("mouseout", () => hlCard(u.id, false))
      .bindTooltip(`<div class="img" style="${u.photo ? `background-image:url('${esc(u.photo)}')` : ""}"></div><div class="tx"><b>${money(u.rent)}</b> · ${bedsLabel(u.beds)}<div>${esc((u.address || "").split(",")[0])}${u.unit ? " #" + esc(u.unit) : ""}</div><div>${esc(deltaText(u.availability))}</div></div>`,
        { className: "mini", direction: "top", offset: [0, -14], opacity: 1 });
    layers.push(m); state.markers.set(u.id, m); pts.push([lat, lon]);
  }
  state.cluster.addLayers ? state.cluster.addLayers(layers) : layers.forEach((l) => state.cluster.addLayer(l));
  if (!state.fitted && pts.length) {
    const med = (a) => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
    const mla = med(pts.map((p) => p[0])), mlo = med(pts.map((p) => p[1]));
    const core = pts.filter(([la, lo]) => Math.abs(la - mla) < 0.03 && Math.abs(lo - mlo) < 0.04);
    state.map.fitBounds(L.latLngBounds(core.length >= 3 ? core : pts).pad(0.08));
    state.fitted = true;
  }
}
function hlMarker(id, on) {
  const m = state.markers.get(id); if (!m) return;
  const el = m.getElement(); if (el) el.classList.toggle("hl", on);
  m.setZIndexOffset(on ? 2000 : 400 - MATCH_ORDER[m.options.match] * 100);
}
function hlCard(id, on) {
  const el = $(`.lc[data-id="${id}"]`); if (!el) return;
  el.classList.toggle("hl", on);
  if (on) el.scrollIntoView({ block: "nearest" });
}

// ============================================================ drawer: unit
function closeDrawer(updateHash = true) {
  $("#drawer").classList.remove("open");
  state.selectedUnit = null;
  $$(".lc.sel").forEach((e) => e.classList.remove("sel"));
  $$(".pin.sel").forEach((e) => e.classList.remove("sel"));
  if (updateHash) {
    const v = currentRoute().view;
    history.pushState(null, "", `#/${v}`);
    route();
  }
}
function lightbox(urls, i = 0) {
  const lb = document.createElement("div"); lb.className = "lightbox";
  const draw = () => { lb.innerHTML = `<img src="${esc(urls[i])}" alt=""><button class="close">✕</button>${urls.length > 1 ? `<button class="prev">‹</button><button class="next">›</button>` : ""}`; };
  draw();
  lb.addEventListener("click", (e) => {
    if (e.target.classList.contains("prev")) { i = (i - 1 + urls.length) % urls.length; draw(); }
    else if (e.target.classList.contains("next")) { i = (i + 1) % urls.length; draw(); }
    else lb.remove();
  });
  const k = (e) => {
    if (!document.body.contains(lb)) return document.removeEventListener("keydown", k);
    if (e.key === "Escape") lb.remove();
    if (e.key === "ArrowRight") { i = (i + 1) % urls.length; draw(); }
    if (e.key === "ArrowLeft") { i = (i - 1 + urls.length) % urls.length; draw(); }
  };
  document.addEventListener("keydown", k);
  document.body.append(lb);
}
function sparkline(points) {
  if (points.length < 2) return "";
  const w = 480, h = 64, xs = points.map((p) => Date.parse(p.at)), ys = points.map((p) => p.rent);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (x) => (x1 === x0 ? w / 2 : 8 + ((x - x0) / (x1 - x0)) * (w - 16));
  const sy = (y) => (y1 === y0 ? h / 2 : h - 10 - ((y - y0) / (y1 - y0)) * (h - 20));
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/>${points.map((p, i) => `<circle cx="${sx(xs[i])}" cy="${sy(ys[i])}" r="3.5" fill="var(--accent)"><title>${fmtDate(p.at)}: ${money(p.rent)}</title></circle>`).join("")}</svg>`;
}
function riskPanel(r) {
  if (!r) return "";
  if (r.level === "verified") return `<div class="panel ok"><div class="head"><span class="big">Listed on a property manager's own site</span><span class="pill r-verified">Verified source</span></div>
    <div class="ev">Comes straight from the manager's listings system, so it isn't a copy someone reposted.</div></div>`;
  if (r.level === "none") return `<div class="panel"><div class="head"><span class="big">No scam signals found</span></div><div class="ev">Still: tour in person and never pay before seeing the unit and signing a lease.</div></div>`;
  const cls = r.level === "high" ? "risk" : r.level === "medium" ? "warn" : "";
  const title = r.level === "high" ? "Strong scam signals" : r.level === "medium" ? "Some red flags — verify before paying anything" : "Minor flags";
  return `<div class="panel ${cls}"><div class="head"><span class="big">${title}</span>${riskPill(r) || `<span class="pill neutral">score ${r.score}</span>`}</div>
    <ul>${r.reasons.map((x) => `<li>${esc(x.reason)}${x.evidence ? ` <small>— “${esc(x.evidence)}”</small>` : ""}</li>`).join("")}</ul>
    <div class="ev">Safe steps: see it in person, confirm the owner (city property records), never wire money or pay by gift card, and be wary of anyone who can't show the unit.</div></div>`;
}

async function openUnit(id, force = false) {
  const d = $("#drawer");
  if (state.selectedUnit === id && !force && d.classList.contains("open")) return;
  state.selectedUnit = id;
  $$(".lc").forEach((e) => e.classList.toggle("sel", Number(e.dataset.id) === id));
  if (!d.classList.contains("open")) { d.innerHTML = `<div class="empty">Loading…</div>`; d.classList.add("open"); }
  let u;
  try { u = await api.get(`/api/units/${id}`); } catch { d.innerHTML = `<div class="empty">That unit no longer exists.</div>`; return; }
  if (u.id !== id) { state.selectedUnit = u.id; history.replaceState(null, "", `#/explore/unit/${u.id}`); }
  const m = state.markers.get(u.id);
  if (m && state.map) {
    const showIt = () => {
      const drawerW = window.innerWidth > 900 ? d.offsetWidth : 0;
      const pt = state.map.project(m.getLatLng(), state.map.getZoom()).add([drawerW / 2, 0]);
      state.map.panTo(state.map.unproject(pt, state.map.getZoom()), { animate: true });
      const el = m.getElement(); if (el) el.classList.add("sel");
    };
    state.cluster.zoomToShowLayer ? state.cluster.zoomToShowLayer(m, showIt) : showIt();
  }
  const a = u.availability, photos = u.photos || [];
  const tagsAvail = [...new Set([...TAG_PRESETS, ...state.units.flatMap((x) => x.tags)])].filter((t) => !u.tags.includes(t));
  const extraAmen = u.text_amenities.filter((t) => !u.amenities.some((s) => s.toLowerCase().includes(t.tag.split("/")[0])));
  const walk = distanceText(u);

  d.innerHTML = `
  <div class="dhead"><span class="crumb">${esc(u.city || "")}${u.sources.length ? " · " + esc(u.sources.map((s) => s.source).join(", ")) : ""}</span>
    <button class="icon-btn" id="dClose" title="Close (Esc)">${ICON.close}</button></div>
  <div class="dbody">
    ${photos.length ? `<div class="hero"><div class="main" id="heroMain" style="background-image:url('${esc(photos[0])}')"></div>${photos.length > 1 ? `<span class="count">${photos.length} photos</span><div class="strip">${photos.slice(0, 24).map((p, i) => `<img loading="lazy" data-i="${i}" class="${i === 0 ? "on" : ""}" src="${esc(p)}" alt="">`).join("")}</div>` : ""}</div>` : ""}
    <div class="dtitle">
      <h2>${esc((u.address || "Address not given").split(",")[0])}${u.unit ? ` <span class="muted" style="font-weight:500">#${esc(u.unit)}</span>` : ""}</h2>
      <div class="sub">${esc(u.title || "")}</div>
      <div class="badges">${matchPill(a)}${riskPill(u.risk)}${u.private_landlord ? `<span class="pill accent">Private landlord</span>` : ""}<span class="pill neutral s-${u.status}">${esc(u.status)}</span>${walk ? `<span class="pill neutral">${walk} from your pin</span>` : ""}</div>
    </div>
    <div class="stats">
      <div><div class="k">Rent</div><div class="v">${money(u.rent)}</div></div>
      <div><div class="k">Beds · Baths</div><div class="v">${u.beds === 0 ? "Studio" : u.beds ?? "—"} · ${u.baths ?? "—"}</div></div>
      <div><div class="k">Size</div><div class="v">${u.sqft ? u.sqft.toLocaleString() + " ft²" : "—"}</div></div>
      <div><div class="k">Deposit</div><div class="v">${money(u.deposit)}</div></div>
    </div>

    <div class="panel">
      <div class="head"><span class="big">${a.kind === "now" ? "Available now" : a.effective_date ? fmtDate(a.effective_date, { weekday: "short", month: "long", day: "numeric", year: "numeric" }) : "Move-in date unknown"}</span>${matchPill(a)}</div>
      <div class="ev">${esc(deltaText(a))} (${fmtDate(state.settings.target_move_in)})</div>
      ${a.text.raw ? `<div class="ev">From the description: <q>${esc(a.text.raw)}</q>${a.text.confidence != null ? ` · ${Math.round(a.text.confidence * 100)}% sure` : ""}</div>` : ""}
      ${a.structured.raw ? `<div class="ev">Listing field: <q>${esc(a.structured.raw)}</q>${a.from === "text" && a.note ? " — the description overrides it" : ""}</div>` : ""}
      ${a.note && a.from !== "text" ? `<div class="ev">${esc(a.note)}</div>` : ""}
    </div>

    ${riskPanel(u.risk)}

    <section class="mine">
      <div class="row">
        <div class="stars" id="stars">${[1, 2, 3, 4, 5].map((i) => `<button data-v="${i}" class="${(u.prefs.rating || 0) >= i ? "on" : ""}" title="${i} star${i > 1 ? "s" : ""}">★</button>`).join("")}</div>
        <div class="seg" id="myStatus">${MY_STATUSES.map((s) => `<button data-v="${s}" class="${u.prefs.status === s ? "on" : ""}">${s}</button>`).join("")}</div>
      </div>
      <div class="chips" id="unitTags">
        ${u.tags.map((t) => `<span class="chip on">${esc(t)}<button class="x" data-rm="${esc(t)}" title="Remove">✕</button></span>`).join("")}
        ${tagsAvail.slice(0, 7).map((t) => `<button class="chip" data-add="${esc(t)}">+ ${esc(t)}</button>`).join("")}
        <input class="field" id="newTag" placeholder="Add tag…" style="width:110px;padding:4px 10px;border-radius:999px;font-size:12.5px">
      </div>
      <textarea class="field" id="notes" rows="3" placeholder="Notes — what you saw, what to ask…">${esc(u.prefs.notes || "")}</textarea>
      <div class="faint" style="font-size:12px" id="notesState">Your notes, rating and tags stay with this unit through relists and merges.</div>
    </section>

    <section class="sect"><h3>Features</h3>
      <div class="chips">
        ${u.amenities.map((x) => `<span class="chip soft">${esc(x)}</span>`).join("")}
        ${extraAmen.map((x) => `<span class="chip dashed" title="Mentioned in the description: “${esc(x.evidence)}”">${esc(x.tag)}</span>`).join("")}
        ${!u.amenities.length && !extraAmen.length ? `<span class="muted">None listed.</span>` : ""}
      </div>
      ${extraAmen.length ? `<div class="faint" style="font-size:12px;margin-top:8px">Dashed: mentioned in the description, not verified.</div>` : ""}
    </section>

    <section class="sect"><h3>Details</h3><dl class="dl">
      <dt>Pets</dt><dd>${esc(u.pets || "—")}</dd>
      <dt>Parking</dt><dd>${esc(u.parking || "—")}</dd>
      <dt>Utilities incl.</dt><dd>${esc(u.utilities || "—")}</dd>
      <dt>Last checked</dt><dd>${ago(u.last_verified)}</dd>
      <dt>First seen</dt><dd>${fmtDate(u.first_seen)}${u.posted_at ? ` · posted ${fmtDate(u.posted_at)}` : ""}</dd>
    </dl></section>

    ${u.description ? `<section class="sect"><h3>Description</h3><div class="desc" id="desc">${esc(u.description)}</div><button class="btn quiet sm" id="descMore" style="margin-top:6px">Read more</button></section>` : ""}

    ${u.contacts.length || u.owners.length ? `<section class="sect"><h3>Who's behind it</h3><div class="rows">
      ${u.owners.map((o) => `<a class="rowlink" href="#/people/owner/${o.company_id}"><div><div style="font-weight:600">${esc(o.name)}</div><div class="sub">Owner${o.manager ? ` · managed by ${esc(o.manager)}` : ""}</div></div><div class="sub">${esc(o.phone || "")}</div></a>`).join("")}
      ${u.contacts.map((c) => `<a class="rowlink" href="#/people/person/${c.id}"><div><div style="font-weight:600">${esc(c.name)}</div><div class="sub">${c.private ? "Private contact" : esc(c.company || "")}</div></div><div class="sub" style="text-align:right">${c.phones.map(esc).join("<br>")}${c.emails.length ? "<br>" + c.emails.map(esc).join("<br>") : ""}</div></a>`).join("")}
    </div></section>` : ""}

    <section class="sect"><h3>Listings (${u.listing_details.length})</h3><div class="rows">
      ${u.listing_details.map((l) => `<div><div><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(u.sources.find((s) => s.source_listing_id === l.source_listing_id)?.source || l.source_id)} ${ICON.ext}</a>
          <div class="sub">seen ${ago(l.last_seen)} · <span class="s-${l.status}">${l.status}</span>${l.link_locked ? " · linked by you" : ""}</div></div>
          ${u.listing_details.length > 1 ? `<button class="btn sm" data-unlink="${l.source_listing_id}" title="This listing is actually a different unit">Not the same unit</button>` : ""}</div>`).join("")}
    </div></section>

    <section class="sect"><h3>History</h3>
      ${u.price_history.length > 1 ? sparkline(u.price_history) : ""}
      <ul class="tl">
        ${u.price_history.map((p) => `<li><span class="when">${fmtDate(p.at)}</span><span>Listed at <b class="num">${money(p.rent)}</b></span></li>`).join("")}
        ${u.status_events.filter((e) => e.from).map((e) => `<li><span class="when">${fmtDate(e.at)}</span><span class="s-${e.to}">${esc(e.to)}</span>&nbsp;<span class="faint">${esc(e.reason || "")}</span></li>`).join("")}
      </ul>
      ${u.turnover.predicted_next_available ? `<div class="panel" style="margin-top:10px"><div class="ev">Likely to turn over again around <b>${fmtDate(u.turnover.predicted_next_available, { month: "long", year: "numeric" })}</b> <span class="faint">(${esc(u.turnover.basis)})</span>. Worth a note to reach out early.</div></div>` : ""}
    </section>

    <details class="more"><summary>Duplicates & merging</summary>
      <div style="display:flex;gap:6px;align-items:center;margin-top:10px">
        <input class="field" id="mergeId" type="number" placeholder="Other unit #" style="width:130px">
        <button class="btn sm" id="mergeBtn">Merge into this one</button><span class="faint" style="font-size:12px">This is #${u.id}</span>
      </div>
      ${u.building && u.building.other_units.length ? `<div class="faint" style="font-size:12px;margin-top:8px">Same building: ${u.building.other_units.map((x) => `<a href="#/explore/unit/${x}">#${x}</a>`).join(", ")}</div>` : ""}
      ${u.merge_log.length ? `<ul class="tl" style="margin-top:8px">${u.merge_log.map((m2) => `<li><span class="when">${fmtDate(m2.at)}</span><span style="flex:1">${esc(m2.action.replace("_", " "))} <span class="faint">${esc(m2.actor)}${m2.reason ? " · " + esc(m2.reason) : ""}</span></span>${m2.undone_at ? `<span class="faint">undone</span>` : m2.actor === "user" || (m2.action === "link_listing" && m2.subject.from_unit_id != null) ? `<button class="btn quiet sm" data-undo="${m2.id}">Undo</button>` : ""}</li>`).join("")}</ul>` : ""}
    </details>
  </div>`;

  $("#dClose").addEventListener("click", () => closeDrawer());
  if (photos.length) {
    let cur = 0;
    $("#heroMain").addEventListener("click", () => lightbox(photos, cur));
    $$(".strip img", d).forEach((img) => img.addEventListener("click", () => {
      cur = Number(img.dataset.i); $("#heroMain").style.backgroundImage = `url('${photos[cur]}')`;
      $$(".strip img", d).forEach((x) => x.classList.toggle("on", x === img));
    }));
  }
  const desc = $("#desc");
  if (desc) {
    const more = $("#descMore");
    if (desc.scrollHeight <= 230) { desc.classList.add("open"); more.remove(); }
    else more.addEventListener("click", () => { desc.classList.toggle("open"); more.textContent = desc.classList.contains("open") ? "Show less" : "Read more"; });
  }
  const refresh = async () => { await loadUnits(); renderResults(); };
  $$("#stars button").forEach((b) => b.addEventListener("click", async () => {
    const v = Number(b.dataset.v), rating = u.prefs.rating === v ? null : v;
    await api.put(`/api/units/${u.id}/prefs`, { rating }); u.prefs.rating = rating;
    $$("#stars button").forEach((x) => x.classList.toggle("on", rating != null && Number(x.dataset.v) <= rating)); refresh();
  }));
  $$("#myStatus button").forEach((b) => b.addEventListener("click", async () => {
    const status = u.prefs.status === b.dataset.v ? null : b.dataset.v;
    await api.put(`/api/units/${u.id}/prefs`, { status }); u.prefs.status = status;
    $$("#myStatus button").forEach((x) => x.classList.toggle("on", x.dataset.v === status)); refresh();
  }));
  const saveNotes = debounce(async (v) => { await api.put(`/api/units/${u.id}/prefs`, { notes: v }); $("#notesState").textContent = "Saved " + new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }, 600);
  $("#notes").addEventListener("input", (e) => { $("#notesState").textContent = "Saving…"; saveNotes(e.target.value); });
  const addTag = async (t) => { if (!t.trim()) return; await api.post(`/api/units/${u.id}/tags`, { tag: t }); await refresh(); openUnit(u.id, true); };
  $$("#unitTags [data-add]").forEach((b) => b.addEventListener("click", () => addTag(b.dataset.add)));
  $$("#unitTags [data-rm]").forEach((b) => b.addEventListener("click", async () => { await api.del(`/api/units/${u.id}/tags/${encodeURIComponent(b.dataset.rm)}`); await refresh(); openUnit(u.id, true); }));
  $("#newTag").addEventListener("keydown", (e) => { if (e.key === "Enter") addTag(e.target.value); });
  $("#mergeBtn").addEventListener("click", async () => {
    const other = Number($("#mergeId").value); if (!other) return;
    if (!confirm(`Merge unit #${other} into #${u.id}? Its listings, notes and tags move here. You can undo this.`)) return;
    await api.post("/api/units/merge", { into_id: u.id, merged_id: other, reason: "same unit (manual)" });
    toast("Merged"); await refresh(); openUnit(u.id, true);
  });
  $$("[data-unlink]", d).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Split this listing off into its own unit?")) return;
    const r = await api.post(`/api/listings/${b.dataset.unlink}/unlink`, { reason: "different unit (manual)" });
    toast(`Split off as unit #${r.unit_id}`); await refresh(); openUnit(u.id, true);
  }));
  $$("[data-undo]", d).forEach((b) => b.addEventListener("click", async () => { await api.post(`/api/merges/${b.dataset.undo}/undo`); toast("Undone"); await refresh(); openUnit(u.id, true); }));
}

// ============================================================ inbox
function bookmarkletCode() {
  const origin = location.origin;
  const js = `(function(){var s=String(window.getSelection()||'').trim();if(!s){var m=document.querySelector('[role=article]')||document.querySelector('article')||document.body;s=(m.innerText||'').slice(0,6000)}var u=location.href.split('?')[0];window.open('${origin}/#/inbox/add?u='+encodeURIComponent(u)+'&t='+encodeURIComponent(s.slice(0,6000))+'&h='+encodeURIComponent(document.title),'btv','width=560,height=720')})()`;
  return "javascript:" + encodeURIComponent(js);
}
async function renderInbox() {
  const r = currentRoute();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const tab = store.get("inboxTab", "new");
  $("#view").innerHTML = `
  <div class="page">
    <div class="phead"><div><h1>Inbox</h1><p>Posts from people — Facebook groups and Marketplace, Front Porch Forum, word of mouth. Save them here and they get the same date parsing, mapping, contact matching and scam checks as everything else.</p></div></div>
    <div class="grid2">
      <div class="card" style="padding:18px;display:flex;flex-direction:column;gap:12px">
        <h3 style="font-size:15px">Save a post</h3>
        <textarea class="field" id="leadText" rows="7" placeholder="Paste the post text here (who posted it, rent, when it's available, how to reach them)…">${esc(params.get("t") || "")}</textarea>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <input class="field" id="leadUrl" placeholder="Link to the post (optional)" value="${esc(params.get("u") || "")}">
          <input class="field" id="leadAuthor" placeholder="Posted by (optional)">
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><span class="faint" style="font-size:12px" id="leadHint">${params.get("h") ? "From: " + esc(params.get("h")) : ""}</span><button class="btn primary" id="leadSave">Save to inbox</button></div>
      </div>
      <div class="card" style="padding:18px;display:flex;flex-direction:column;gap:12px">
        <h3 style="font-size:15px">One-click from Facebook</h3>
        <div><a class="bookmarklet" href="${bookmarkletCode()}" onclick="event.preventDefault();alert('Drag this button to your bookmarks bar.')">＋ Save to BTV</a></div>
        <ol class="steps">
          <li>Drag the green button to your browser's bookmarks bar (once).</li>
          <li>On a Facebook group post or Marketplace listing, highlight the post text (or just click — it grabs the post).</li>
          <li>Click <b>Save to BTV</b>. This page opens with the text filled in; hit Save.</li>
        </ol>
        <div class="faint" style="font-size:12px">You stay logged in to Facebook as yourself; nothing scrapes your account. Works on any site (Front Porch Forum, Zillow, a friend's post).</div>
        <details class="more"><summary>Email forwarding (automatic)</summary><div class="faint" style="font-size:12.5px;margin-top:6px">Set up a Gmail just for this, turn on Facebook group notifications and Front Porch Forum digests to it, then add an <code>[inbox]</code> section to <code>btv.toml</code>. Rental-looking emails land here every 30 minutes.</div></details>
      </div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
      <div class="seg" id="leadTabs">${["new", "saved", "contacted", "dismissed", "all"].map((t) => `<button data-t="${t}" class="${tab === t ? "on" : ""}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
      <span class="faint" style="font-size:12.5px" id="leadCount"></span>
    </div>
    <div id="leads" style="display:flex;flex-direction:column;gap:12px"></div>
  </div>`;
  $("#leadSave").addEventListener("click", async () => {
    const text = $("#leadText").value.trim(), url = $("#leadUrl").value.trim() || null;
    if (!text && !url) return toast("Paste the post text first");
    const res = await api.post("/api/leads", { text, url, author: $("#leadAuthor").value.trim() || null, title: null });
    toast(res.unit_id ? "Saved and placed on the map" : "Saved to inbox");
    history.replaceState(null, "", "#/inbox"); $("#leadText").value = ""; $("#leadUrl").value = ""; $("#leadAuthor").value = ""; $("#leadHint").textContent = "";
    drawLeads(); refreshChrome();
    if (window.opener && window.name === "btv") setTimeout(() => window.close(), 900);
  });
  $$("#leadTabs button").forEach((b) => b.addEventListener("click", () => { store.set("inboxTab", b.dataset.t); $$("#leadTabs button").forEach((x) => x.classList.toggle("on", x === b)); drawLeads(); }));
  if (r.sub === "add") $("#leadText").focus();
  drawLeads();
}
async function drawLeads() {
  const tab = store.get("inboxTab", "new");
  const all = await api.get("/api/leads");
  const rows = tab === "all" ? all : all.filter((l) => l.triage === tab);
  $("#leadCount").textContent = `${rows.length} of ${all.length}`;
  $("#leads").innerHTML = rows.length ? rows.map((l) => `
    <article class="card lead" data-id="${l.source_listing_id}">
      <div class="top">
        <div><div style="font-weight:650">${esc(l.title || "Untitled post")}</div>
          <div class="faint" style="font-size:12.5px">${esc(l.source)}${l.author ? " · " + esc(l.author) : ""} · ${ago(l.received_at)}${l.url ? ` · <a href="${esc(l.url)}" target="_blank" rel="noopener">open ${ICON.ext}</a>` : ""}</div></div>
        <div class="chips">${l.risk.level === "high" ? `<span class="pill r-high">⚠ Likely scam</span>` : l.risk.level === "medium" ? `<span class="pill r-medium">Check carefully</span>` : ""}</div>
      </div>
      <div class="chips">
        ${l.rent ? `<span class="chip soft">${money(l.rent)}</span>` : ""}${l.beds != null ? `<span class="chip soft">${bedsLabel(l.beds)}</span>` : ""}
        ${l.availability.kind === "date" ? `<span class="chip soft">Avail. ${fmtDate(l.availability.date)}</span>` : l.availability.kind === "now" ? `<span class="chip soft">Available now</span>` : ""}
        ${l.unit_id ? `<a class="chip soft" href="#/explore/unit/${l.unit_id}">On the map →</a>` : l.address ? `<span class="chip dashed">${esc(l.address)}</span>` : `<span class="chip dashed">No address yet</span>`}
      </div>
      ${l.risk.reasons.length ? `<div style="font-size:12.5px;color:var(--warn)">${l.risk.reasons.map((x) => esc(x.reason)).join(" · ")}</div>` : ""}
      ${l.text ? `<div class="text">${esc(l.text)}</div>` : ""}
      <div class="acts">
        ${!l.unit_id ? `<input class="field" placeholder="Street address to place it on the map" style="flex:1;min-width:200px" data-addr="${l.source_listing_id}" value="${esc(l.address_override || "")}"><button class="btn sm" data-place="${l.source_listing_id}">Place</button>` : ""}
        <span style="flex:1"></span>
        ${["saved", "contacted", "dismissed"].map((t) => `<button class="btn sm ${l.triage === t ? "primary" : ""}" data-tri="${t}" data-id="${l.source_listing_id}">${t === "saved" ? "Keep" : t === "contacted" ? "Contacted" : "Dismiss"}</button>`).join("")}
      </div>
    </article>`).join("") : `<div class="empty card"><h3>Nothing here</h3>Saved posts and forwarded emails show up here.</div>`;
  $$("#leads .text").forEach((t) => { if (t.scrollHeight > 124) t.addEventListener("click", () => t.classList.toggle("open")); });
  $$("[data-tri]").forEach((b) => b.addEventListener("click", async () => { await api.put(`/api/leads/${b.dataset.id}`, { triage: b.dataset.tri }); drawLeads(); refreshChrome(); }));
  $$("[data-place]").forEach((b) => b.addEventListener("click", async () => {
    const v = $(`[data-addr="${b.dataset.place}"]`).value.trim(); if (!v) return;
    const res = await api.put(`/api/leads/${b.dataset.place}`, { address: v.match(/,|vt|vermont/i) ? v : v + ", Burlington, VT" });
    toast(res.unit_id ? "Placed on the map (geocoding runs in the background)" : "Saved"); drawLeads();
  }));
}

// ============================================================ people
async function renderPeople() {
  $("#view").innerHTML = `<div class="page"><div class="empty">Loading…</div></div>`;
  const [contacts, owners, companies] = await Promise.all([api.get("/api/contacts"), api.get("/api/owners"), api.get("/api/companies")]);
  const people = contacts.filter((c) => c.kind === "person" || c.private);
  const tab = store.get("peopleTab", "owners");
  $("#view").innerHTML = `
  <div class="page">
    <div class="phead"><div><h1>People</h1><p>Who owns and rents out the places you're looking at. Reach out directly before units hit the public feeds.</p></div>
      <label class="search" style="width:260px">${ICON.search}<input class="field" id="pQ" placeholder="Search names, phones, streets…"></label></div>
    <div class="seg" id="pTabs">
      <button data-t="owners">Owners <span class="faint">${owners.length}</span></button>
      <button data-t="people">Individuals <span class="faint">${people.length}</span></button>
      <button data-t="managers">Property managers <span class="faint">${companies.filter((c) => c.kind === "manager").length}</span></button>
    </div>
    <div id="pBody"></div>
  </div>`;
  const draw = () => {
    const t = store.get("peopleTab", "owners"), q = ($("#pQ").value || "").toLowerCase();
    $$("#pTabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === t));
    const match = (...xs) => !q || xs.join(" ").toLowerCase().includes(q);
    if (t === "owners") {
      const rows = owners.filter((o) => match(o.name, o.managers.join(" "), o.phones.join(" "), o.buildings.map((b) => b.address).join(" ")));
      $("#pBody").innerHTML = rows.length ? `<div class="tiles">${rows.map((o) => `
        <a class="card tile" href="#/people/owner/${o.id}" style="color:inherit;text-decoration:none">
          <div class="n">${esc(o.name)}</div>
          <div class="s">${o.kind === "owner_llc" ? "Company / LLC" : "Owner"}${o.managers.length ? " · via " + esc(o.managers.join(", ")) : ""}</div>
          <div class="s" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(o.buildings.map((b) => (b.address || "").split(",")[0]).slice(0, 3).join(" · "))}</div>
          <div class="k"><div><b>${o.building_count}</b><span>buildings</span></div><div><b>${o.unit_count}</b><span>units listed</span></div></div>
        </a>`).join("")}</div>` : `<div class="empty card"><h3>No owners yet</h3>The <b>owners</b> job fills this from AppFolio's public search (ShowMeTheRent) daily. Run it from Health.</div>`;
    } else if (t === "people") {
      const rows = people.filter((c) => match(c.name, c.phones.join(" "), c.emails.join(" "), c.notes || ""));
      $("#pBody").innerHTML = rows.length ? `<div class="card list">${rows.map((c) => personRow(c)).join("")}</div>` : `<div class="empty card"><h3>No individuals yet</h3>Private landlords appear here from Craigslist posts, Facebook posts you save in the Inbox, and forwarded emails — whenever a post names a person or gives a phone/email.</div>`;
    } else {
      const offices = contacts.filter((c) => !(c.kind === "person" || c.private));
      const rows = companies.filter((c) => c.kind === "manager" && match(c.name));
      $("#pBody").innerHTML = `<div class="tiles">${rows.map((c) => {
        const off = offices.filter((o) => o.company_id === c.id);
        return `<div class="card tile" data-co="${c.id}"><div class="n">${esc(c.name)}</div>
          <div class="s">${off.map((o) => esc(o.phones[0] || o.emails[0] || "")).filter(Boolean).join(" · ") || "—"}${c.website ? ` · <a href="${esc(c.website)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">site ${ICON.ext}</a>` : ""}</div>
          <div class="k"><div><b>${c.unit_count}</b><span>units seen</span></div><div><b class="num" style="font-size:14px">${c.rent_min ? money(c.rent_min) + "–" + money(c.rent_max) : "—"}</b><span>rent range</span></div></div></div>`;
      }).join("")}</div>`;
      $$("[data-co]").forEach((el) => el.addEventListener("click", () => {
        const off = offices.find((o) => o.company_id === Number(el.dataset.co));
        if (off) location.hash = `#/people/person/${off.id}`;
      }));
    }
  };
  $$("#pTabs button").forEach((b) => b.addEventListener("click", () => { store.set("peopleTab", b.dataset.t); draw(); }));
  $("#pQ").addEventListener("input", debounce(draw, 120));
  store.set("peopleTab", tab); draw();
}
function personRow(c) {
  return `<a class="person" href="#/people/person/${c.id}" style="color:inherit;text-decoration:none">
    <div class="avatar ${c.kind === "office" ? "office" : ""}">${c.kind === "office" ? "🏢" : esc(initials(c.name))}</div>
    <div style="min-width:0"><div style="font-weight:600">${esc(c.name)} ${c.multi_property ? `<span class="pill accent">${c.building_count} buildings</span>` : ""} ${c.needs_review ? `<span class="pill r-medium">review</span>` : ""}</div>
      <div class="faint" style="font-size:12.5px">${esc([...c.phones, ...c.emails].join(" · ") || "No phone/email yet")}${c.company ? " · " + esc(c.company) : ""}</div></div>
    <div style="text-align:right;font-size:12.5px"><div><b>${c.unit_count}</b> unit${c.unit_count === 1 ? "" : "s"}</div><div class="faint">${ago(c.last_seen)}</div></div>
  </a>`;
}

async function openContact(id) {
  const d = $("#drawer");
  d.innerHTML = `<div class="empty">Loading…</div>`; d.classList.add("open");
  const c = await api.get(`/api/contacts/${id}`);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const mx = Math.max(1, ...Object.values(c.stats.listing_months));
  d.innerHTML = `
  <div class="dhead"><span class="crumb">${c.private ? "Private contact" : esc(c.company || "")}</span><button class="icon-btn" id="dClose">${ICON.close}</button></div>
  <div class="dbody" style="padding-top:20px">
    <div style="display:flex;gap:14px;align-items:center"><div class="avatar ${c.kind === "office" ? "office" : ""}" style="width:52px;height:52px;font-size:18px">${c.kind === "office" ? "🏢" : esc(initials(c.name))}</div>
      <div class="dtitle"><h2>${esc(c.name)}</h2><div class="sub">${c.kind === "office" ? "Office line" : "Person"}${c.role ? " · " + esc(c.role) : ""}</div></div></div>
    <div class="stats" style="grid-template-columns:repeat(3,1fr)">
      <div><div class="k">Units</div><div class="v">${c.stats.unit_count}</div></div>
      <div><div class="k">Buildings</div><div class="v">${c.stats.building_count}</div></div>
      <div><div class="k">Rent range</div><div class="v" style="font-size:14px">${c.stats.rent_min ? money(c.stats.rent_min) + "–" + money(c.stats.rent_max) : "—"}</div></div>
    </div>
    <section class="sect"><h3>Reach</h3><dl class="dl">
      <dt>Phone</dt><dd>${c.phones.map((p) => `<a href="tel:${esc(p.replace(/[^\d+]/g, ""))}">${esc(p)}</a>`).join("<br>") || "—"}</dd>
      <dt>Email</dt><dd>${c.emails.map((e) => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join("<br>") || "—"}</dd>
      <dt>Also seen as</dt><dd>${esc(c.name_variants.join(", ") || "—")}</dd>
      <dt>Found on</dt><dd>${esc(c.stats.sources.join(", ") || "—")}</dd>
    </dl></section>
    <section class="sect"><h3>When their places get listed</h3>
      <div class="months">${months.map((m) => `<div style="height:${((c.stats.listing_months[m] || 0) / mx) * 100}%" title="${m}: ${c.stats.listing_months[m] || 0}"></div>`).join("")}</div>
      <div class="months-l">${months.map((m) => `<span>${m[0]}</span>`).join("")}</div>
    </section>
    ${c.merge_suggestions.length ? `<section class="sect"><h3>Possible duplicates</h3><div class="rows">${c.merge_suggestions.map((s) => `
      <div><span>“${esc(s.name)}” may be <a href="#/people/person/${s.suggested.id}">${esc(s.suggested.name)}</a></span>
      <span><button class="btn sm" data-review="${s.mention_id}" data-d="merge">Same</button> <button class="btn quiet sm" data-review="${s.mention_id}" data-d="keep">Different</button></span></div>`).join("")}</div></section>` : ""}
    <section class="sect"><h3>Outreach</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <select class="field" id="oChan"><option value="email">Email</option><option value="call">Call</option><option value="text">Text</option><option value="in_person">In person</option></select>
        <select class="field" id="oDir"><option value="out">I reached out</option><option value="in">They replied</option></select>
        <input class="field" id="oSum" placeholder="What did you ask or hear?" style="grid-column:span 2">
        <input class="field" id="oOut" placeholder="Outcome (optional)">
        <input class="field" id="oFollow" type="date" title="Follow up on">
      </div>
      <button class="btn primary" id="oSave" style="margin-top:10px">Log it</button>
      <ul class="tl" style="margin-top:12px">${c.outreach.map((o) => `<li><span class="when">${fmtDate(o.at)}</span><span><b>${esc(o.channel)}</b> ${o.direction === "in" ? "← reply" : "→"} ${esc(o.summary || "")}${o.outcome ? ` <span class="muted">· ${esc(o.outcome)}</span>` : ""}${o.follow_up_on ? ` <span class="pill m-near">follow up ${fmtDate(o.follow_up_on, { month: "short", day: "numeric" })}</span>` : ""}</span></li>`).join("") || `<li class="muted">Nothing logged yet.</li>`}</ul>
    </section>
    <section class="sect"><h3>Notes</h3><textarea class="field" id="cNotes" rows="3" placeholder="What they're like, what they own, when to follow up…">${esc(c.notes || "")}</textarea><div class="faint" id="cNotesState" style="font-size:12px;margin-top:4px"></div></section>
    <section class="sect"><h3>Places (${c.units.length})</h3><div class="rows">
      ${c.units.map((u) => `<a class="rowlink" href="#/explore/unit/${u.id}"><div style="min-width:0"><div style="font-weight:600">${esc((u.address || "").split(",")[0])}${u.unit ? " #" + esc(u.unit) : ""}</div>
        <div class="sub">${esc(u.city || "")} · ${bedsLabel(u.beds)} · <span class="s-${u.status}">${u.status}</span></div></div>
        <div style="text-align:right"><div class="num" style="font-weight:650">${money(u.rent)}</div>${matchPill(u.availability, true)}</div></a>`).join("")}
    </div></section>
    <details class="more"><summary>Same person as…</summary>
      <div style="display:flex;gap:6px;margin-top:10px"><select class="field" id="mergeContact"><option value="">Pick a contact to merge into this one</option></select><button class="btn" id="mergeContactBtn">Merge</button></div>
    </details>
  </div>`;
  $("#dClose").addEventListener("click", () => closeDrawer());
  $("#oSave").addEventListener("click", async () => {
    await api.post(`/api/contacts/${c.id}/outreach`, { channel: $("#oChan").value, direction: $("#oDir").value, summary: $("#oSum").value || null, outcome: $("#oOut").value || null, follow_up_on: $("#oFollow").value || null });
    toast("Logged"); openContact(c.id);
  });
  const saveNotes = debounce(async (v) => { await api.put(`/api/contacts/${c.id}`, { notes: v }); $("#cNotesState").textContent = "Saved"; }, 600);
  $("#cNotes").addEventListener("input", (e) => { $("#cNotesState").textContent = "Saving…"; saveNotes(e.target.value); });
  $$("[data-review]", d).forEach((b) => b.addEventListener("click", async () => { await api.post(`/api/mentions/${b.dataset.review}/review`, { decision: b.dataset.d }); toast("Saved"); openContact(c.id); }));
  api.get("/api/contacts").then((all) => {
    const sel = $("#mergeContact"); if (!sel) return;
    sel.innerHTML += all.filter((x) => x.id !== c.id).map((x) => `<option value="${x.id}">${esc(x.name)}${x.phones[0] ? " · " + esc(x.phones[0]) : x.emails[0] ? " · " + esc(x.emails[0]) : ""}</option>`).join("");
  });
  $("#mergeContactBtn").addEventListener("click", async () => {
    const other = Number($("#mergeContact").value); if (!other) return;
    if (!confirm("Merge that contact into this one?")) return;
    await api.post("/api/contacts/merge", { into_id: c.id, merged_id: other, reason: "same person (manual)" });
    toast("Merged"); renderedView = null; await route();
  });
}

async function openOwner(id) {
  const d = $("#drawer");
  d.innerHTML = `<div class="empty">Loading…</div>`; d.classList.add("open");
  const o = (await api.get("/api/owners")).find((x) => x.id === id);
  if (!o) { d.innerHTML = `<div class="empty">Owner not found.</div>`; return; }
  d.innerHTML = `
  <div class="dhead"><span class="crumb">Owner</span><button class="icon-btn" id="dClose">${ICON.close}</button></div>
  <div class="dbody" style="padding-top:20px">
    <div class="dtitle"><h2>${esc(o.name)}</h2><div class="sub">${o.kind === "owner_llc" ? "Company / LLC" : "Owner as listed"}${o.managers.length ? " · managed by " + esc(o.managers.join(", ")) : ""}</div></div>
    <div class="stats" style="grid-template-columns:repeat(3,1fr)">
      <div><div class="k">Buildings</div><div class="v">${o.building_count}</div></div>
      <div><div class="k">Units listed</div><div class="v">${o.unit_count}</div></div>
      <div><div class="k">Phone</div><div class="v" style="font-size:13px">${esc(o.phones[0] || "—")}</div></div>
    </div>
    <div class="panel"><div class="ev">Owner names come from AppFolio's public rental search. An LLC's people can be looked up in the <a href="https://bizfilings.vermont.gov/online/BusinessInquire/" target="_blank" rel="noopener">Vermont business registry ${ICON.ext}</a>; for parcels, try the <a href="https://www.burlingtonvt.gov/Assessor" target="_blank" rel="noopener">Burlington assessor ${ICON.ext}</a>. Contact through the manager unless the owner lists their own number.</div></div>
    <section class="sect"><h3>Buildings</h3><div class="rows">
      ${o.buildings.map((b) => `<div><div><div style="font-weight:600">${esc((b.address || "").split(",")[0])}</div><div class="sub">${esc((b.address || "").split(",").slice(1).join(",").trim())}${b.units_listed ? ` · ${b.units_listed} unit${b.units_listed === 1 ? "" : "s"} on ShowMeTheRent` : ""}</div></div>
        <div style="display:flex;gap:6px">${b.unit_ids.map((uid) => `<a class="btn sm" href="#/explore/unit/${uid}">Unit #${uid}</a>`).join("")}${b.listing_url ? `<a class="btn quiet sm" href="${esc(b.listing_url)}" target="_blank" rel="noopener">${ICON.ext}</a>` : ""}</div></div>`).join("")}
    </div></section>
  </div>`;
  $("#dClose").addEventListener("click", () => closeDrawer());
}

// ============================================================ health
async function renderHealth() {
  $("#view").innerHTML = `
  <div class="page">
    <div class="phead"><div><h1>Health</h1><p id="hSummary">Loading…</p></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" data-job="scrape_due">Check all sources</button><button class="btn" data-job="owners">Refresh owners</button><button class="btn" data-job="geocode">Geocode</button><button class="btn" data-job="backup">Back up</button></div></div>
    <div id="hActive"></div>
    <div class="card" style="overflow-x:auto"><table class="t" id="hSources"></table></div>
    <details class="more"><summary>Recent jobs</summary><div class="card" style="overflow-x:auto;margin-top:10px"><table class="t" id="hJobs"></table></div></details>
    <div class="faint" style="font-size:12.5px">JSON: <a href="/api/health">/api/health</a> · <a href="/api/jobs">/api/jobs</a> · <a href="/api/units">/api/units</a> · <a href="/docs">API docs</a></div>
  </div>`;
  $$("[data-job]").forEach((b) => b.addEventListener("click", async () => {
    const r = await api.post("/api/jobs", { kind: b.dataset.job, params: b.dataset.job === "geocode" ? { retry_failed: true } : {} });
    toast(`Queued #${r.id}`); drawHealth();
  }));
  await drawHealth();
  state.timers.push(setInterval(drawHealth, 3000));
}
async function drawHealth() {
  if (!$("#hSources")) return;
  const [h, jobs] = await Promise.all([api.get("/api/health"), api.get("/api/jobs?limit=30")]);
  const enabled = h.sources.filter((s) => s.enabled);
  $("#hSummary").innerHTML = `<span class="${h.ok ? "st-ok" : "st-failing"}" style="font-weight:650">${h.ok ? "All sources healthy" : "Needs attention: " + esc(h.problem_sources.join(", "))}</span> · ${enabled.length} active sources · last backup ${h.last_backup ? ago(h.last_backup.finished_at) : "never"}`;
  $("#hActive").innerHTML = jobs.active.length ? `<div style="display:flex;flex-direction:column;gap:8px">${jobs.active.map((j) => `
    <div class="card" style="padding:14px 16px;display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b>${esc(j.kind)} ${esc(j.source_id || "")}</b>
        <span class="muted" style="font-size:12.5px">${j.status === "queued" ? "waiting" : `${j.percent == null ? "working" : j.percent + "%"}${j.total ? ` · ${j.done}/${j.total}` : ""}${j.eta_seconds != null ? ` · ~${dur(j.eta_seconds)} left` : ""}`}
        <button class="btn quiet sm" data-cancel="${j.id}">Cancel</button></span></div>
      ${j.status === "queued" ? "" : `<div class="bar ${j.percent == null ? "indet" : ""}"><span style="width:${j.percent ?? 0}%"></span></div><div class="faint" style="font-size:12px">${esc(j.step || "")}</div>`}
    </div>`).join("")}</div>` : "";
  $("#hSources").innerHTML = `<thead><tr><th>Source</th><th>Status</th><th>Listings</th><th>Last checked</th><th>Next</th><th></th></tr></thead><tbody>` +
    h.sources.map((s) => `<tr>
      <td><div style="font-weight:600">${esc(s.name)}</div><div class="faint" style="font-size:12px">${esc(s.platform)}${s.robots_override ? " · robots override" : ""}${s.reasons.length ? " · " + esc(s.reasons.join("; ")) : ""}</div></td>
      <td class="st-${s.state}" style="font-weight:600;white-space:nowrap">${esc(s.state.replace("_", " "))}</td>
      <td class="num">${s.last_count ?? "—"}</td>
      <td style="white-space:nowrap">${ago(s.last_success_at || s.last_run_at)}</td>
      <td style="white-space:nowrap" class="faint">${s.next_due_at ? fmtDate(s.next_due_at, { hour: "numeric", minute: "2-digit" }) : "—"}</td>
      <td style="white-space:nowrap;text-align:right"><button class="btn sm" data-run="${esc(s.source_id)}" ${s.enabled ? "" : "disabled"}>Run</button>
        <button class="btn quiet sm" data-toggle="${esc(s.source_id)}" data-en="${s.enabled ? 0 : 1}">${s.enabled ? "Pause" : "Enable"}</button></td>
    </tr>`).join("") + `</tbody>`;
  $("#hJobs").innerHTML = `<thead><tr><th>#</th><th>Job</th><th>Status</th><th>Result</th><th>Finished</th></tr></thead><tbody>` +
    jobs.jobs.map((j) => `<tr><td class="num faint">${j.id}</td><td>${esc(j.kind)} ${esc(j.source_id || "")}</td><td class="st-${j.status === "succeeded" ? "ok" : j.status}">${esc(j.status)}</td>
      <td class="faint" style="font-size:12px;max-width:420px">${esc(j.error || (j.result ? Object.entries(j.result).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k}: ${v}`).join(" · ") : ""))}</td><td class="faint">${ago(j.finished_at)}</td></tr>`).join("") + `</tbody>`;
  $$("[data-run]").forEach((b) => b.addEventListener("click", () => api.post("/api/jobs", { kind: "scrape", source_id: b.dataset.run }).then((r) => { toast(`Queued #${r.id}`); drawHealth(); })));
  $$("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
    const enabled = b.dataset.en === "1"; let reason = null;
    if (!enabled) { reason = prompt("Why pause this source? (kill switch)", "paused manually"); if (reason === null) return; }
    await api.post(`/api/sources/${b.dataset.toggle}/enabled`, { enabled, reason }); drawHealth(); refreshChrome();
  }));
  $$("[data-cancel]").forEach((b) => b.addEventListener("click", () => api.post(`/api/jobs/${b.dataset.cancel}/cancel`).then(drawHealth)));
}

// ============================================================ boot
(async function boot() {
  await loadSettings();
  refreshChrome(); setInterval(refreshChrome, 30000);
  await route();
})();
