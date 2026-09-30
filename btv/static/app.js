/* BTV Apartments — single-page UI over the JSON API. No build step. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString());
const fmtDate = (iso, opts = { month: "short", day: "numeric", year: "numeric" }) => {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return d.toLocaleDateString(undefined, opts);
};
const ago = (iso) => {
  if (!iso) return "never";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
const dur = (s) => (s == null ? "" : s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`);
const bedsLabel = (b) => (b == null ? "? bd" : b === 0 ? "Studio" : `${+b} bd`);
const store = {
  get(k, d) { try { const v = localStorage.getItem("btv." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("btv." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.append(t);
  setTimeout(() => t.remove(), 2200);
}
async function req(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { "content-type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    toast("Error: " + msg);
    throw new Error(msg);
  }
  return r.json();
}
const api = { get: (u) => req("GET", u), put: (u, b) => req("PUT", u, b), post: (u, b) => req("POST", u, b || {}), del: (u) => req("DELETE", u) };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const MATCH_LABEL = { exact: "On target", near: "Near miss", early: "Too early", late: "Too late", unknown: "Date unknown" };
const MATCH_COLOR = () => {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(["exact", "near", "early", "late", "unknown"].map((k) => [k, cs.getPropertyValue("--" + k).trim()]));
};
const TAG_PRESETS = ["w/d", "balcony", "roof access", "character", "quiet", "top floor", "parking", "yard"];
const MY_STATUSES = ["interested", "toured", "applied", "passed"];

function deltaText(a) {
  if (a.kind === "now") return "Available now";
  if (a.delta_days == null) return a.kind === "flexible" ? "Flexible" : "Unknown";
  const d = a.delta_days;
  if (d === 0) return "Exactly on target";
  return `${Math.abs(d)}d ${d < 0 ? "before" : "after"} target`;
}
function matchPill(a) {
  return `<span class="pill m-${a.match}" title="${esc(deltaText(a))}">${MATCH_LABEL[a.match] || a.match}${a.delta_days != null && a.kind !== "now" && a.match !== "exact" ? ` · ${a.delta_days > 0 ? "+" : ""}${a.delta_days}d` : ""}</span>`;
}
function stars(n) { return n ? "★".repeat(n) + "☆".repeat(5 - n) : ""; }

// ------------------------------------------------------------------ state
const state = {
  settings: null,
  units: [],
  filters: Object.assign({ q: "", match: "all", maxRent: "", minBeds: "", includeGone: false, tags: [], myStatus: "", city: "", sort: "match", hidePassed: true }, store.get("filters", {})),
  selectedUnit: null,
  map: null, markers: new Map(), markerLayer: null, fitted: false,
  healthTimer: null,
};

// ------------------------------------------------------------------ settings
async function loadSettings() {
  state.settings = await api.get("/api/settings");
  $("#targetDate").value = state.settings.target_move_in;
  $("#windowDays").value = state.settings.near_miss_days;
}
const saveSettings = debounce(async () => {
  const target_move_in = $("#targetDate").value, near_miss_days = Number($("#windowDays").value);
  if (!target_move_in) return;
  state.settings = await api.put("/api/settings", { target_move_in, near_miss_days });
  toast(`Target ${fmtDate(target_move_in)} ± ${near_miss_days}d`);
  if (currentRoute().view === "map") { await loadUnits(); renderResults(); }
  if (state.selectedUnit) openUnit(state.selectedUnit, true);
}, 500);
$("#targetDate").addEventListener("change", saveSettings);
$("#windowDays").addEventListener("input", saveSettings);

async function refreshHealthDot() {
  try {
    const h = await api.get("/api/health");
    $("#healthDot").className = "health-dot " + (h.ok ? "ok" : "bad");
    $("#healthDot").title = h.ok ? "All sources healthy" : "Problems: " + h.problem_sources.join(", ");
  } catch { $("#healthDot").className = "health-dot bad"; }
}

// ------------------------------------------------------------------ routing
function currentRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return { view: parts[0] || "map", sub: parts[1], id: parts[2] ? Number(parts[2]) : parts[1] && /^\d+$/.test(parts[1]) ? Number(parts[1]) : null };
}
let renderedView = null;
async function route() {
  const r = currentRoute();
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === r.view));
  if (state.healthTimer && r.view !== "health") { clearInterval(state.healthTimer); state.healthTimer = null; }
  if (renderedView !== r.view) {
    renderedView = r.view;
    if (r.view === "contacts") await renderContacts();
    else if (r.view === "health") await renderHealth();
    else await renderMapView();
  }
  if (r.view === "map" && r.sub === "unit" && r.id) openUnit(r.id);
  else if (r.view === "contacts" && r.id) openContact(r.id);
  else closeDrawer(false);
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ map view
async function loadUnits() {
  const data = await api.get(`/api/units?include_gone=${state.filters.includeGone}`);
  state.units = data.units;
}

function filteredUnits() {
  const f = state.filters;
  let us = state.units.filter((u) => {
    if (f.match === "target" && !["exact", "near"].includes(u.availability.match)) return false;
    if (f.match === "exact" && u.availability.match !== "exact") return false;
    if (f.maxRent && u.rent != null && u.rent > Number(f.maxRent)) return false;
    if (f.minBeds !== "" && (u.beds == null || u.beds < Number(f.minBeds))) return false;
    if (f.city && (u.city || "") !== f.city) return false;
    if (f.tags.length && !f.tags.every((t) => u.tags.includes(t))) return false;
    if (f.myStatus === "any" && !u.prefs.status && !u.prefs.rating) return false;
    if (f.myStatus && f.myStatus !== "any" && u.prefs.status !== f.myStatus) return false;
    if (f.hidePassed && u.prefs.status === "passed" && f.myStatus !== "passed") return false;
    if (f.q) {
      const hay = [u.address, u.title, u.unit, u.city, ...u.amenities, ...u.text_amenities.map((a) => a.tag), ...u.tags, ...u.sources.map((s) => s.source)].join(" ").toLowerCase();
      if (!f.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  });
  const order = { exact: 0, near: 1, late: 2, early: 3, unknown: 4 };
  const sorters = {
    match: (a, b) => order[a.availability.match] - order[b.availability.match] || Math.abs(a.availability.delta_days ?? 9999) - Math.abs(b.availability.delta_days ?? 9999) || (a.rent ?? 1e9) - (b.rent ?? 1e9),
    rent: (a, b) => (a.rent ?? 1e9) - (b.rent ?? 1e9),
    newest: (a, b) => Date.parse(b.first_seen) - Date.parse(a.first_seen),
    rating: (a, b) => (b.prefs.rating ?? 0) - (a.prefs.rating ?? 0) || order[a.availability.match] - order[b.availability.match],
  };
  return us.sort(sorters[f.sort] || sorters.match);
}

async function renderMapView() {
  closeDrawer(false);
  const f = state.filters;
  $("#view").innerHTML = `
  <div class="mapview" id="mapview">
    <div class="sidebar">
      <div class="filters">
        <div class="row"><input class="input grow" id="fQ" placeholder="Search address, amenity, source…" value="${esc(f.q)}"></div>
        <div class="row">
          <div class="seg" id="fMatch">
            <button data-v="all">All</button><button data-v="target">Near target</button><button data-v="exact">On target</button>
          </div>
          <select class="input" id="fSort" style="width:auto;margin-left:auto">
            <option value="match">Best match</option><option value="rent">Lowest rent</option><option value="newest">Newest</option><option value="rating">My rating</option>
          </select>
        </div>
        <div class="row">
          <input class="input" id="fRent" type="number" step="50" min="0" placeholder="Max rent" style="width:110px" value="${esc(f.maxRent)}">
          <select class="input" id="fBeds" style="width:98px">
            <option value="">Any beds</option><option value="0">Studio+</option><option value="1">1+ bd</option><option value="2">2+ bd</option><option value="3">3+ bd</option>
          </select>
          <select class="input grow" id="fCity"><option value="">All towns</option></select>
        </div>
        <div class="row">
          <select class="input" id="fMine" style="width:auto">
            <option value="">All units</option><option value="any">Rated / tracked</option>
            ${MY_STATUSES.map((s) => `<option value="${s}">${s[0].toUpperCase() + s.slice(1)}</option>`).join("")}
          </select>
          <label class="small"><input type="checkbox" id="fGone" ${f.includeGone ? "checked" : ""}> Show gone</label>
          <label class="small"><input type="checkbox" id="fPassed" ${f.hidePassed ? "checked" : ""}> Hide passed</label>
        </div>
        <div class="chips" id="fTags"></div>
      </div>
      <div class="resultbar"><span id="count"></span><span id="targetNote"></span></div>
      <div class="results" id="results"></div>
    </div>
    <div class="mapwrap">
      <div id="map"></div>
      <div class="legend card">
        <span><i style="background:var(--exact)"></i>On target</span><span><i style="background:var(--near)"></i>Near miss</span>
        <span><i style="background:var(--early)"></i>Early</span><span><i style="background:var(--late)"></i>Late</span><span><i style="background:var(--unknown)"></i>Unknown</span>
      </div>
    </div>
    <button class="btn primary maptoggle" id="mapToggle">Map</button>
  </div>`;
  $$("#fMatch button").forEach((b) => b.classList.toggle("on", b.dataset.v === f.match));
  $("#fSort").value = f.sort; $("#fBeds").value = f.minBeds; $("#fMine").value = f.myStatus;

  const upd = (k, v, reload) => { state.filters[k] = v; store.set("filters", state.filters); (reload ? loadUnits().then(renderResults) : renderResults()); };
  $("#fQ").addEventListener("input", debounce((e) => upd("q", e.target.value), 150));
  $$("#fMatch button").forEach((b) => b.addEventListener("click", () => { $$("#fMatch button").forEach((x) => x.classList.toggle("on", x === b)); upd("match", b.dataset.v); }));
  $("#fSort").addEventListener("change", (e) => upd("sort", e.target.value));
  $("#fRent").addEventListener("input", debounce((e) => upd("maxRent", e.target.value), 250));
  $("#fBeds").addEventListener("change", (e) => upd("minBeds", e.target.value));
  $("#fCity").addEventListener("change", (e) => upd("city", e.target.value));
  $("#fMine").addEventListener("change", (e) => upd("myStatus", e.target.value));
  $("#fGone").addEventListener("change", (e) => upd("includeGone", e.target.checked, true));
  $("#fPassed").addEventListener("change", (e) => upd("hidePassed", e.target.checked));
  $("#mapToggle").addEventListener("click", () => {
    const mv = $("#mapview"); mv.classList.toggle("show-map");
    $("#mapToggle").textContent = mv.classList.contains("show-map") ? "List" : "Map";
    state.map && setTimeout(() => state.map.invalidateSize(), 50);
  });

  initMap();
  await loadUnits();
  const cities = [...new Set(state.units.map((u) => u.city).filter(Boolean))].sort();
  $("#fCity").innerHTML = `<option value="">All towns</option>` + cities.map((c) => `<option ${c === f.city ? "selected" : ""}>${esc(c)}</option>`).join("");
  renderResults();
}

function renderTagFilter() {
  const all = [...new Set([...TAG_PRESETS, ...state.units.flatMap((u) => u.tags)])];
  const used = new Set(state.units.flatMap((u) => u.tags));
  const shown = all.filter((t) => used.has(t) || state.filters.tags.includes(t));
  $("#fTags").innerHTML = shown.length
    ? shown.map((t) => `<button class="chip ${state.filters.tags.includes(t) ? "on" : ""}" data-t="${esc(t)}">${esc(t)}</button>`).join("")
    : `<span class="faint" style="font-size:12px">Tag units (W/D, balcony, roof access…) in their detail panel to filter here.</span>`;
  $$("#fTags .chip").forEach((c) => c.addEventListener("click", () => {
    const t = c.dataset.t, tags = state.filters.tags;
    state.filters.tags = tags.includes(t) ? tags.filter((x) => x !== t) : [...tags, t];
    store.set("filters", state.filters); renderResults();
  }));
}

function renderResults() {
  if (!$("#results")) return;
  renderTagFilter();
  const us = filteredUnits();
  const onTarget = us.filter((u) => ["exact", "near"].includes(u.availability.match)).length;
  $("#count").textContent = `${us.length} unit${us.length === 1 ? "" : "s"}`;
  $("#targetNote").innerHTML = `<span class="pill m-exact">${onTarget}</span> near ${fmtDate(state.settings.target_move_in, { month: "short", day: "numeric" })}`;
  $("#results").innerHTML = us.length ? us.map(cardHTML).join("") : `<div class="empty">No units match these filters.</div>`;
  $$("#results .lcard").forEach((el) => {
    el.addEventListener("click", () => { location.hash = `#/map/unit/${el.dataset.id}`; });
    el.addEventListener("mouseenter", () => highlightMarker(Number(el.dataset.id), true));
    el.addEventListener("mouseleave", () => highlightMarker(Number(el.dataset.id), false));
  });
  renderMarkers(us);
}

function cardHTML(u) {
  const a = u.availability;
  const when = a.kind === "now" ? "Now" : a.effective_date ? fmtDate(a.effective_date) : "Unknown";
  return `<div class="lcard card ${state.selectedUnit === u.id ? "sel" : ""}" data-id="${u.id}">
    <div class="thumb" style="${u.photo ? `background-image:url('${esc(u.photo)}')` : ""}">${u.prefs.rating ? `<span class="stars">${"★".repeat(u.prefs.rating)}</span>` : ""}</div>
    <div style="min-width:0">
      <div class="top"><span class="rent num">${money(u.rent)}</span>${matchPill(a)}</div>
      <div class="addr ${u.status === "gone" ? "gone" : ""}" title="${esc(u.address)}">${esc((u.address || "").split(",")[0])}${u.unit ? ` <span class="muted">#${esc(u.unit)}</span>` : ""}</div>
      <div class="meta"><span>${bedsLabel(u.beds)} · ${u.baths ?? "?"} ba</span><span>${esc(u.city || "")}</span><span>${when}</span>
        ${u.status !== "available" ? `<span class="s-${u.status}">${u.status}</span>` : ""}${u.prefs.status ? `<span class="chip tag">${esc(u.prefs.status)}</span>` : ""}</div>
    </div>
  </div>`;
}

function initMap() {
  if (typeof L === "undefined") {
    $("#map").innerHTML = `<div class="empty">Map library failed to load. The list still works.</div>`;
    return;
  }
  if (state.map) { state.map.remove(); state.map = null; state.markers.clear(); state.fitted = false; }
  state.map = L.map("map", { zoomControl: true }).setView([44.4759, -73.2121], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(state.map);
  state.markerLayer = L.layerGroup().addTo(state.map);
}

function renderMarkers(units) {
  if (!state.map) return;
  const colors = MATCH_COLOR();
  state.markerLayer.clearLayers(); state.markers.clear();
  const pts = [];
  // Offset units sharing a building so every pin is clickable.
  const perBuilding = new Map();
  for (const u of units) {
    if (u.lat == null || u.lon == null) continue;
    const n = perBuilding.get(u.building_id) || 0; perBuilding.set(u.building_id, n + 1);
    const lat = u.lat + n * 0.00008, lon = u.lon + n * 0.00011;
    const html = `<b style="background:${colors[u.availability.match]}">${u.rent ? "$" + (u.rent >= 1000 ? (u.rent / 1000).toFixed(1).replace(/\.0$/, "") + "k" : u.rent) : "?"}</b>`;
    const icon = L.divIcon({ className: `pin ${u.prefs.rating >= 4 ? "fav" : ""} ${state.selectedUnit === u.id ? "sel" : ""}`, html, iconSize: [54, 22], iconAnchor: [27, 11] });
    const m = L.marker([lat, lon], { icon, riseOnHover: true, zIndexOffset: { exact: 400, near: 300, late: 100, early: 50, unknown: 0 }[u.availability.match] })
      .on("click", () => { location.hash = `#/map/unit/${u.id}`; })
      .bindTooltip(`${esc((u.address || "").split(",")[0])}${u.unit ? " #" + esc(u.unit) : ""} · ${bedsLabel(u.beds)} · ${deltaText(u.availability)}`, { direction: "top", offset: [0, -10] });
    m.addTo(state.markerLayer); state.markers.set(u.id, m); pts.push([lat, lon]);
  }
  if (!state.fitted && pts.length) {
    // Frame the dense core (around the median point) rather than far-flung towns.
    const med = (a) => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
    const mla = med(pts.map((p) => p[0])), mlo = med(pts.map((p) => p[1]));
    const core = pts.filter(([la, lo]) => Math.abs(la - mla) < 0.03 && Math.abs(lo - mlo) < 0.04);
    state.map.fitBounds(L.latLngBounds(core.length >= 3 ? core : pts).pad(0.1));
    state.fitted = true;
  }
}
function highlightMarker(id, on) {
  const m = state.markers.get(id); if (!m) return;
  const el = m.getElement(); if (el) el.classList.toggle("sel", on || state.selectedUnit === id);
  if (on) m.setZIndexOffset(1000); else m.setZIndexOffset(0);
}

// ------------------------------------------------------------------ drawer
function closeDrawer(updateHash = true) {
  const d = $("#drawer");
  d.classList.remove("open");
  state.selectedUnit = null;
  $$(".lcard.sel").forEach((e) => e.classList.remove("sel"));
  $$(".pin.sel").forEach((e) => e.classList.remove("sel"));
  if (updateHash) {
    const r = currentRoute();
    location.hash = r.view === "contacts" ? "#/contacts" : "#/map";
  }
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#drawer").classList.contains("open") && !$(".lightbox")) closeDrawer(); });

function lightbox(src) {
  const lb = document.createElement("div"); lb.className = "lightbox"; lb.innerHTML = `<img src="${esc(src)}" alt="">`;
  lb.addEventListener("click", () => lb.remove());
  document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { lb.remove(); document.removeEventListener("keydown", k); } });
  document.body.append(lb);
}

function sparkline(points) {
  if (points.length < 2) return "";
  const w = 460, h = 56, xs = points.map((p) => Date.parse(p.at)), ys = points.map((p) => p.rent);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (x) => (x1 === x0 ? w / 2 : 8 + ((x - x0) / (x1 - x0)) * (w - 16));
  const sy = (y) => (y1 === y0 ? h / 2 : h - 8 - ((y - y0) / (y1 - y0)) * (h - 16));
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/>${points.map((p, i) => `<circle cx="${sx(xs[i])}" cy="${sy(ys[i])}" r="3" fill="var(--accent)"><title>${fmtDate(p.at)}: ${money(p.rent)}</title></circle>`).join("")}</svg>`;
}

async function openUnit(id, force = false) {
  if (state.selectedUnit === id && !force && $("#drawer").classList.contains("open")) return;
  state.selectedUnit = id;
  $$(".lcard").forEach((e) => e.classList.toggle("sel", Number(e.dataset.id) === id));
  const d = $("#drawer");
  if (!d.classList.contains("open")) { d.innerHTML = `<div class="empty">Loading…</div>`; d.classList.add("open"); }
  let u;
  try { u = await api.get(`/api/units/${id}`); } catch { d.innerHTML = `<div class="empty">Unit not found.</div>`; return; }
  if (u.id !== id) { state.selectedUnit = u.id; history.replaceState(null, "", `#/map/unit/${u.id}`); }
  const m = state.markers.get(u.id);
  if (m && state.map) {
    // Centre the pin in the part of the map not covered by the drawer.
    const drawerW = window.innerWidth > 860 ? d.offsetWidth : 0;
    const pt = state.map.project(m.getLatLng()).add([drawerW / 2, 0]);
    state.map.panTo(state.map.unproject(pt), { animate: true });
    highlightMarker(u.id, true);
  }
  const a = u.availability;
  const allTags = [...new Set([...TAG_PRESETS, ...state.units.flatMap((x) => x.tags)])].filter((t) => !u.tags.includes(t));
  const detectedNotStructured = u.text_amenities.filter((t) => !u.amenities.some((s) => s.toLowerCase().includes(t.tag.split("/")[0])));

  d.innerHTML = `
  <div class="head">
    <div style="flex:1;min-width:0">
      <h2>${esc((u.address || "").split(",")[0])}${u.unit ? ` <span class="muted">#${esc(u.unit)}</span>` : ""}</h2>
      <div class="muted" style="font-size:13px">${esc(u.city || "")} · ${esc(u.title || "")}</div>
    </div>
    <button class="btn ghost" id="closeDrawer" title="Close (Esc)">✕</button>
  </div>
  <div class="body">
    ${u.photos.length ? `<div class="gallery">${u.photos.slice(0, 30).map((p) => `<img loading="lazy" src="${esc(p)}" alt="">`).join("")}</div>` : ""}
    <div class="facts">
      <div class="fact"><div class="k">Rent</div><div class="v num">${money(u.rent)}</div></div>
      <div class="fact"><div class="k">Beds / Baths</div><div class="v">${bedsLabel(u.beds)} · ${u.baths ?? "?"}</div></div>
      <div class="fact"><div class="k">Size</div><div class="v num">${u.sqft ? u.sqft + " ft²" : "—"}</div></div>
      <div class="fact"><div class="k">Deposit</div><div class="v num">${money(u.deposit)}</div></div>
    </div>

    <div class="avail">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <span class="big">${a.kind === "now" ? "Available now" : a.effective_date ? fmtDate(a.effective_date) : "Availability unknown"}</span>${matchPill(a)}
      </div>
      <div class="evidence">${esc(deltaText(a))} (target ${fmtDate(state.settings.target_move_in)})</div>
      ${a.text.raw ? `<div class="evidence">From description: <q>${esc(a.text.raw)}</q>${a.text.confidence != null ? ` · ${Math.round(a.text.confidence * 100)}% confidence` : ""}</div>` : ""}
      ${a.structured.raw ? `<div class="evidence">Listing field: <q>${esc(a.structured.raw)}</q>${a.from === "text" && a.note ? " · overridden by description" : ""}</div>` : ""}
      ${a.note && a.from !== "text" ? `<div class="evidence">${esc(a.note)}</div>` : ""}
    </div>

    <section class="mine">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
        <div class="stars-input" id="stars">${[1, 2, 3, 4, 5].map((i) => `<button data-v="${i}" class="${(u.prefs.rating || 0) >= i ? "on" : ""}" title="${i} star${i > 1 ? "s" : ""}">★</button>`).join("")}</div>
        <div class="seg" id="myStatus">${MY_STATUSES.map((s) => `<button data-v="${s}" class="${u.prefs.status === s ? "on" : ""}">${s}</button>`).join("")}</div>
      </div>
      <div class="chips" id="unitTags">
        ${u.tags.map((t) => `<span class="chip tag">${esc(t)}<button data-rm="${esc(t)}" title="Remove">✕</button></span>`).join("")}
        ${allTags.slice(0, 8).map((t) => `<button class="chip" data-add="${esc(t)}">+ ${esc(t)}</button>`).join("")}
        <input class="input" id="newTag" placeholder="+ tag" style="width:90px;padding:2px 8px;font-size:12px;border-radius:999px">
      </div>
      <textarea class="input" id="notes" rows="3" placeholder="My notes (autosaves)…">${esc(u.prefs.notes || "")}</textarea>
      <div class="faint" style="font-size:11.5px" id="notesState">Notes, rating and tags stay with this unit across relists and merges.</div>
    </section>

    <section>
      <h3>Amenities</h3>
      <div class="chips">
        ${u.amenities.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}
        ${detectedNotStructured.map((x) => `<span class="chip detected" title="Mentioned in description: “${esc(x.evidence)}”">${esc(x.tag)}</span>`).join("")}
        ${!u.amenities.length && !detectedNotStructured.length ? `<span class="muted">None listed.</span>` : ""}
      </div>
      <div class="faint" style="font-size:11.5px;margin-top:6px">Dashed = mentioned in the description (hover for the quote). Not verified.</div>
    </section>

    <section>
      <h3>Details</h3>
      <dl class="kv">
        <dt>Pets</dt><dd>${esc(u.pets || "—")}</dd>
        <dt>Parking</dt><dd>${esc(u.parking || "—")}</dd>
        <dt>Utilities incl.</dt><dd>${esc(u.utilities || "—")}</dd>
        <dt>Status</dt><dd class="s-${u.status}">${esc(u.status)}</dd>
        <dt>Last verified</dt><dd>${ago(u.last_verified)} <span class="faint">(${fmtDate(u.last_verified, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })})</span></dd>
        <dt>First seen</dt><dd>${fmtDate(u.first_seen)}</dd>
      </dl>
    </section>

    ${u.description ? `<section><h3>Description</h3><div class="desc" id="desc">${esc(u.description)}</div><button class="btn small ghost" id="descMore">Show more</button></section>` : ""}

    <section>
      <h3>Sources (${u.listing_details.length})</h3>
      ${u.listing_details.map((l) => `<div class="srcrow">
          <div><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(u.sources.find((s) => s.source_listing_id === l.source_listing_id)?.source || l.source_id)} ↗</a>
          <div class="faint" style="font-size:12px">seen ${ago(l.last_seen)} · <span class="s-${l.status}">${l.status}</span>${l.link_locked ? " · manually linked" : ""}</div></div>
          ${u.listing_details.length > 1 ? `<button class="btn small" data-unlink="${l.source_listing_id}" title="This listing is a different unit">Split off</button>` : ""}
        </div>`).join("")}
    </section>

    ${u.contacts.length ? `<section><h3>Contacts</h3><div style="display:flex;flex-direction:column;gap:6px">${u.contacts.map((c) => `
      <a class="card ccard" href="#/contacts/${c.id}" style="color:inherit;text-decoration:none">
        <div><div style="font-weight:600">${esc(c.name)}</div><div class="muted" style="font-size:12px">${esc(c.company || "")}</div></div>
        <div style="text-align:right;font-size:12.5px">${c.phones.map((p) => `<div>${esc(p)}</div>`).join("")}${c.emails.map((e) => `<div>${esc(e)}</div>`).join("")}</div>
      </a>`).join("")}</div></section>` : ""}

    <section>
      <h3>Price history</h3>
      ${u.price_history.length > 1 ? sparkline(u.price_history) : ""}
      <ul class="timeline">${u.price_history.map((p) => `<li><span class="when">${fmtDate(p.at)}</span><span class="num">${money(p.rent)}</span></li>`).join("") || `<li class="muted">No history yet.</li>`}</ul>
    </section>

    <section>
      <h3>Timeline</h3>
      <ul class="timeline">${u.status_events.map((e) => `<li><span class="when">${fmtDate(e.at)}</span><span><span class="s-${e.to}">${esc(e.to)}</span> <span class="faint">${esc(e.reason || "")}</span></span></li>`).join("")}</ul>
      ${u.turnover.predicted_next_available ? `<div class="evidence" style="margin-top:8px">Predicted next turnover: <b>${fmtDate(u.turnover.predicted_next_available)}</b> <span class="faint">(${esc(u.turnover.basis)})</span></div>` : ""}
    </section>

    <section>
      <h3>Dedupe</h3>
      <div class="row" style="display:flex;gap:6px;align-items:center">
        <input class="input" id="mergeId" type="number" placeholder="Other unit #" style="width:130px">
        <button class="btn small" id="mergeBtn">Merge into this unit</button>
        <span class="faint" style="font-size:12px">Unit #${u.id}</span>
      </div>
      ${u.building && u.building.other_units.length ? `<div class="faint" style="font-size:12px;margin-top:6px">Same building: ${u.building.other_units.map((x) => `<a href="#/map/unit/${x}">#${x}</a>`).join(", ")}</div>` : ""}
      ${u.merge_log.length ? `<ul class="timeline" style="margin-top:8px">${u.merge_log.map((m) => `<li><span class="when">${fmtDate(m.at)}</span><span style="flex:1">${esc(m.action.replace("_", " "))} <span class="faint">${esc(m.actor)}${m.reason ? " · " + esc(m.reason) : ""}</span></span>${m.undone_at ? `<span class="faint">undone</span>` : m.actor === "user" || (m.action === "link_listing" && m.subject.from_unit_id != null) ? `<button class="btn small ghost" data-undo="${m.id}">Undo</button>` : ""}</li>`).join("")}</ul>` : ""}
    </section>
  </div>`;

  $("#closeDrawer").addEventListener("click", () => closeDrawer());
  $$(".gallery img", d).forEach((img) => img.addEventListener("click", () => lightbox(img.src)));
  const desc = $("#desc");
  if (desc) {
    const more = $("#descMore");
    if (desc.scrollHeight <= 262) { desc.classList.add("expanded"); more.remove(); }
    else more.addEventListener("click", () => { desc.classList.toggle("expanded"); more.textContent = desc.classList.contains("expanded") ? "Show less" : "Show more"; });
  }
  const refreshCard = async () => { await loadUnits(); renderResults(); };
  $$("#stars button").forEach((b) => b.addEventListener("click", async () => {
    const v = Number(b.dataset.v), rating = u.prefs.rating === v ? null : v;
    await api.put(`/api/units/${u.id}/prefs`, { rating }); u.prefs.rating = rating;
    $$("#stars button").forEach((x) => x.classList.toggle("on", rating != null && Number(x.dataset.v) <= rating)); refreshCard();
  }));
  $$("#myStatus button").forEach((b) => b.addEventListener("click", async () => {
    const status = u.prefs.status === b.dataset.v ? null : b.dataset.v;
    await api.put(`/api/units/${u.id}/prefs`, { status }); u.prefs.status = status;
    $$("#myStatus button").forEach((x) => x.classList.toggle("on", x.dataset.v === status)); refreshCard();
  }));
  const saveNotes = debounce(async (v) => { await api.put(`/api/units/${u.id}/prefs`, { notes: v }); $("#notesState").textContent = "Saved " + new Date().toLocaleTimeString(); }, 600);
  $("#notes").addEventListener("input", (e) => { $("#notesState").textContent = "Saving…"; saveNotes(e.target.value); });
  const addTag = async (t) => { if (!t.trim()) return; await api.post(`/api/units/${u.id}/tags`, { tag: t }); await refreshCard(); openUnit(u.id, true); };
  $$("#unitTags [data-add]").forEach((b) => b.addEventListener("click", () => addTag(b.dataset.add)));
  $$("#unitTags [data-rm]").forEach((b) => b.addEventListener("click", async () => { await api.del(`/api/units/${u.id}/tags/${encodeURIComponent(b.dataset.rm)}`); await refreshCard(); openUnit(u.id, true); }));
  $("#newTag").addEventListener("keydown", (e) => { if (e.key === "Enter") addTag(e.target.value); });
  $("#mergeBtn").addEventListener("click", async () => {
    const other = Number($("#mergeId").value); if (!other) return;
    if (!confirm(`Merge unit #${other} into #${u.id}? Its listings, notes and tags move here. You can undo this.`)) return;
    await api.post("/api/units/merge", { into_id: u.id, merged_id: other, reason: "same unit (manual)" });
    toast("Merged"); await refreshCard(); openUnit(u.id, true);
  });
  $$("[data-unlink]", d).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Split this listing into its own unit?")) return;
    const r = await api.post(`/api/listings/${b.dataset.unlink}/unlink`, { reason: "different unit (manual)" });
    toast("Split off as unit #" + r.unit_id); await refreshCard(); openUnit(u.id, true);
  }));
  $$("[data-undo]", d).forEach((b) => b.addEventListener("click", async () => {
    await api.post(`/api/merges/${b.dataset.undo}/undo`); toast("Undone"); await refreshCard(); openUnit(u.id, true);
  }));
}

// ------------------------------------------------------------------ contacts
async function renderContacts() {
  $("#view").innerHTML = `<div class="page"><div class="empty">Loading…</div></div>`;
  const [contacts, companies] = await Promise.all([api.get("/api/contacts"), api.get("/api/companies")]);
  const f = store.get("contactFilter", { multi: false, q: "" });
  $("#view").innerHTML = `
  <div class="page">
    <div class="page-head">
      <div><h1>Landlords & contacts</h1><div class="muted">Who is behind each listing — reach out before units hit the public feeds.</div></div>
      <div style="display:flex;gap:8px;align-items:center">
        <input class="input" id="cQ" placeholder="Search…" style="width:200px" value="${esc(f.q)}">
        <label class="small muted" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="cMulti" ${f.multi ? "checked" : ""}> Multi-property only</label>
      </div>
    </div>
    <div class="companies">${companies.map((c) => `
      <div class="card company">
        <div class="n">${esc(c.name)}</div>
        <div class="muted" style="font-size:12px">${esc(c.kind || "")}${c.website ? ` · <a href="${esc(c.website)}" target="_blank" rel="noopener">site ↗</a>` : ""}</div>
        <div class="stats">
          <div><span class="big num">${c.unit_count}</span> <span class="muted" style="font-size:12px">units</span></div>
          <div class="range">${c.rent_min ? money(c.rent_min) + " – " + money(c.rent_max) : "—"}</div>
        </div>
      </div>`).join("")}</div>
    <div class="card table-wrap">
      <table class="t" id="ctable">
        <thead><tr><th>Contact</th><th>Company</th><th>Phone / email</th><th>Units</th><th>Buildings</th><th>Rent range</th><th>Last seen</th><th>Outreach</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>`;
  const draw = () => {
    const q = $("#cQ").value.toLowerCase(), multi = $("#cMulti").checked;
    store.set("contactFilter", { q, multi });
    const rows = contacts.filter((c) => (!multi || c.multi_property) && (!q || [c.name, c.company, ...c.phones, ...c.emails, c.notes].join(" ").toLowerCase().includes(q)));
    $("#ctable tbody").innerHTML = rows.map((c) => `<tr class="click" data-id="${c.id}">
      <td><b>${esc(c.name)}</b>${c.multi_property ? ` <span class="badge-multi">● multi-property</span>` : ""}${c.needs_review ? ` <span class="pill m-near">review</span>` : ""}</td>
      <td>${esc(c.company || "")}</td>
      <td>${c.phones.map(esc).join("<br>")}${c.phones.length && c.emails.length ? "<br>" : ""}<span class="muted">${c.emails.map(esc).join("<br>")}</span></td>
      <td class="num" style="white-space:nowrap">${c.unit_count}${c.active_units ? ` <span class="faint">· ${c.active_units} active</span>` : ""}</td>
      <td class="num">${c.building_count}</td>
      <td class="num">${c.rent_min ? money(c.rent_min) + "–" + money(c.rent_max) : "—"}</td>
      <td>${ago(c.last_seen)}</td>
      <td class="num">${c.outreach_count || ""}</td>
    </tr>`).join("") || `<tr><td colspan="8" class="empty">No contacts.</td></tr>`;
    $$("#ctable tr.click").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/contacts/${tr.dataset.id}`; }));
  };
  $("#cQ").addEventListener("input", debounce(draw, 120));
  $("#cMulti").addEventListener("change", draw);
  draw();
}

async function openContact(id) {
  const d = $("#drawer");
  d.innerHTML = `<div class="empty">Loading…</div>`; d.classList.add("open");
  const c = await api.get(`/api/contacts/${id}`);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const mx = Math.max(1, ...Object.values(c.stats.listing_months));
  d.innerHTML = `
  <div class="head">
    <div style="flex:1;min-width:0"><h2>${esc(c.name)}</h2><div class="muted" style="font-size:13px">${esc(c.company || "")}${c.role ? " · " + esc(c.role) : ""}</div></div>
    <button class="btn ghost" id="closeDrawer">✕</button>
  </div>
  <div class="body">
    <div class="facts">
      <div class="fact"><div class="k">Units</div><div class="v num">${c.stats.unit_count}</div></div>
      <div class="fact"><div class="k">Buildings</div><div class="v num">${c.stats.building_count}</div></div>
      <div class="fact" style="grid-column:span 2"><div class="k">Rent range</div><div class="v num">${c.stats.rent_min ? money(c.stats.rent_min) + " – " + money(c.stats.rent_max) : "—"}</div></div>
    </div>
    <section><h3>Reach</h3><dl class="kv">
      <dt>Phone</dt><dd>${c.phones.map((p) => `<a href="tel:${esc(p.replace(/[^\d+]/g, ""))}">${esc(p)}</a>`).join("<br>") || "—"}</dd>
      <dt>Email</dt><dd>${c.emails.map((e) => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join("<br>") || "—"}</dd>
      <dt>Name variants</dt><dd>${esc(c.name_variants.join(", ") || "—")}</dd>
      <dt>Sources</dt><dd>${esc(c.stats.sources.join(", "))}</dd>
    </dl></section>
    <section><h3>When their units get listed</h3>
      <div class="months">${months.map((m) => `<div style="height:${((c.stats.listing_months[m] || 0) / mx) * 100}%" title="${m}: ${c.stats.listing_months[m] || 0}"></div>`).join("")}</div>
      <div class="months-l">${months.map((m) => `<span>${m[0]}</span>`).join("")}</div>
    </section>
    ${c.merge_suggestions.length ? `<section><h3>Possible duplicates</h3>${c.merge_suggestions.map((s) => `
      <div class="srcrow"><span>“${esc(s.name)}” may be <a href="#/contacts/${s.suggested.id}">${esc(s.suggested.name)}</a></span>
      <span><button class="btn small" data-review="${s.mention_id}" data-d="merge">Same person</button> <button class="btn small ghost" data-review="${s.mention_id}" data-d="keep">Different</button></span></div>`).join("")}</section>` : ""}
    <section><h3>Log outreach</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        <select class="input" id="oChan"><option value="email">Email</option><option value="call">Call</option><option value="text">Text</option><option value="in_person">In person</option></select>
        <select class="input" id="oDir"><option value="out">I reached out</option><option value="in">They replied</option></select>
        <input class="input" id="oSum" placeholder="What did you ask / hear?" style="grid-column:span 2">
        <input class="input" id="oOut" placeholder="Outcome (optional)">
        <input class="input" id="oFollow" type="date" title="Follow up on">
      </div>
      <button class="btn primary" id="oSave" style="margin-top:8px">Add to log</button>
      <ul class="timeline" style="margin-top:10px">${c.outreach.map((o) => `<li><span class="when">${fmtDate(o.at)}</span><span><b>${esc(o.channel)}</b> ${o.direction === "in" ? "← reply" : "→"} ${esc(o.summary || "")}${o.outcome ? ` <span class="muted">· ${esc(o.outcome)}</span>` : ""}${o.follow_up_on ? ` <span class="pill m-near">follow up ${fmtDate(o.follow_up_on, { month: "short", day: "numeric" })}</span>` : ""}</span></li>`).join("") || `<li class="muted">No outreach yet.</li>`}</ul>
    </section>
    <section><h3>Same person as…</h3>
      <div style="display:flex;gap:6px"><select class="input" id="mergeContact"><option value="">Choose a contact to merge into this one</option></select>
      <button class="btn" id="mergeContactBtn">Merge</button></div>
      <div class="faint" style="font-size:11.5px;margin-top:4px">Moves their phones, emails, names, listings and outreach here. Undo from the API if needed.</div>
    </section>
    <section><h3>Notes</h3><textarea class="input" id="cNotes" rows="3" placeholder="Notes about this landlord…">${esc(c.notes || "")}</textarea><div class="faint" id="cNotesState" style="font-size:11.5px"></div></section>
    <section><h3>Units (${c.units.length})</h3><div style="display:flex;flex-direction:column;gap:6px">
      ${c.units.map((u) => `<a class="card ccard" href="#/map/unit/${u.id}" style="color:inherit;text-decoration:none">
        <div style="min-width:0"><div style="font-weight:600">${esc((u.address || "").split(",")[0])}${u.unit ? " #" + esc(u.unit) : ""}</div>
        <div class="muted" style="font-size:12px">${esc(u.city || "")} · ${bedsLabel(u.beds)} · <span class="s-${u.status}">${u.status}</span></div></div>
        <div style="text-align:right"><div class="num" style="font-weight:600">${money(u.rent)}</div>${matchPill(u.availability)}</div></a>`).join("")}
    </div></section>
  </div>`;
  $("#closeDrawer").addEventListener("click", () => closeDrawer());
  $("#oSave").addEventListener("click", async () => {
    await api.post(`/api/contacts/${c.id}/outreach`, { channel: $("#oChan").value, direction: $("#oDir").value, summary: $("#oSum").value || null, outcome: $("#oOut").value || null, follow_up_on: $("#oFollow").value || null });
    toast("Logged"); openContact(c.id);
  });
  const saveNotes = debounce(async (v) => { await api.put(`/api/contacts/${c.id}`, { notes: v }); $("#cNotesState").textContent = "Saved"; }, 600);
  $("#cNotes").addEventListener("input", (e) => { $("#cNotesState").textContent = "Saving…"; saveNotes(e.target.value); });
  $$("[data-review]", d).forEach((b) => b.addEventListener("click", async () => {
    await api.post(`/api/mentions/${b.dataset.review}/review`, { decision: b.dataset.d }); toast("Saved"); openContact(c.id);
  }));
  api.get("/api/contacts").then((all) => {
    const others = all.filter((x) => x.id !== c.id).sort((a, b) => (b.company === c.company) - (a.company === c.company) || a.name.localeCompare(b.name));
    $("#mergeContact").innerHTML += others.map((x) => `<option value="${x.id}">${esc(x.name)} — ${esc(x.company || "")}${x.phones[0] ? " · " + esc(x.phones[0]) : x.emails[0] ? " · " + esc(x.emails[0]) : ""}</option>`).join("");
  });
  $("#mergeContactBtn").addEventListener("click", async () => {
    const other = Number($("#mergeContact").value); if (!other) return;
    if (!confirm("Merge that contact into this one?")) return;
    await api.post("/api/contacts/merge", { into_id: c.id, merged_id: other, reason: "same person (manual)" });
    toast("Merged"); renderedView = null; await route();
  });
}

// ------------------------------------------------------------------ health
async function renderHealth() {
  $("#view").innerHTML = `
  <div class="page">
    <div class="page-head">
      <div><h1>Health</h1><div class="muted" id="hSummary">Loading…</div></div>
      <div style="display:flex;gap:8px"><button class="btn" id="runAll">Scrape all due</button><button class="btn" id="runGeo">Geocode</button><button class="btn" id="runBackup">Back up now</button></div>
    </div>
    <div><h3 class="muted" style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Active jobs</h3><div id="hActive"></div></div>
    <div class="card table-wrap"><table class="t" id="hSources"></table></div>
    <div class="card table-wrap"><table class="t" id="hJobs"></table></div>
    <div class="muted" style="font-size:12px">JSON: <a href="/api/health">/api/health</a> · <a href="/api/jobs">/api/jobs</a> · <a href="/api/units">/api/units</a> · <a href="/docs">API docs</a></div>
  </div>`;
  const job = (kind, body = {}) => api.post("/api/jobs", { kind, ...body }).then((r) => { toast(`Queued job #${r.id}`); drawHealth(); });
  $("#runAll").addEventListener("click", () => job("scrape_due"));
  $("#runGeo").addEventListener("click", () => job("geocode", { params: { retry_failed: true } }));
  $("#runBackup").addEventListener("click", () => job("backup"));
  await drawHealth();
  state.healthTimer = setInterval(drawHealth, 3000);
}

async function drawHealth() {
  if (!$("#hSources")) return;
  const [h, jobs] = await Promise.all([api.get("/api/health"), api.get("/api/jobs?limit=25")]);
  $("#hSummary").innerHTML = `<span class="${h.ok ? "st-ok" : "st-failing"}" style="font-weight:600">${h.ok ? "All sources healthy" : "Attention needed: " + esc(h.problem_sources.join(", "))}</span> · last backup ${h.last_backup ? ago(h.last_backup.finished_at) + " (" + esc(h.last_backup.status) + ")" : "never"}`;
  $("#hActive").innerHTML = jobs.active.length ? jobs.active.map((j) => `
    <div class="card jobcard" style="margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;gap:8px"><b>#${j.id} ${esc(j.kind)} ${esc(j.source_id || "")}</b><span class="muted">${esc(j.status)}${j.elapsed_seconds != null ? " · " + dur(j.elapsed_seconds) : ""}${j.eta_seconds != null ? " · ~" + dur(j.eta_seconds) + " left" : ""}</span></div>
      <div class="bar ${j.percent == null ? "indet" : ""}"><span style="width:${j.percent ?? 0}%"></span></div>
      <div class="muted" style="font-size:12.5px">${j.percent == null ? "working" : j.percent + "%"}${j.total ? ` (${j.done}/${j.total})` : ""} · ${esc(j.step || "")}
        <button class="btn small ghost" data-cancel="${j.id}" style="float:right">Cancel</button></div>
    </div>`).join("") : `<div class="muted">Nothing running.</div>`;
  $("#hSources").innerHTML = `<thead><tr><th>Source</th><th>State</th><th>Last run</th><th>Last success</th><th>Count</th><th>Next due</th><th>Notes</th><th></th></tr></thead><tbody>` +
    h.sources.map((s) => `<tr>
      <td><b>${esc(s.name)}</b><div class="faint" style="font-size:12px">${esc(s.source_id)} · ${esc(s.platform)}${s.robots_override ? " · robots override" : ""}</div></td>
      <td class="st-${s.state}" style="font-weight:600">${esc(s.state.replace("_", " "))}</td>
      <td>${ago(s.last_run_at)}</td><td>${ago(s.last_success_at)}</td>
      <td class="num">${s.last_count ?? ""}${s.expected_min ? ` <span class="faint">≥${s.expected_min}</span>` : ""}</td>
      <td>${s.next_due_at ? fmtDate(s.next_due_at, { hour: "numeric", minute: "2-digit" }) : "—"}</td>
      <td class="muted" style="max-width:260px">${esc(s.reasons.join("; "))}</td>
      <td style="white-space:nowrap"><button class="btn small" data-run="${esc(s.source_id)}" ${s.enabled ? "" : "disabled"}>Run</button>
        <button class="btn small ghost" data-toggle="${esc(s.source_id)}" data-en="${s.enabled ? 0 : 1}">${s.enabled ? "Disable" : "Enable"}</button></td>
    </tr>`).join("") + `</tbody>`;
  $("#hJobs").innerHTML = `<thead><tr><th>#</th><th>Job</th><th>Status</th><th>Progress</th><th>Result</th><th>Finished</th></tr></thead><tbody>` +
    jobs.jobs.map((j) => `<tr><td class="num">${j.id}</td><td>${esc(j.kind)} ${esc(j.source_id || "")}</td><td class="st-${j.status === "succeeded" ? "ok" : j.status}">${esc(j.status)}</td>
      <td class="num">${j.percent != null ? j.percent + "%" : ""}</td>
      <td class="muted" style="max-width:380px;font-size:12px">${esc(j.error || (j.result ? Object.entries(j.result).filter(([k, v]) => typeof v !== "object").map(([k, v]) => `${k}: ${v}`).join(" · ") : ""))}</td>
      <td>${ago(j.finished_at)}</td></tr>`).join("") + `</tbody>`;
  $$("[data-run]").forEach((b) => b.addEventListener("click", () => api.post("/api/jobs", { kind: "scrape", source_id: b.dataset.run }).then((r) => { toast(`Queued #${r.id}`); drawHealth(); })));
  $$("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
    const enabled = b.dataset.en === "1"; let reason = null;
    if (!enabled) { reason = prompt("Reason for disabling (kill switch)?", "disabled manually"); if (reason === null) return; }
    await api.post(`/api/sources/${b.dataset.toggle}/enabled`, { enabled, reason }); drawHealth(); refreshHealthDot();
  }));
  $$("[data-cancel]").forEach((b) => b.addEventListener("click", () => api.post(`/api/jobs/${b.dataset.cancel}/cancel`).then(drawHealth)));
}

// ------------------------------------------------------------------ boot
(async function boot() {
  await loadSettings();
  refreshHealthDot(); setInterval(refreshHealthDot, 30000);
  await route();
})();
