import { session, get, post, put, patch, del, upload } from "./api.js";
import { html, raw, render, icon, $, $$, fmtTime, fmtNum, fmtMoney, fmtDate, toast, toastError, modal, closeModal, confirmDialog, emptyState, skeletonRows, debounce, esc } from "./ui.js";

const PERMS = { super_admin: ["*"], content_manager: ["content", "takedown", "analytics"], moderator: ["users", "playlists", "takedown", "analytics"], analyst: ["analytics"] };
const can = (p) => { const r = PERMS[session.user?.admin_role] || []; return r.includes("*") || r.includes(p); };
const TABS = [
  ["dashboard", "Dashboard", "analytics"], ["songs", "Songs", "content"], ["import", "Bulk upload", "content"], ["artists", "Artists", "content"],
  ["albums", "Albums", "content"], ["taxonomy", "Categories & languages", "content"], ["users", "Users", "users"], ["playlists", "Playlists", "playlists"],
  ["takedowns", "Takedowns", "takedown"], ["plans", "Plans & settings", "*"], ["audit", "Audit log", "*"],
];
const LICENSE_TYPES = [["label", "Record label"], ["distributor", "Distributor"], ["publisher", "Publisher"], ["direct", "Direct from artist"], ["royalty_free", "Royalty-free"], ["cc0", "CC0 / public domain"]];

export async function adminView(view, tab, query, alive) {
  if (!session.user) { render(view, emptyState("lock", "Admins only", "Log in with an admin account.", { href: "/login?next=/admin", label: "Log in" })); return; }
  if (!session.isAdmin) { render(view, emptyState("shield", "You don't have admin access", "Ask a super admin to give your account a role.")); return; }
  const tabs = TABS.filter(([, , p]) => can(p));
  tab = tab && tabs.some(([k]) => k === tab) ? tab : tabs[0][0];
  render(view, html`<div class="section-head"><div class="admin-brand"><img src="/img/logo.svg" alt="PENDEL logo" width="52" height="52"><div><span class="brand-name">PENDEL</span><h1 style="margin:0">Admin Dashboard</h1><p>Signed in as ${session.user.email} · role: ${session.user.admin_role.replace("_", " ")}</p></div></div></div>
    <div class="tabs" role="tablist">${tabs.map(([k, l]) => html`<a class="tab ${k === tab ? "active" : ""}" role="tab" aria-selected="${k === tab}" href="/admin/${k}" data-link>${l}</a>`)}</div>
    <div id="admin-body">${skeletonRows()}</div>`);
  const body = $("#admin-body");
  const fn = { dashboard, songs: songsTab, import: importTab, artists, albums, taxonomy, users, playlists, takedowns, plans, audit }[tab];
  await fn(body, alive);
}

// ------------------------------------------------------------------ dashboard
async function dashboard(el, alive) {
  const d = await get("/api/admin/stats");
  if (!alive()) return;
  const t = d.totals;
  const stat = (k, v) => html`<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  render(el, html`<div class="admin-grid">
      ${stat("Users", fmtNum(t.users))}${stat("New users (7d)", fmtNum(t.new_users_7d))}${stat("Premium users", fmtNum(t.premium_users))}
      ${stat("Published songs", fmtNum(t.songs))}${stat("Plays (30d)", fmtNum(t.plays_30d))}${stat("Listening hours (30d)", t.listening_hours_30d)}
      ${stat("Highlight → full song", t.highlight_to_full_rate === null ? "—" : t.highlight_to_full_rate + "%")}${stat("Revenue (30d)", fmtMoney(t.revenue_minor_30d))}
      ${stat("Sandbox payments (30d)", t.sandbox_payments_30d)}${stat("Playlists", fmtNum(t.playlists))}
    </div>
    <div class="panel"><h3>Daily plays, last 14 days</h3><p class="dim small" style="margin:-6px 0 10px">Full songs and highlights combined. Hover a bar for details.</p>${barChart(d.daily)}</div>
    <div class="split">
      <div class="panel"><h3>Top songs (30 days)</h3><div class="table-wrap"><table class="tbl"><thead><tr><th>#</th><th>Song</th><th>Plays</th></tr></thead><tbody>
        ${d.top_songs.map((s, i) => html`<tr><td>${i + 1}</td><td><strong>${s.title}</strong><div class="dim small">${s.artist}</div></td><td>${fmtNum(s.plays)}</td></tr>`)}</tbody></table></div></div>
      <div class="panel"><h3>Languages</h3><div class="table-wrap"><table class="tbl"><thead><tr><th>Language</th><th>Songs</th><th>Plays (30d)</th></tr></thead><tbody>
        ${d.by_language.map((l) => html`<tr><td>${l.name}</td><td>${l.songs}</td><td>${fmtNum(l.plays)}</td></tr>`)}</tbody></table></div></div>
    </div>`);
  const tip = document.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; document.body.append(tip);
  $$(".chart .hit", el).forEach((r) => {
    r.addEventListener("mousemove", (e) => { tip.hidden = false; tip.textContent = r.dataset.tip; tip.style.left = e.clientX + 12 + "px"; tip.style.top = e.clientY - 34 + "px"; });
    r.addEventListener("mouseleave", () => (tip.hidden = true));
  });
  const obs = new MutationObserver(() => { if (!document.body.contains(el)) { tip.remove(); obs.disconnect(); } });
  obs.observe(document.body, { childList: true, subtree: true });
}
function barChart(rows) {
  const W = 760, H = 220, P = { l: 40, r: 10, t: 10, b: 28 };
  const max = Math.max(1, ...rows.map((r) => r.plays));
  const nice = Math.pow(10, Math.floor(Math.log10(max))) * Math.ceil(max / Math.pow(10, Math.floor(Math.log10(max))));
  const bw = (W - P.l - P.r) / rows.length;
  const y = (v) => H - P.b - ((H - P.t - P.b) * v) / nice;
  const ticks = [0, nice / 2, nice];
  return raw(`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Bar chart of daily plays">
    <g class="grid">${ticks.map((v) => `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${P.l - 6}" y="${y(v) + 4}" text-anchor="end">${fmtNum(v)}</text>`).join("")}</g>
    ${rows.map((r, i) => {
      const x = P.l + i * bw + bw * 0.22, w = bw * 0.56, h = Math.max(0, H - P.b - y(r.plays));
      const bar = h > 0 ? `<path class="bar" d="M${x},${H - P.b} v${-Math.max(0, h - 4)} q0,-4 4,-4 h${w - 8} q4,0 4,4 v${Math.max(0, h - 4)} z"/>` : "";
      return `${bar}<rect class="hit" x="${P.l + i * bw}" y="${P.t}" width="${bw}" height="${H - P.t - P.b}" fill="transparent" data-tip="${esc(r.day)}: ${r.plays} plays (${r.highlight_plays} highlights)"/>
        ${i % 2 === 0 ? `<text x="${P.l + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${esc(r.day)}</text>` : ""}`;
    }).join("")}
  </svg><table class="sr-only"><caption>Daily plays</caption>${rows.map((r) => `<tr><td>${esc(r.day)}</td><td>${r.plays}</td></tr>`).join("")}</table>`);
}

// ------------------------------------------------------------------ songs
let lookups = null;
async function getLookups(force = false) { if (!lookups || force) lookups = await get("/api/admin/lookups"); return lookups; }

async function songsTab(el, alive) {
  render(el, html`<div class="toolbar"><input class="input" id="sq" type="search" placeholder="Search title, artist or ID" aria-label="Search songs">
    <select class="input" id="sst" aria-label="Status" style="min-width:150px;flex:0 0 160px"><option value="">All statuses</option><option value="published">Published</option><option value="draft">Draft</option><option value="taken_down">Taken down</option></select>
    <button class="btn primary" id="new-song" style="margin-left:auto">${icon("plus")}Add song</button></div><div id="slist">${skeletonRows()}</div>`);
  const load = async () => {
    const p = new URLSearchParams({ q: $("#sq").value, status: $("#sst").value });
    const d = await get("/api/admin/songs?" + p);
    if (!alive()) return;
    render($("#slist"), d.songs.length ? html`<div class="table-wrap"><table class="tbl"><thead><tr><th></th><th>Song</th><th>Language</th><th>Highlight</th><th>Audio</th><th>Licence</th><th>Status</th><th></th></tr></thead><tbody>
      ${d.songs.map((s) => html`<tr><td><img src="${s.cover_url || "/img/logo.svg"}" alt=""></td>
        <td><strong>${s.title}</strong><div class="dim small">${s.artist.name} · ${s.id}</div></td><td>${s.language.name}</td>
        <td>${s.highlight ? html`${fmtTime(s.highlight.start)}–${fmtTime(s.highlight.end)}<div class="dim small">${s.highlight.source}</div>` : html`<span class="dim">none</span>`}</td>
        <td>${s.assets.filter((a) => a.kind === "full").length ? html`${fmtTime(s.duration)}${s.assets.some((a) => a.kind === "preview") ? html`<div class="dim small">+ preview</div>` : ""}` : html`<span class="dim">missing</span>`}</td>
        <td class="small">${s.license ? html`${s.license.licensor}<div class="dim">${s.license.license_type}${s.license.allow_free_full_stream ? " · free stream" : " · premium"}</div>` : html`<span class="dim">missing</span>`}</td>
        <td><span class="status ${s.status}">${s.status.replace("_", " ")}</span></td>
        <td class="acts"><button class="btn sm" data-edit="${s.id}">Edit</button></td></tr>`)}</tbody></table></div>`
      : emptyState("music", "No songs found"));
    $("#slist").onclick = (e) => { const b = e.target.closest("[data-edit]"); if (b) songEditor(d.songs.find((s) => s.id === b.dataset.edit), load); };
  };
  $("#sq").addEventListener("input", debounce(load, 250));
  $("#sst").addEventListener("change", load);
  $("#new-song").onclick = () => songEditor(null, load);
  await load();
}

async function songEditor(song, reload) {
  const L = await getLookups(true);
  const lic = song?.license || { licensor: "", license_type: "label", territories: "WORLD", allow_free_full_stream: 0, allow_offline: 0, allow_lyrics: 0 };
  const opt = (v, l, sel) => html`<option value="${v}" ${raw(String(v) === String(sel) ? "selected" : "")}>${l}</option>`;
  const m = modal(html`<h3>${song ? `Edit “${song.title}”` : "Add a song"}</h3>
    <form id="sf">
      <div class="form-grid">
        <div class="field"><label>Title</label><input class="input" name="title" required maxlength="150" value="${song?.title || ""}"></div>
        <div class="field"><label>Artist</label><select class="input" name="artist_id" required><option value="">Choose…</option>${L.artists.map((a) => opt(a.id, a.name, song?.artist.id))}</select></div>
        <div class="field"><label>Album</label><select class="input" name="album_id"><option value="">No album</option>${L.albums.map((a) => opt(a.id, a.title, song?.album?.id))}</select></div>
        <div class="field"><label>Language</label><select class="input" name="language_code" required><option value="">Choose…</option>${L.languages.map((l) => opt(l.code, l.name, song?.language.code))}</select></div>
        <div class="field"><label>Genre</label><input class="input" name="genre" list="genres" value="${song?.genre || ""}" maxlength="60"><datalist id="genres">${L.genres.map((g) => html`<option value="${g}">`)}</datalist></div>
        <div class="field"><label>Release year</label><input class="input" name="release_year" type="number" min="1900" max="2100" value="${song?.year || ""}"></div>
        <div class="field"><label>Trending pin (1 = top)</label><input class="input" name="trending_pin" type="number" min="1" max="100" value=""></div>
        <div class="field"><label>Status</label><select class="input" name="status">${opt("draft", "Draft", song?.status || "draft")}${opt("published", "Published", song?.status)}</select></div>
      </div>
      <div class="field"><span class="lbl">Categories (pick all that fit)</span><div class="chips">${L.categories.map((c) => html`<label class="chip"><input type="checkbox" name="cat" value="${c.slug}" ${raw(song?.categories.some((x) => x.slug === c.slug) ? "checked" : "")} style="accent-color:var(--pink)">${c.name}</label>`)}</div></div>
      <label class="check"><input type="checkbox" name="is_featured" ${raw(song?.is_featured ? "checked" : "")}>Featured on the home page</label>
      <label class="check"><input type="checkbox" name="is_explicit" ${raw(song?.is_explicit ? "checked" : "")}>Explicit</label>
      <div class="ed-section"><h4>Licensing (required)</h4><div class="form-grid">
        <div class="field"><label>Licensor / rights holder</label><input class="input" name="licensor" required maxlength="120" value="${lic.licensor}"></div>
        <div class="field"><label>Licence type</label><select class="input" name="license_type">${LICENSE_TYPES.map(([v, l]) => opt(v, l, lic.license_type))}</select></div>
        <div class="field"><label>Territories</label><input class="input" name="territories" value="${lic.territories || "WORLD"}" maxlength="200"></div>
        <div class="field"><label>Licence reference</label><input class="input" name="reference" value="${lic.reference || ""}" maxlength="120"></div>
        <div class="field"><label>Valid from</label><input class="input" type="date" name="valid_from" value="${lic.valid_from || ""}"></div>
        <div class="field"><label>Valid until</label><input class="input" type="date" name="valid_until" value="${lic.valid_until || ""}"></div>
      </div>
        <label class="check"><input type="checkbox" name="allow_free_full_stream" ${raw(lic.allow_free_full_stream ? "checked" : "")}>Full song may stream on the Free plan (otherwise Premium only; highlights stay free)</label>
        <label class="check"><input type="checkbox" name="allow_offline" ${raw(lic.allow_offline ? "checked" : "")}>Offline downloads permitted</label>
        <label class="check"><input type="checkbox" name="allow_lyrics" ${raw(lic.allow_lyrics ? "checked" : "")}>Lyrics display is licensed</label>
      </div>
      <div class="modal-actions" style="justify-content:space-between"><span class="dim small">${song ? song.id : "Save first, then upload audio and set the highlight."}</span><div style="display:flex;gap:8px"><button type="button" class="btn ghost" data-close>Close</button><button class="btn primary">${song ? "Save changes" : "Create draft"}</button></div></div>
    </form>
    ${song ? html`
      <div class="ed-section"><h4>Audio & artwork</h4>
        <div class="toolbar"><label class="btn">${icon("upload")}Upload licensed audio<input type="file" id="aud" accept="audio/*" class="sr-only"></label>
          <select class="input" id="aq" style="min-width:150px;flex:0 0 170px" aria-label="Audio quality"><option value="standard">Standard quality</option><option value="high">High quality (Premium)</option></select>
          <label class="btn">${icon("upload")}Upload cover<input type="file" id="cov" accept="image/png,image/jpeg,image/webp" class="sr-only"></label>
          <span class="dim small" id="aud-msg">${song.assets.map((a) => `${a.kind}/${a.quality} ${a.size_bytes ? Math.round(a.size_bytes / 1024) + " KB" : ""}`).join(" · ") || "No audio yet"}</span></div></div>
      <div class="ed-section"><h4>Highlight (20–30 seconds)</h4>
        <p class="dim small" style="margin-top:-6px">Drag the pink window over the chorus or hook. Drag its edges to resize. Only this part plays in Song Highlights.</p>
        <div class="wave-wrap" id="wave"><canvas></canvas><div class="wave-sel" id="wsel"><div class="h l"></div><div class="h r"></div><span></span></div><div class="wave-head" id="whead" hidden></div><div class="wave-msg" id="wmsg">Loading waveform…</div></div>
        <div class="toolbar" style="margin-top:10px">
          <label class="small">Start <input class="input" id="hs" type="number" step="0.1" min="0" style="width:100px;min-width:0;display:inline-block;height:36px"></label>
          <label class="small">End <input class="input" id="he" type="number" step="0.1" min="0" style="width:100px;min-width:0;display:inline-block;height:36px"></label>
          <button class="btn sm" id="hauto">${icon("spark")}Auto-detect</button><button class="btn sm" id="hplay">${icon("play")}Preview</button><button class="btn sm primary" id="hsave">Save highlight</button>
          <span class="dim small" id="hmsg"></span></div></div>
      <div class="ed-section"><h4>Lyrics (licensed only)</h4>
        <p class="dim small" style="margin-top:-6px">Paste LRC (<code>[01:12.40] line</code>) for synced lyrics, or plain lines for static lyrics.</p>
        <div class="split"><div class="field"><label>Lyrics</label><textarea class="input" id="ly" rows="8" dir="auto" placeholder="[00:12.00] First line"></textarea></div>
          <div class="field"><label>Translation (optional, same timing)</label><textarea class="input" id="lyt" rows="8" placeholder="[00:12.00] Translated line"></textarea></div></div>
        <div class="toolbar"><input class="input" id="lys" placeholder="Lyrics licence / source (required)" maxlength="200"><input class="input" id="lytl" placeholder="Translation language code" value="en" style="flex:0 0 200px;min-width:0"><button class="btn sm primary" id="lysave">Save lyrics</button><button class="btn sm danger" id="lydel">Remove lyrics</button></div></div>
      <div class="ed-section"><h4>Rights & removal</h4><div class="toolbar">
        ${song.status === "taken_down" ? html`<button class="btn" id="restore">Restore song</button>` : html`<button class="btn danger" id="takedown">Take down (copyright request)</button>`}
        ${song.status === "draft" ? html`<button class="btn danger" id="deldraft">${icon("trash")}Delete draft</button>` : ""}</div></div>` : ""}`, { wide: true });

  $("[data-close]", m).onclick = closeModal;
  $("#sf", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, fd = new FormData(f);
    const body = {
      title: fd.get("title"), artist_id: fd.get("artist_id"), album_id: fd.get("album_id") || null, language_code: fd.get("language_code"), genre: fd.get("genre"),
      release_year: fd.get("release_year") || null, status: fd.get("status"), categories: fd.getAll("cat"), is_featured: f.is_featured.checked, is_explicit: f.is_explicit.checked,
      license: { licensor: fd.get("licensor"), license_type: fd.get("license_type"), territories: fd.get("territories"), reference: fd.get("reference"), valid_from: fd.get("valid_from"), valid_until: fd.get("valid_until"),
        allow_free_full_stream: f.allow_free_full_stream.checked, allow_offline: f.allow_offline.checked, allow_lyrics: f.allow_lyrics.checked },
    };
    if (fd.get("trending_pin")) body.trending_pin = fd.get("trending_pin");
    try {
      const r = song ? await patch(`/api/admin/songs/${song.id}`, body) : await post("/api/admin/songs", body);
      toast(song ? "Saved" : "Draft created. Now upload the audio."); reload();
      const fresh = (await get(`/api/admin/songs?q=${r.song.id}`)).songs[0];
      songEditor(fresh, reload);
    } catch (err) { toastError(err); }
  });
  if (!song) return;

  // audio + cover uploads
  $("#aud", m).addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    $("#aud-msg", m).textContent = `Uploading ${f.name}…`;
    try {
      const r = await upload(`/api/admin/songs/${song.id}/audio`, f, { method: "POST", query: `?quality=${$("#aq", m).value}` });
      toast(`Audio uploaded (${fmtTime(r.duration)}).${r.highlight_applied ? " Highlight auto-detected." : ""}`);
      reload(); songEditor((await get(`/api/admin/songs?q=${song.id}`)).songs[0], reload);
    } catch (err) { $("#aud-msg", m).textContent = ""; toastError(err); }
  });
  $("#cov", m).addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { await upload(`/api/admin/songs/${song.id}/cover`, f); toast("Cover updated"); reload(); } catch (err) { toastError(err); }
  });

  // highlight editor
  highlightEditor(m, song);

  // lyrics
  get(`/api/admin/songs/${song.id}/lyrics`).then((l) => { $("#ly", m).value = l.text; $("#lyt", m).value = l.translation_text; $("#lys", m).value = l.source; $("#lytl", m).value = l.translation_language || "en"; }).catch(() => {});
  $("#lysave", m).onclick = async () => {
    try { const r = await put(`/api/admin/songs/${song.id}/lyrics`, { text: $("#ly", m).value, translation_text: $("#lyt", m).value, translation_language: $("#lytl", m).value, source: $("#lys", m).value }); toast(`Saved ${r.lines} lyric lines${r.synced ? " (synced)" : ""}`); }
    catch (err) { toastError(err); }
  };
  $("#lydel", m).onclick = async () => { if (await confirmDialog("Remove lyrics?", "Lyrics and translations for this song will be removed.", { ok: "Remove", danger: true })) { try { await del(`/api/admin/songs/${song.id}/lyrics`); toast("Lyrics removed"); } catch (err) { toastError(err); } } };
  $("#takedown", m)?.addEventListener("click", () => takedownDialog(song, reload));
  $("#restore", m)?.addEventListener("click", async () => {
    const reason = prompt("Reason for restoring (e.g. claim withdrawn):");
    if (!reason) return;
    try { await post(`/api/admin/songs/${song.id}/restore`, { reason }); toast("Song restored"); closeModal(); reload(); } catch (err) { toastError(err); }
  });
  $("#deldraft", m)?.addEventListener("click", async () => {
    if (!(await confirmDialog("Delete this draft?", "The draft and its uploaded audio will be deleted.", { ok: "Delete", danger: true }))) return;
    try { await del(`/api/admin/songs/${song.id}`); toast("Draft deleted"); reload(); } catch (err) { toastError(err); }
  });
}

function takedownDialog(song, reload) {
  const m = modal(html`<h3>Take down “${song.title}”</h3><p class="muted small">The song disappears from the catalogue, highlights and search immediately. Playlists show it as unavailable. A record is kept for legal purposes.</p>
    <form id="tdf"><div class="field"><label>Claimant (rights holder)</label><input class="input" name="claimant" required maxlength="200"></div>
    <div class="field"><label>Reason / reference</label><textarea class="input" name="reason" required maxlength="1000" style="font-family:inherit"></textarea></div>
    <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button><button class="btn danger">Take down</button></div></form>`);
  $("[data-close]", m).onclick = closeModal;
  $("#tdf", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await post(`/api/admin/songs/${song.id}/takedown`, { claimant: e.target.claimant.value, reason: e.target.reason.value }); toast("Song taken down"); closeModal(); reload(); }
    catch (err) { toastError(err); }
  });
}

function highlightEditor(m, song) {
  const wrap = $("#wave", m), canvas = $("canvas", wrap), sel = $("#wsel", m), msg = $("#wmsg", m), head = $("#whead", m);
  const hs = $("#hs", m), he = $("#he", m), hmsg = $("#hmsg", m);
  const dur = song.duration || 0;
  let start = song.highlight?.start ?? Math.min(30, Math.max(0, dur * 0.35)), end = song.highlight?.end ?? Math.min(dur, start + 25);
  let previewAudio = null, raf = null;
  const clamp = () => {
    let len = Math.min(30, Math.max(20, end - start));
    start = Math.max(0, Math.min(start, dur - len)); end = Math.min(dur, start + len);
  };
  const draw = () => {
    if (!dur) return;
    sel.style.left = `${(100 * start) / dur}%`; sel.style.width = `${(100 * (end - start)) / dur}%`;
    $("span", sel).textContent = `${fmtTime(start)}–${fmtTime(end)} · ${(end - start).toFixed(1)}s`;
    hs.value = start.toFixed(1); he.value = end.toFixed(1);
  };
  if (!dur) { msg.textContent = "Upload audio to set a highlight."; sel.hidden = true; return; }
  draw();

  // waveform via Web Audio
  (async () => {
    try {
      const { url } = await get(`/api/admin/songs/${song.id}/preview-url`);
      const buf = await (await fetch(url)).arrayBuffer();
      const ac = new (window.AudioContext || window.webkitAudioContext)();
      const audioBuf = await ac.decodeAudioData(buf);
      ac.close();
      const data = audioBuf.getChannelData(0), W = canvas.clientWidth * 2, H = canvas.clientHeight * 2;
      canvas.width = W; canvas.height = H;
      const ctx = canvas.getContext("2d"), step = Math.floor(data.length / W);
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--text-3").trim() || "#888";
      for (let x = 0; x < W; x++) {
        let mx = 0;
        for (let i = 0; i < step; i += 8) mx = Math.max(mx, Math.abs(data[x * step + i] || 0));
        const h = Math.max(2, mx * H * 0.9);
        ctx.fillRect(x, (H - h) / 2, 1.4, h);
      }
      msg.hidden = true;
    } catch { msg.textContent = "Waveform unavailable here; use the start/end fields."; }
  })();

  // drag to move / resize
  let drag = null;
  wrap.addEventListener("pointerdown", (e) => {
    const r = wrap.getBoundingClientRect();
    const t = ((e.clientX - r.left) / r.width) * dur;
    const mode = e.target.classList.contains("l") ? "l" : e.target.classList.contains("r") ? "r" : e.target.closest("#wsel") ? "move" : "jump";
    if (mode === "jump") { const len = end - start; start = t - len / 2; end = start + len; clamp(); draw(); return; }
    drag = { mode, x0: t, s0: start, e0: end };
    wrap.setPointerCapture(e.pointerId);
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const r = wrap.getBoundingClientRect();
    const dt = ((e.clientX - r.left) / r.width) * dur - drag.x0;
    if (drag.mode === "move") { start = drag.s0 + dt; end = drag.e0 + dt; const len = drag.e0 - drag.s0; start = Math.max(0, Math.min(start, dur - len)); end = start + len; }
    else if (drag.mode === "l") { start = Math.max(0, Math.min(drag.s0 + dt, end - 20)); if (end - start > 30) start = end - 30; }
    else { end = Math.min(dur, Math.max(drag.e0 + dt, start + 20)); if (end - start > 30) end = start + 30; }
    draw();
  });
  wrap.addEventListener("pointerup", () => (drag = null));
  [hs, he].forEach((i) => i.addEventListener("change", () => { start = +hs.value; end = +he.value; if (end - start < 20 || end - start > 30) { hmsg.textContent = "Length must be 20–30 s; adjusted."; } clamp(); draw(); }));

  $("#hauto", m).onclick = async () => {
    hmsg.textContent = "Analysing audio…";
    try { const { suggestion: s } = await get(`/api/admin/songs/${song.id}/highlight/suggest`); start = s.start; end = s.end; clamp(); draw(); hmsg.textContent = `Suggested by ${s.method} (confidence ${Math.round(s.confidence * 100)}%). Review and save.`; }
    catch (err) { hmsg.textContent = ""; toastError(err); }
  };
  $("#hplay", m).onclick = async () => {
    if (previewAudio && !previewAudio.paused) { previewAudio.pause(); return; }
    const { url } = await get(`/api/admin/songs/${song.id}/preview-url`);
    previewAudio ||= new Audio();
    previewAudio.src = url; previewAudio.currentTime = start;
    previewAudio.onloadedmetadata = () => { previewAudio.currentTime = start; };
    previewAudio.play();
    head.hidden = false;
    const tick = () => {
      if (!previewAudio || previewAudio.paused) { head.hidden = true; return; }
      if (previewAudio.currentTime >= end) { previewAudio.pause(); head.hidden = true; return; }
      head.style.left = `${(100 * previewAudio.currentTime) / dur}%`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  };
  $("#hsave", m).onclick = async () => {
    hmsg.textContent = "Saving and cutting the preview…";
    try { await put(`/api/admin/songs/${song.id}/highlight`, { start, end }); hmsg.textContent = "Saved. The highlight preview has been re-cut."; toast("Highlight saved"); }
    catch (err) { hmsg.textContent = ""; toastError(err); }
  };
  m.parentElement._onClose = () => { previewAudio?.pause(); cancelAnimationFrame(raf); };
}

// ------------------------------------------------------------------ bulk import
async function importTab(el, alive) {
  const t = await get("/api/admin/import/template");
  if (!alive()) return;
  render(el, html`<div class="split">
    <div class="panel"><h3>1 · Upload audio & artwork</h3><p class="muted small">Upload any number of licensed audio files and covers. Each one gets a storage key to reference in the CSV.</p>
      <label class="btn primary">${icon("upload")}Choose files<input type="file" id="bulk" multiple accept="audio/*,image/png,image/jpeg,image/webp" class="sr-only"></label>
      <div id="keys" style="margin-top:14px"></div></div>
    <div class="panel"><h3>2 · Import the CSV</h3><p class="muted small">Columns: <code>${t.columns.join(", ")}</code>. Separate several categories with <code>;</code>. Rows with audio are published automatically (with an auto-detected highlight if you leave the times blank); rows without audio stay as drafts.</p>
      <div class="toolbar"><label class="btn sm">${icon("upload")}Load .csv file<input type="file" id="csvf" accept=".csv,text/csv" class="sr-only"></label><button class="btn sm" id="tmpl">Insert example</button></div>
      <textarea class="input" id="csv" rows="10" placeholder="Paste CSV here"></textarea>
      <div class="toolbar" style="margin-top:10px"><button class="btn" id="dry">Validate (dry run)</button><button class="btn primary" id="imp">Import</button></div>
      <div id="impres"></div></div></div>`);
  const keys = $("#keys");
  $("#bulk").addEventListener("change", async (e) => {
    for (const f of e.target.files) {
      const row = document.createElement("div"); row.className = "small"; row.textContent = `Uploading ${f.name}…`; keys.append(row);
      try { const r = await upload("/api/admin/uploads", f, { method: "POST" }); row.innerHTML = html`<code>${r.key}</code> ← ${f.name} <button class="btn sm ghost" data-copy="${r.key}">Copy</button>`.s; }
      catch (err) { row.textContent = `${f.name}: ${err.message}`; row.style.color = "#fb7185"; }
    }
  });
  keys.addEventListener("click", (e) => { const b = e.target.closest("[data-copy]"); if (b) navigator.clipboard.writeText(b.dataset.copy).then(() => toast("Key copied")); });
  $("#tmpl").onclick = () => { $("#csv").value = t.example; };
  $("#csvf").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) $("#csv").value = await f.text(); });
  const run = async (dry) => {
    try {
      const r = await post("/api/admin/import/csv", { csv: $("#csv").value, dry_run: dry });
      render($("#impres"), html`<p style="margin-top:14px"><strong>${dry ? "Validation" : "Import"}:</strong> ${r.imported} ok, ${r.failed} with problems</p>
        <div class="table-wrap"><table class="tbl"><thead><tr><th>Row</th><th>Title</th><th>Result</th></tr></thead><tbody>${r.results.map((x) => html`<tr><td>${x.row}</td><td>${x.title}</td><td>${x.ok ? (dry ? "Valid" : html`Imported <code>${x.song_id}</code>`) : html`<span style="color:#fb7185">${x.error}</span>`}</td></tr>`)}</tbody></table></div>`);
    } catch (err) { toastError(err); }
  };
  $("#dry").onclick = () => run(true);
  $("#imp").onclick = () => run(false);
}

// ------------------------------------------------------------------ artists / albums
async function artists(el, alive) {
  const d = await get("/api/admin/artists");
  if (!alive()) return;
  render(el, html`<div class="toolbar"><button class="btn primary" id="na" style="margin-left:auto">${icon("plus")}Add artist</button></div>
    <div class="table-wrap"><table class="tbl"><thead><tr><th></th><th>Artist</th><th>Country</th><th>Songs</th><th>Flags</th><th></th></tr></thead><tbody>
    ${d.artists.map((a) => html`<tr><td><img src="${a.image_url || "/img/logo.svg"}" alt="" style="border-radius:50%"></td><td><strong>${a.name}</strong><div class="dim small">${a.id}</div></td><td>${a.country || ""}</td><td>${a.song_count}</td>
      <td class="small">${a.is_verified ? "Verified " : ""}${a.is_independent ? "Independent" : ""}</td>
      <td class="acts"><label class="btn sm">Photo<input type="file" data-img="${a.id}" accept="image/png,image/jpeg,image/webp" class="sr-only"></label><button class="btn sm" data-ed="${a.id}">Edit</button><button class="btn sm danger" data-del="${a.id}">Delete</button></td></tr>`)}</tbody></table></div>`);
  const form = (a) => {
    const m = modal(html`<h3>${a ? "Edit artist" : "Add artist"}</h3><form id="af">
      <div class="field"><label>Name</label><input class="input" name="name" required maxlength="120" value="${a?.name || ""}"></div>
      <div class="field"><label>Country</label><input class="input" name="country" maxlength="60" value="${a?.country || ""}"></div>
      <div class="field"><label>Bio</label><textarea class="input" name="bio" maxlength="2000" style="font-family:inherit">${a?.bio || ""}</textarea></div>
      <label class="check"><input type="checkbox" name="is_independent" ${raw(a?.is_independent ? "checked" : "")}>Independent artist</label>
      <label class="check"><input type="checkbox" name="is_verified" ${raw(a?.is_verified ? "checked" : "")}>Verified</label>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`);
    $("[data-close]", m).onclick = closeModal;
    $("#af", m).addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target, b = { name: f.name.value, country: f.country.value, bio: f.bio.value, is_independent: f.is_independent.checked, is_verified: f.is_verified.checked };
      try { a ? await patch(`/api/admin/artists/${a.id}`, b) : await post("/api/admin/artists", b); closeModal(); toast("Saved"); artists(el, alive); } catch (err) { toastError(err); }
    });
  };
  $("#na").onclick = () => form(null);
  el.onclick = async (e) => {
    const ed = e.target.closest("[data-ed]"), dl = e.target.closest("[data-del]");
    if (ed) form(d.artists.find((a) => a.id === ed.dataset.ed));
    if (dl && await confirmDialog("Delete artist?", "Only artists without songs can be deleted.", { ok: "Delete", danger: true })) {
      try { await del(`/api/admin/artists/${dl.dataset.del}`); toast("Artist deleted"); artists(el, alive); } catch (err) { toastError(err); }
    }
  };
  el.onchange = async (e) => {
    const i = e.target.closest("[data-img]"); if (!i?.files[0]) return;
    try { await upload(`/api/admin/artists/${i.dataset.img}/image`, i.files[0]); toast("Photo updated"); artists(el, alive); } catch (err) { toastError(err); }
  };
}

async function albums(el, alive) {
  const [d, L] = await Promise.all([get("/api/admin/albums"), getLookups(true)]);
  if (!alive()) return;
  render(el, html`<div class="toolbar"><button class="btn primary" id="nal" style="margin-left:auto">${icon("plus")}Add album</button></div>
    <div class="table-wrap"><table class="tbl"><thead><tr><th></th><th>Album</th><th>Artist</th><th>Year</th><th>Songs</th><th></th></tr></thead><tbody>
    ${d.albums.map((a) => html`<tr><td><img src="${a.cover_url || "/img/logo.svg"}" alt=""></td><td><strong>${a.title}</strong><div class="dim small">${a.label || ""}</div></td><td>${a.artist_name}</td><td>${a.release_year || ""}</td><td>${a.song_count}</td>
      <td class="acts"><label class="btn sm">Cover<input type="file" data-cov="${a.id}" accept="image/png,image/jpeg,image/webp" class="sr-only"></label><button class="btn sm" data-ed="${a.id}">Edit</button><button class="btn sm danger" data-del="${a.id}">Delete</button></td></tr>`)}</tbody></table></div>`);
  const form = (a) => {
    const m = modal(html`<h3>${a ? "Edit album" : "Add album"}</h3><form id="alf">
      <div class="field"><label>Title</label><input class="input" name="title" required maxlength="150" value="${a?.title || ""}"></div>
      ${a ? "" : html`<div class="field"><label>Artist</label><select class="input" name="artist_id" required><option value="">Choose…</option>${L.artists.map((x) => html`<option value="${x.id}">${x.name}</option>`)}</select></div>`}
      <div class="field"><label>Release year</label><input class="input" type="number" name="release_year" min="1900" max="2100" value="${a?.release_year || ""}"></div>
      <div class="field"><label>Label</label><input class="input" name="label" maxlength="120" value="${a?.label || ""}"></div>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`);
    $("[data-close]", m).onclick = closeModal;
    $("#alf", m).addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target, b = { title: f.title.value, release_year: f.release_year.value || null, label: f.label.value };
      if (!a) b.artist_id = f.artist_id.value;
      try { a ? await patch(`/api/admin/albums/${a.id}`, b) : await post("/api/admin/albums", b); closeModal(); toast("Saved"); albums(el, alive); } catch (err) { toastError(err); }
    });
  };
  $("#nal").onclick = () => form(null);
  el.onclick = async (e) => {
    const ed = e.target.closest("[data-ed]"), dl = e.target.closest("[data-del]");
    if (ed) form(d.albums.find((a) => a.id === ed.dataset.ed));
    if (dl && await confirmDialog("Delete album?", "Songs stay in the catalogue but are no longer grouped in this album.", { ok: "Delete", danger: true })) {
      try { await del(`/api/admin/albums/${dl.dataset.del}`); albums(el, alive); } catch (err) { toastError(err); }
    }
  };
  el.onchange = async (e) => {
    const i = e.target.closest("[data-cov]"); if (!i?.files[0]) return;
    try { await upload(`/api/admin/albums/${i.dataset.cov}/cover`, i.files[0], { query: "?apply_to_songs=1" }); toast("Cover updated for the album and its songs"); albums(el, alive); } catch (err) { toastError(err); }
  };
}

// ------------------------------------------------------------------ categories & languages
async function taxonomy(el, alive) {
  const [c, l] = await Promise.all([get("/api/categories"), get("/api/admin/languages")]);
  if (!alive()) return;
  render(el, html`<div class="split">
    <div class="panel"><h3>Categories</h3>
      <form class="toolbar" id="nc"><input class="input" name="name" placeholder="New category name" required maxlength="60"><input type="color" name="color" value="#7c5cff" aria-label="Colour" style="width:44px;height:40px;border:0;background:none"><label class="check"><input type="checkbox" name="feed">Highlight feed</label><button class="btn sm primary">Add</button></form>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Name</th><th>Songs</th><th>Feed</th><th>Active</th><th></th></tr></thead><tbody>
      ${c.categories.map((x) => html`<tr><td><span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${x.color};margin-right:8px"></span>${x.name}</td><td>${x.song_count}</td>
        <td><input type="checkbox" data-cf="${x.id}" ${raw(x.has_highlight_feed ? "checked" : "")} aria-label="Highlight feed"></td><td><input type="checkbox" data-ca="${x.id}" ${raw(x.is_active ? "checked" : "")} aria-label="Active"></td>
        <td class="acts"><button class="btn sm" data-cr="${x.id}">Rename</button></td></tr>`)}</tbody></table></div></div>
    <div class="panel"><h3>Languages</h3>
      <form class="toolbar" id="nl"><input class="input" name="code" placeholder="Code (e.g. mai)" required maxlength="10" style="flex:0 0 120px;min-width:0"><input class="input" name="name" placeholder="Name" required maxlength="60" style="min-width:0"><input class="input" name="native_name" placeholder="Native name" required maxlength="60" style="min-width:0">
        <select class="input" name="region" style="flex:0 0 150px;min-width:0"><option value="indian">Indian</option><option value="international">International</option></select><button class="btn sm primary">Add</button></form>
      <div class="table-wrap"><table class="tbl"><thead><tr><th>Language</th><th>Code</th><th>Songs</th><th>Featured</th><th>Active</th></tr></thead><tbody>
      ${l.languages.map((x) => html`<tr><td>${x.name} <span class="dim">${x.native_name}</span></td><td>${x.code}</td><td>${x.song_count}</td>
        <td><input type="checkbox" data-lf="${x.code}" ${raw(x.featured ? "checked" : "")} aria-label="Featured"></td><td><input type="checkbox" data-la="${x.code}" ${raw(x.is_active ? "checked" : "")} aria-label="Active"></td></tr>`)}</tbody></table></div></div></div>`);
  const again = () => taxonomy(el, alive);
  $("#nc").addEventListener("submit", async (e) => { e.preventDefault(); const f = e.target; try { await post("/api/admin/categories", { name: f.name.value, color: f.color.value, has_highlight_feed: f.feed.checked }); toast("Category added"); again(); } catch (err) { toastError(err); } });
  $("#nl").addEventListener("submit", async (e) => { e.preventDefault(); const f = e.target; try { await post("/api/admin/languages", { code: f.code.value, name: f.name.value, native_name: f.native_name.value, region: f.region.value }); toast("Language added"); again(); } catch (err) { toastError(err); } });
  el.onchange = async (e) => {
    const t = e.target, ds = t.dataset;
    try {
      if (ds.cf) await patch(`/api/admin/categories/${ds.cf}`, { has_highlight_feed: t.checked });
      else if (ds.ca) await patch(`/api/admin/categories/${ds.ca}`, { is_active: t.checked });
      else if (ds.lf) await patch(`/api/admin/languages/${ds.lf}`, { featured: t.checked });
      else if (ds.la) await patch(`/api/admin/languages/${ds.la}`, { is_active: t.checked });
      else return;
      toast("Updated", { ms: 1200 });
    } catch (err) { t.checked = !t.checked; toastError(err); }
  };
  el.onclick = async (e) => {
    const b = e.target.closest("[data-cr]"); if (!b) return;
    const name = prompt("New name"); if (!name) return;
    try { await patch(`/api/admin/categories/${b.dataset.cr}`, { name }); again(); } catch (err) { toastError(err); }
  };
}

// ------------------------------------------------------------------ users / playlists / takedowns / plans / audit
async function users(el, alive) {
  render(el, html`<div class="toolbar"><input class="input" id="uq" type="search" placeholder="Search email or name" aria-label="Search users"></div><div id="ul">${skeletonRows()}</div>`);
  const load = async () => {
    const d = await get(`/api/admin/users?q=${encodeURIComponent($("#uq").value)}`);
    if (!alive()) return;
    const sup = session.user.admin_role === "super_admin";
    render($("#ul"), html`<div class="table-wrap"><table class="tbl"><thead><tr><th>User</th><th>Sign-in</th><th>Plan</th><th>Role</th><th>Plays</th><th>Joined</th><th></th></tr></thead><tbody>
      ${d.users.map((u) => html`<tr><td><strong>${u.display_name}</strong><div class="dim small">${u.email}</div></td><td>${u.auth_provider}</td><td>${u.plan}</td>
        <td>${sup && u.id !== session.user.id ? html`<select class="input" data-role="${u.id}" style="height:34px;min-width:150px"><option value="">Listener</option>${Object.keys(PERMS).map((r) => html`<option value="${r}" ${raw(u.admin_role === r ? "selected" : "")}>${r.replace("_", " ")}</option>`)}</select>` : (u.admin_role || "listener").replace("_", " ")}</td>
        <td>${u.plays}</td><td>${fmtDate(u.created_at)}</td>
        <td class="acts">${sup ? html`<button class="btn sm" data-grant="${u.id}">Grant Premium</button>` : ""}${u.id !== session.user.id ? html`<button class="btn sm ${u.is_active ? "danger" : ""}" data-active="${u.id}" data-v="${u.is_active ? 0 : 1}">${u.is_active ? "Suspend" : "Reactivate"}</button>` : ""}</td></tr>`)}</tbody></table></div>`);
  };
  $("#uq").addEventListener("input", debounce(load, 250));
  el.onclick = async (e) => {
    const a = e.target.closest("[data-active]"), g = e.target.closest("[data-grant]");
    try {
      if (a) { await patch(`/api/admin/users/${a.dataset.active}`, { is_active: a.dataset.v === "1" }); toast("Updated"); load(); }
      if (g) { const days = prompt("Grant Premium for how many days?", "30"); if (days) { await patch(`/api/admin/users/${g.dataset.grant}`, { grant_premium_days: +days }); toast("Premium granted"); load(); } }
    } catch (err) { toastError(err); }
  };
  el.onchange = async (e) => {
    const s = e.target.closest("[data-role]"); if (!s) return;
    try { s.value ? await put(`/api/admin/admins/${s.dataset.role}`, { role: s.value }) : await del(`/api/admin/admins/${s.dataset.role}`); toast("Role updated"); } catch (err) { toastError(err); load(); }
  };
  await load();
}

async function playlists(el, alive) {
  const d = await get("/api/admin/playlists");
  if (!alive()) return;
  render(el, d.playlists.length ? html`<div class="table-wrap"><table class="tbl"><thead><tr><th>Playlist</th><th>Owner</th><th>Songs</th><th>Followers</th><th>Visibility</th><th></th></tr></thead><tbody>
    ${d.playlists.map((p) => html`<tr><td><a href="/playlist/${p.id}" data-link><strong>${p.title}</strong></a></td><td>${p.owner_email}</td><td>${p.song_count}</td><td>${p.followers}</td><td>${p.is_public ? "Public" : "Private"}</td>
      <td class="acts">${p.is_public ? html`<button class="btn sm" data-hide="${p.id}">Make private</button>` : ""}<button class="btn sm danger" data-del="${p.id}">Delete</button></td></tr>`)}</tbody></table></div>` : emptyState("queue", "No playlists yet"));
  el.onclick = async (e) => {
    const h = e.target.closest("[data-hide]"), dl = e.target.closest("[data-del]");
    try {
      if (h) { await patch(`/api/admin/playlists/${h.dataset.hide}`, { is_public: false }); toast("Playlist hidden"); playlists(el, alive); }
      if (dl && await confirmDialog("Delete playlist?", "This removes it for its owner and followers.", { ok: "Delete", danger: true })) { await del(`/api/admin/playlists/${dl.dataset.del}`); playlists(el, alive); }
    } catch (err) { toastError(err); }
  };
}

async function takedowns(el, alive) {
  const d = await get("/api/admin/takedowns");
  if (!alive()) return;
  render(el, html`<p class="muted small">To remove a song, open it under Songs and choose “Take down”. Every takedown and restoration is recorded here.</p>
    ${d.takedowns.length ? html`<div class="table-wrap"><table class="tbl"><thead><tr><th>When</th><th>Song</th><th>Action</th><th>Claimant</th><th>Reason</th><th>Admin</th></tr></thead><tbody>
    ${d.takedowns.map((t) => html`<tr><td>${fmtDate(t.created_at)}</td><td>${t.title}</td><td><span class="status ${t.action === "restored" ? "published" : "taken_down"}">${t.action.replace("_", " ")}</span></td><td>${t.claimant}</td><td class="small">${t.reason}</td><td class="small">${t.admin_email}</td></tr>`)}</tbody></table></div>` : emptyState("shield", "No takedowns recorded")}`);
}

async function plans(el, alive) {
  const d = await get("/api/admin/plans");
  if (!alive()) return;
  render(el, html`<div class="split"><div>
    ${d.plans.map((p) => html`<div class="panel"><form data-plan="${p.id}"><h3>${p.name} <span class="dim small">${p.id}</span></h3>
      <div class="form-grid"><div class="field"><label>Name</label><input class="input" name="name" value="${p.name}" maxlength="60"></div>
      <div class="field"><label>Price (${p.currency}, in paise/cents)</label><input class="input" type="number" name="price_minor" value="${p.price_minor}" min="0" ${raw(p.tier === "free" ? "disabled" : "")}><span class="hint">${fmtMoney(p.price_minor, p.currency)} per ${p.interval}</span></div></div>
      <div class="field"><label>Features (one per line)</label><textarea class="input" name="features" style="font-family:inherit;min-height:100px">${p.features.join("\n")}</textarea></div>
      <label class="check"><input type="checkbox" name="is_active" ${raw(p.is_active ? "checked" : "")}>Offered to listeners</label>
      <button class="btn primary sm">Save plan</button></form></div>`)}</div>
    <div><div class="panel"><h3>Free plan rules</h3><form id="setf">
      <div class="field"><label>Skips per hour</label><input class="input" type="number" name="free_skips_per_hour" value="${d.settings.free_skips_per_hour}" min="0" max="1000"></div>
      <div class="field"><label>Show a house ad every N full songs (0 = never)</label><input class="input" type="number" name="free_ad_every_n_tracks" value="${d.settings.free_ad_every_n_tracks}" min="0" max="1000"></div>
      <div class="field"><label>Ad length (seconds)</label><input class="input" type="number" name="ad_duration_seconds" value="${d.settings.ad_duration_seconds}" min="0" max="60"></div>
      <button class="btn primary sm">Save rules</button></form></div>
      <div class="panel"><h3>Payment providers</h3>${d.providers.map((p) => html`<p class="small"><strong>${p.label}</strong> — ${p.enabled ? html`<span style="color:#4ade80">enabled</span>` : html`<span class="dim">not configured</span>`}</p>`)}
        <p class="dim small">Providers are switched on with environment variables (see README). Real payments stay off until credentials are supplied.</p></div></div></div>`);
  el.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      if (f.id === "setf") { await patch("/api/admin/settings", Object.fromEntries(new FormData(f))); toast("Rules saved"); }
      else if (f.dataset.plan) {
        const b = { name: f.name.value, features: f.features.value.split("\n").map((x) => x.trim()).filter(Boolean), is_active: f.is_active.checked };
        if (!f.price_minor.disabled) b.price_minor = +f.price_minor.value;
        await patch(`/api/admin/plans/${f.dataset.plan}`, b); toast("Plan saved");
      }
    } catch (err) { toastError(err); }
  });
}

async function audit(el, alive) {
  const d = await get("/api/admin/audit");
  if (!alive()) return;
  render(el, d.entries.length ? html`<div class="table-wrap"><table class="tbl"><thead><tr><th>When</th><th>Admin</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead><tbody>
    ${d.entries.map((a) => html`<tr><td>${new Date(a.at * 1000).toLocaleString()}</td><td class="small">${a.admin_email || "system"}</td><td>${a.action}</td><td class="small">${a.entity} ${a.entity_id || ""}</td><td class="small dim" style="max-width:360px;word-break:break-word">${a.details || ""}</td></tr>`)}</tbody></table></div>` : emptyState("list", "No admin activity yet"));
}
