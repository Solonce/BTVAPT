// Faceless Studio UI (vanilla JS)
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = opts.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}
const toast = (msg) => alert(msg);

// ---- Settings metadata ------------------------------------------------------
const GROUPS = [
  ["Output", [
    ["vertical", "bool", "Render 9:16 (Shorts/TikTok/Reels)"],
    ["horizontal", "bool", "Render 16:9 (YouTube)"],
    ["target_seconds", "int", "Default length in seconds"],
    ["resolution", "int", "Short side in px (720 or 1080)"],
    ["fps", "int", "Frames per second"],
    ["output_dir", "str", "Finished videos land here (relative to the app folder or absolute)"],
    ["format_weights", "weights:formats", "How often autopilot picks each format (0 = never)"],
  ]],
  ["Writing", [
    ["llm_provider", ["claude", "featherless"], "Who writes topics & scripts. Featherless = flat-rate open models (no web trend research)"],
    ["featherless_model", "str", "Featherless model id, e.g. deepseek-ai/DeepSeek-V3-0324, moonshotai/Kimi-K2-Instruct-0905, zai-org/GLM-4.7, Qwen/Qwen3-14B (smaller plans)"],
    ["featherless_vision_model", "str", "Featherless model that can see images (Narrate my footage), e.g. Qwen/Qwen3-VL-30B-A3B-Instruct"],
    ["featherless_base_url", "str", "Any OpenAI-compatible endpoint works here"],
    ["llm_model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"], "Claude model (when the writer is claude)"],
    ["llm_effort", ["low", "medium", "high", "xhigh", "max"], "Claude thinking effort"],
    ["trend_research", "bool", "Claude only: search the web for trending faceless videos when refilling topics"],
    ["extra_script_notes", "text", "Standing direction added to every script"],
  ]],
  ["Voice", [
    ["tts_provider", ["edge", "elevenlabs"], "edge = free Microsoft neural voices; elevenlabs = premium"],
    ["edge_voice", "str", "e.g. en-US-AndrewMultilingualNeural (warm storyteller), en-US-BrianMultilingualNeural, en-US-AvaMultilingualNeural, en-GB-RyanNeural"],
    ["edge_rate", "str", "Speaking speed, e.g. +5%"],
    ["max_pause", "float", "Longest silence kept between words, in seconds (removes choppy gaps)"],
    ["elevenlabs_voice_id", "str", "ElevenLabs voice id"],
    ["elevenlabs_model", "str", "ElevenLabs model id"],
  ]],
  ["Visuals", [
    ["image_provider", ["auto", "pollinations", "fal"], "auto = Flux on fal.ai when a fal key is set (much sharper, ~$0.008/img), otherwise the free service"],
    ["art_style", ["anime", "cartoon", "comic", "3d_cartoon", "storybook", "cinematic", "format"], "Look of every image. anime and 3d_cartoon work best on the free image service; cartoon and comic look best with fal (Flux)"],
    ["fal_image_model", "str", "fal.ai image model id"],
    ["video_provider", ["none", "fal"], "AI video clips for the most dramatic beats"],
    ["fal_video_model", "str", "fal.ai image-to-video model id"],
    ["max_ai_video_clips", "int", "AI video clips per video (most expensive part)"],
    ["visual_mix", "weights:mix", "Where footage comes from when the script doesn't say"],
    ["visual_style", "str", "Custom style text; overrides art style when filled in"],
    ["motion_intensity", "float", "Pan/zoom strength: 0 static, 1 normal, 2 aggressive"],
    ["max_shot_seconds", "float", "Longest a single shot stays on screen"],
  ]],
  ["Media server (your local vidsrc)", [
    ["mediaserver_enabled", "bool", "Use clips from your media server for movie formats"],
    ["mediaserver_base", "str", "Base URL, e.g. http://vidsrc.xyz"],
    ["mediaserver_movie_path", "str", "Embed path. Placeholders: {tmdb} {imdb} {title} {year}"],
    ["clip_seconds_min", "float", "Minimum clip length"],
  ]],
  ["Captions & music", [
    ["captions", "bool", "Burn in captions"],
    ["caption_style", ["pop", "subtle"], "pop = big word-by-word highlight"],
    ["caption_words", "int", "Words on screen at once (pop)"],
    ["caption_font", "str", "Font family installed on the box"],
    ["caption_color", "color", "Text colour"],
    ["caption_highlight", "color", "Active word colour"],
    ["music", "bool", "Background music (from Library or data/music)"],
    ["music_volume", "float", "Music level before ducking (0-1)"],
  ]],
  ["Autopilot & budget", [
    ["autopilot", "bool", "Make videos on its own"],
    ["videos_per_day", "int", "Spread evenly through the day"],
    ["topic_pool_min", "int", "Refill topics when fewer than this remain"],
    ["require_approval", "bool", "Pause after the script so you can edit/approve"],
    ["monthly_budget_usd", "float", "Hard monthly cap across all paid services"],
    ["per_video_max_usd", "float", "Paid sources are skipped beyond this per video"],
  ]],
  ["API keys", [
    ["anthropic_api_key", "secret", "Claude (needed when the writer is claude)"],
    ["featherless_api_key", "secret", "Featherless (needed when the writer is featherless)"],
    ["elevenlabs_api_key", "secret", "Optional: premium voices"],
    ["fal_key", "secret", "Optional: Flux images + AI video"],
    ["pexels_api_key", "secret", "Optional, free: stock footage"],
    ["youtube_api_key", "secret", "Optional, free: trending title research"],
    ["tmdb_api_key", "secret", "Optional, free: movie ids for the media server"],
  ]],
];

let SETTINGS = {}, FORMATS = {}, PRESETS = {};

function fieldHTML(key, type, help) {
  const v = SETTINGS[key];
  let input;
  if (Array.isArray(type)) {
    input = `<select data-k="${key}">${type.map((o) => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("")}</select>`;
  } else if (type === "bool") {
    return `<label class="switch"><input type="checkbox" data-k="${key}" data-t="bool" ${v ? "checked" : ""}> ${key.replaceAll("_", " ")}</label><div class="help">${esc(help)}</div>`;
  } else if (type === "int" || type === "float") {
    input = `<input type="number" step="${type === "int" ? 1 : 0.01}" data-k="${key}" data-t="${type}" value="${esc(v)}">`;
  } else if (type === "text") {
    input = `<textarea rows="2" data-k="${key}">${esc(v)}</textarea>`;
  } else if (type === "color") {
    input = `<input type="color" data-k="${key}" value="${esc(v)}">`;
  } else if (type === "secret") {
    input = `<input type="password" autocomplete="off" data-k="${key}" data-t="secret" placeholder="${v ? "saved (leave blank to keep)" : "not set"}">`;
  } else if (type.startsWith("weights:")) {
    const keys = type === "weights:formats" ? Object.keys(FORMATS).filter((k) => !FORMATS[k].uses_source_video) : ["ai_image", "stock", "library"];
    const cur = v || {};
    input = `<div class="row wrap" data-k="${key}" data-t="weights">${keys.map((k) =>
      `<label style="min-width:110px">${esc(k)}<input type="number" step="0.1" min="0" data-w="${k}" value="${cur[k] ?? 0}"></label>`).join("")}</div>`;
    return `<label style="grid-column:1/-1">${key.replaceAll("_", " ")}${input}<span class="help">${esc(help)}</span></label>`;
  } else {
    input = `<input data-k="${key}" value="${esc(v)}">`;
  }
  return `<label>${key.replaceAll("_", " ")}${input}<span class="help">${esc(help)}</span></label>`;
}

function renderSettings() {
  $("#settings-form").innerHTML = GROUPS.map(([title, fields]) =>
    `<div class="settings-group"><h3>${esc(title)}</h3><div class="fields">${fields.map((f) => `<div>${fieldHTML(...f)}</div>`).join("")}</div></div>`).join("");
}

function readSettingsForm(onlyChanged = false) {
  const out = {};
  $$("#settings-form [data-k]").forEach((el) => {
    const k = el.dataset.k, t = el.dataset.t;
    let v;
    if (t === "bool") v = el.checked;
    else if (t === "int") v = parseInt(el.value || "0", 10);
    else if (t === "float") v = parseFloat(el.value || "0");
    else if (t === "weights") { v = {}; $$("[data-w]", el).forEach((w) => (v[w.dataset.w] = parseFloat(w.value || "0"))); }
    else if (t === "secret") { if (!el.value) return; v = el.value; }
    else v = el.value;
    if (onlyChanged && JSON.stringify(v) === JSON.stringify(SETTINGS[k])) return;
    out[k] = v;
  });
  return out;
}

async function loadSettings() {
  [SETTINGS, FORMATS, PRESETS] = await Promise.all([api("/api/settings"), api("/api/formats"), api("/api/presets")]);
  renderSettings();
  const fmtOpts = Object.entries(FORMATS).filter(([, f]) => !f.uses_source_video)
    .map(([k, f]) => `<option value="${k}">${esc(f.label)}</option>`).join("");
  $("#c-format").innerHTML = `<option value="">auto (from pool)</option>` + fmtOpts;
  $("#t-format").innerHTML = fmtOpts;
  const presetOpts = Object.keys(PRESETS.presets).map((p) => `<option>${esc(p)}</option>`).join("");
  ["#c-preset", "#u-preset", "#p-pick"].forEach((s) => ($(s).innerHTML = presetOpts));
  $("#f-pick").innerHTML = Object.entries(FORMATS).map(([k, f]) => `<option value="${k}">${esc(f.label)}</option>`).join("");
  renderFormatForm($("#f-pick").value);
  $("#ap-on").checked = SETTINGS.autopilot;
  $("#ap-vpd").value = SETTINGS.videos_per_day;
  $("#ap-budget").value = SETTINGS.monthly_budget_usd;
}

// ---- Status ------------------------------------------------------------------
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const p = s.providers;
    const warn = [];
    if (!p.writer) warn.push(`set your ${p.writer_name} API key in Settings`);
    if (s.missing_binaries.length) warn.push("missing " + s.missing_binaries.join(", "));
    $("#statusbar").innerHTML =
      `<span>Spend <b>$${s.month_spend.toFixed(2)}</b> / $${s.monthly_budget}</span>` +
      `<span>Topics <b>${s.topics_new}</b></span>` +
      `<span>${s.worker.busy ? "<b>Rendering…</b>" : "Idle"}</span>` +
      (warn.length ? `<span style="color:var(--bad)">${esc(warn.join(" · "))}</span>` : "");
    $("#ap-note").textContent = "Autopilot: " + (s.worker.autopilot_note || "starting…") + ` · Output folder: ${s.output_dir}`;
    $("#t-refill").disabled = s.refilling;
    $("#t-refill").textContent = s.refilling ? "Refilling…" : "Refill with Claude";
    if (s.refill_error) $("#t-refill").title = s.refill_error;
  } catch (e) { $("#statusbar").textContent = "offline"; }
}

// ---- Jobs ------------------------------------------------------------------------
function jobRow(j) {
  const thumb = j.output && j.output.thumbnails && Object.keys(j.output.thumbnails)[0];
  const img = thumb ? `<img src="/media/${j.id}/thumbnails/${thumb}?t=${j.updated_at}">` : `<div class="ph"></div>`;
  const pct = Math.round((j.progress || 0) * 100);
  return `<div class="job" data-id="${j.id}">${img}
    <div style="min-width:0"><div class="t">${esc(j.title)}</div>
      <div class="sub">${esc(FORMATS[j.format]?.label || j.format)} · ${esc(j.preset)} · ${j.origin === "autopilot" ? "autopilot · " : ""}$${(j.cost_usd || 0).toFixed(3)} · ${esc(j.step || "")}</div>
      ${j.status === "running" || j.status === "queued" ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}
      ${j.error ? `<div class="sub err">${esc(j.error.slice(0, 160))}</div>` : ""}
    </div>
    <span class="pill ${j.status}">${j.status.replace("_", " ")}</span></div>`;
}

async function refreshJobs() {
  const jobs = await api("/api/jobs?limit=100");
  $("#jobs").innerHTML = jobs.map(jobRow).join("") || `<p class="muted">Nothing yet.</p>`;
  $("#recent").innerHTML = jobs.slice(0, 6).map(jobRow).join("") || `<p class="muted">Nothing yet. Hit Generate.</p>`;
}

document.addEventListener("click", (e) => {
  const row = e.target.closest(".job");
  if (row) openJob(+row.dataset.id);
});

let openJobId = null, openJobStatus = null;
async function openJob(id) {
  openJobId = id;
  const j = await api(`/api/jobs/${id}`);
  openJobStatus = j.status;
  const vids = j.output ? Object.keys(j.output.videos || {}).map((k) =>
    `<div><div class="muted">${k}</div><video controls preload="metadata" src="/media/${id}/videos/${k}"></video></div>`).join("") : "";
  const script = j.script;
  const editable = script && ["awaiting_approval", "done", "failed", "cancelled"].includes(j.status);
  const kinds = ["auto", "ai_image", "ai_video", "stock", "movie", "source"];
  const beats = script ? script.beats.map((b, i) => `<div class="beat" data-i="${i}">
      <div class="n">${i + 1}</div>
      <textarea rows="3" data-f="narration" ${editable ? "" : "disabled"}>${esc(b.narration)}</textarea>
      <textarea rows="3" data-f="image_prompt" ${editable ? "" : "disabled"}>${esc(b.image_prompt)}</textarea>
      <select data-f="kind" ${editable ? "" : "disabled"}>${kinds.map((k) => `<option ${k === b.kind ? "selected" : ""}>${k}</option>`).join("")}</select>
    </div>`).join("") : "";
  $("#job-body").innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:center">
      <h2 style="margin:0">${esc(j.title)}</h2><button id="jd-close">✕</button></div>
    <p class="muted">#${j.id} · ${esc(FORMATS[j.format]?.label || j.format)} · preset ${esc(j.preset)} · <span class="pill ${j.status}">${j.status}</span> · ${esc(j.step)} · cost $${j.cost_usd.toFixed(3)}</p>
    ${j.error ? `<p class="err">${esc(j.error)}</p>` : ""}
    ${vids ? `<div class="videos">${vids}</div>` : ""}
    ${j.output ? `<p class="muted">Folder: ${esc(j.output.folder)}</p>` : ""}
    ${script ? `<h3>Upload text</h3><pre class="muted">${esc(script.title)}\n\n${esc(script.description)}\n\n${esc((script.hashtags || []).map((h) => "#" + h).join(" "))}</pre>` : ""}
    <div class="actions">
      ${j.status === "awaiting_approval" ? `<button class="primary" id="jd-approve">Approve & render</button>` : ""}
      ${["queued", "running", "awaiting_approval"].includes(j.status) ? `<button class="danger" id="jd-cancel">Cancel</button>` : ""}
      ${["failed", "cancelled"].includes(j.status) ? `<button id="jd-retry">Retry</button>` : ""}
      ${editable && j.status !== "awaiting_approval" ? `<button class="primary" id="jd-remake">Re-render with edits</button>` : ""}
      <button id="jd-new">New script, same topic</button>
      <button class="danger" id="jd-del">Delete</button>
    </div>
    ${script ? `<h3>Script ${editable ? "(editable)" : ""}</h3><div class="beats">${beats}</div>` : ""}
    <h3>Log</h3><div class="log">${esc(j.log)}</div>`;
  const dlg = $("#job-dialog");
  if (!dlg.open) dlg.showModal();
  const collect = () => {
    const s = structuredClone(script);
    $$(".beat", $("#job-body")).forEach((el) => {
      const b = s.beats[+el.dataset.i];
      $$("[data-f]", el).forEach((f) => (b[f.dataset.f] = f.value));
    });
    return s;
  };
  const on = (sel, fn) => { const el = $(sel); if (el) el.onclick = fn; };
  on("#jd-close", () => dlg.close());
  on("#jd-approve", async () => { await api(`/api/jobs/${id}/approve`, { json: { script: collect() } }); dlg.close(); refreshJobs(); });
  on("#jd-cancel", async () => { await api(`/api/jobs/${id}/cancel`, { json: {} }); openJob(id); refreshJobs(); });
  on("#jd-retry", async () => { await api(`/api/jobs/${id}/retry`, { json: {} }); dlg.close(); refreshJobs(); });
  on("#jd-remake", async () => { const r = await api(`/api/jobs/${id}/remake`, { json: { script: collect() } }); dlg.close(); refreshJobs(); toast(`Queued #${r.id}`); });
  on("#jd-new", async () => { const r = await api(`/api/jobs/${id}/remake`, { json: { new_script: true } }); dlg.close(); refreshJobs(); toast(`Queued #${r.id}`); });
  on("#jd-del", async () => { if (confirm("Delete this job and its working files? (Finished videos in the output folder stay.)")) { await api(`/api/jobs/${id}`, { method: "DELETE" }); dlg.close(); refreshJobs(); } });
}
$("#job-dialog").addEventListener("close", () => (openJobId = null));

// ---- Create ----------------------------------------------------------------------
function triState(sel) { const v = $(sel).value; return v === "" ? undefined : v === "1"; }
async function createJob(surprise) {
  const overrides = {};
  const len = parseInt($("#c-length").value, 10);
  if (len) overrides.target_seconds = len;
  const ap = triState("#c-approve"); if (ap !== undefined) overrides.require_approval = ap;
  const v = triState("#c-vert"); if (v !== undefined) overrides.vertical = v;
  const h = triState("#c-horiz"); if (h !== undefined) overrides.horizontal = h;
  if ($("#c-notes").value.trim()) overrides.extra_script_notes = $("#c-notes").value.trim();
  const body = { title: surprise ? "" : $("#c-title").value, format: $("#c-format").value, preset: $("#c-preset").value, overrides };
  try {
    const r = await api("/api/jobs", { json: body });
    $("#c-title").value = "";
    refreshJobs();
    openJob(r.id);
  } catch (e) { toast(e.message); }
}
$("#c-go").onclick = () => createJob(false);
$("#c-auto").onclick = () => createJob(true);

$("#u-go").onclick = () => {
  const f = $("#u-file").files[0];
  if (!f) return toast("Pick a video file first");
  const fd = new FormData();
  fd.append("file", f);
  fd.append("direction", $("#u-dir").value);
  fd.append("preset", $("#u-preset").value);
  fd.append("target_seconds", $("#u-length").value || "0");
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs/upload");
  xhr.upload.onprogress = (e) => ($("#u-progress").textContent = `Uploading ${Math.round((e.loaded / e.total) * 100)}%`);
  xhr.onload = () => {
    $("#u-progress").textContent = "";
    if (xhr.status >= 300) return toast(JSON.parse(xhr.responseText).detail || "upload failed");
    refreshJobs();
    openJob(JSON.parse(xhr.responseText).id);
  };
  xhr.send(fd);
};

$("#ap-save").onclick = async () => {
  SETTINGS = await api("/api/settings", { method: "PUT", json: {
    autopilot: $("#ap-on").checked, videos_per_day: parseInt($("#ap-vpd").value, 10) || 1,
    monthly_budget_usd: parseFloat($("#ap-budget").value) || 0 } });
  renderSettings();
  toast("Saved");
};

// ---- Topics ----------------------------------------------------------------------
async function refreshTopics() {
  const st = $("#t-status").value;
  const list = await api(`/api/topics?status=${st}`);
  $("#topics").innerHTML = list.map((t) => `<div class="topic">
    <div class="tt"><div>${esc(t.title)}</div><div class="muted">${esc(FORMATS[t.format]?.label || t.format)} · ${esc(t.source)}${t.angle ? " · " + esc(t.angle) : ""}</div></div>
    ${st === "new" ? `<button data-make="${t.id}" class="primary">Make</button><button data-reject="${t.id}">✕</button>` : ""}
    ${st === "rejected" ? `<button data-restore="${t.id}">Restore</button>` : ""}
  </div>`).join("") || `<p class="muted">No topics here.</p>`;
}
$("#topics").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.make) { const r = await api("/api/jobs", { json: { topic_id: +b.dataset.make, preset: $("#c-preset").value } }); openJob(r.id); }
  if (b.dataset.reject) await api(`/api/topics/${b.dataset.reject}`, { method: "PATCH", json: { status: "rejected" } });
  if (b.dataset.restore) await api(`/api/topics/${b.dataset.restore}`, { method: "PATCH", json: { status: "new" } });
  refreshTopics(); refreshJobs();
});
$("#t-status").onchange = refreshTopics;
$("#t-add").onclick = async () => {
  if (!$("#t-new").value.trim()) return;
  await api("/api/topics", { json: { title: $("#t-new").value.trim(), format: $("#t-format").value } });
  $("#t-new").value = ""; refreshTopics();
};
$("#t-refill").onclick = async () => {
  await api("/api/topics/refill", { json: { count: 12 } });
  refreshStatus();
  const poll = setInterval(async () => {
    const s = await api("/api/status");
    if (!s.refilling) { clearInterval(poll); refreshTopics(); refreshStatus(); if (s.refill_error) toast("Refill failed: " + s.refill_error); }
  }, 3000);
};

// ---- Library ---------------------------------------------------------------------
async function refreshLibrary() {
  const items = await api("/api/library");
  $("#library").innerHTML = items.map((i) => `<div class="libitem"><b>${esc(i.kind)}</b> ${esc(i.path.split("/").pop())}
    <div class="muted">${esc(i.tags)}${i.duration ? ` · ${i.duration.toFixed(1)}s` : ""}</div>
    <button class="danger" data-del="${i.id}">Remove</button></div>`).join("") || `<p class="muted">Empty.</p>`;
}
$("#library").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-del]");
  if (b) { await api(`/api/library/${b.dataset.del}`, { method: "DELETE" }); refreshLibrary(); }
});
$("#l-add").onclick = async () => {
  const files = $("#l-file").files;
  for (const f of files) {
    const fd = new FormData(); fd.append("file", f); fd.append("tags", $("#l-tags").value);
    const r = await fetch("/api/library", { method: "POST", body: fd });
    if (!r.ok) toast(`${f.name}: ${(await r.json()).detail}`);
  }
  $("#l-file").value = ""; refreshLibrary();
};

// ---- Formats ---------------------------------------------------------------------
const FORMAT_FIELDS = [
  ["label", "str"], ["description", "str"], ["examples", "list"], ["structure", "text"], ["tone", "str"],
  ["visual_style", "str"], ["recurring_character", "bool"], ["uses_movies", "bool"],
];
function renderFormatForm(key) {
  const f = FORMATS[key] || { label: "", examples: [] };
  $("#f-form").dataset.key = key || "";
  $("#f-form").innerHTML = (key ? "" : `<label>Key (file name)<input data-ff="__key" placeholder="e.g. survival_rules"></label>`) +
    FORMAT_FIELDS.map(([k, t]) => {
      const v = f[k];
      if (t === "bool") return `<label class="switch"><input type="checkbox" data-ff="${k}" ${v ? "checked" : ""}> ${k.replaceAll("_", " ")}</label>`;
      if (t === "text") return `<label>${k}<textarea rows="6" data-ff="${k}">${esc(v)}</textarea></label>`;
      if (t === "list") return `<label>${k} (one per line)<textarea rows="4" data-ff="${k}" data-list>${esc((v || []).join("\n"))}</textarea></label>`;
      return `<label>${k.replaceAll("_", " ")}<input data-ff="${k}" value="${esc(v)}"></label>`;
    }).join("");
}
$("#f-pick").onchange = () => renderFormatForm($("#f-pick").value);
$("#f-new").onclick = () => renderFormatForm("");
$("#f-save").onclick = async () => {
  const data = {};
  $$("#f-form [data-ff]").forEach((el) => {
    data[el.dataset.ff] = el.type === "checkbox" ? el.checked : el.hasAttribute("data-list") ? el.value.split("\n").map((s) => s.trim()).filter(Boolean) : el.value;
  });
  const key = $("#f-form").dataset.key || data.__key;
  delete data.__key;
  if (!key) return toast("Give the format a key");
  const prev = FORMATS[key] || {};
  if (prev.uses_source_video) data.uses_source_video = true;
  const r = await api(`/api/formats/${encodeURIComponent(key)}`, { method: "PUT", json: data });
  await loadSettings();
  $("#f-pick").value = r.saved; renderFormatForm(r.saved);
  toast("Format saved");
};

// ---- Settings & presets -------------------------------------------------------------
$("#s-save").onclick = async () => {
  try {
    SETTINGS = await api("/api/settings", { method: "PUT", json: readSettingsForm() });
    renderSettings();
    $("#s-msg").textContent = "Saved " + new Date().toLocaleTimeString();
  } catch (e) { toast(e.message); }
};
$("#p-load").onclick = () => ($("#p-view").textContent = JSON.stringify(PRESETS.presets[$("#p-pick").value] || {}, null, 2) || "{}");
$("#p-save").onclick = async () => {
  const name = $("#p-name").value.trim();
  if (!name) return toast("Name the preset");
  const changed = readSettingsForm(true);
  PRESETS = await api(`/api/presets/${encodeURIComponent(name)}`, { method: "PUT", json: changed });
  await loadSettings();
  toast(`Preset "${name}" saved with ${Object.keys(changed).length} override(s). Note: the base settings were not changed.`);
};
$("#p-del").onclick = async () => {
  const name = $("#p-pick").value;
  if (PRESETS.builtin.includes(name)) return toast("Built-in presets can't be deleted");
  PRESETS = await api(`/api/presets/${encodeURIComponent(name)}`, { method: "DELETE" });
  await loadSettings();
};

// ---- Tabs & polling --------------------------------------------------------------------
$$("#tabs button").forEach((b) => (b.onclick = () => {
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "topics") refreshTopics();
  if (b.dataset.tab === "library") refreshLibrary();
}));

(async function init() {
  await loadSettings();
  refreshStatus(); refreshJobs();
  setInterval(() => { refreshStatus(); refreshJobs(); }, 4000);
  setInterval(async () => {
    if (openJobId === null) return;
    const j = await api(`/api/jobs/${openJobId}`);
    const live = ["queued", "running"].includes(j.status) || j.status !== openJobStatus;
    if (live && !document.activeElement.closest("#job-body textarea")) openJob(openJobId);
  }, 4000);
})();
