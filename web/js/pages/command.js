// Command: AI-view video synced with the map + live alert feed. Playback of a saved drive
// (#/command/<run>) or a live session (#/command/live/<id>: processing, replay or phone).
import { $, api, bars, caseHref, coord, esc, healthOf, icons, mmss, nearestAuthority, pageHead, weatherAt, wxIcon, prioTag, store, toast, ACTION, BLOCKING, CAT, PRIO_OF } from "../core.js";
import { userRuns } from "../core.js";
import { addCase, drawRoute, haversine, makeMap, renderClosures, renderCondition, vehicleIcon, ROUTE_COL } from "../map.js";

export default async function render(el, params, query) {
  // ---------- choose what to show
  if (!params.length) {
    const live = (await api("/api/live").catch(() => []))[0];
    if (live) { location.replace(`#/command/live/${encodeURIComponent(live.id)}`); return; }
    const runs = userRuns(await api("/api/runs"));
    const pick = runs.find((r) => r.media?.annotated) || runs[0];
    if (pick) { location.replace(`#/command/${encodeURIComponent(pick.id)}`); return; }
    el.innerHTML = `${pageHead("06", "Command", "Command")}<p class="muted">No drives yet. Use <b>New drive</b> or <b>Go live</b>.</p>`;
    return;
  }
  const isLive = params[0] === "live";
  const id = isLive ? params[1] : params[0];
  const runs = userRuns(await api("/api/runs").catch(() => []));

  el.innerHTML = `
    ${pageHead("06", "Command", isLive ? "Live" : "Command", `
      <label class="field"><span>Drive</span><select class="select" id="cRun">
        ${isLive ? `<option value="">● LIVE · ${esc(id)}</option>` : ""}
        ${runs.map((r) => `<option value="${esc(r.id)}" ${r.id === id && !isLive ? "selected" : ""}>${esc(r.id)} · ${r.hazards} hazards</option>`).join("")}</select></label>
      ${isLive ? "" : `<a class="btn" href="/report.html?run=${encodeURIComponent(id)}" target="_blank" rel="noopener"><i data-lucide="file-text"></i>Report</a>`}`)}
    <div class="command">
      <div class="cmd-left">
        <div class="cmd-stage">
          <span class="cmd-tag label"><span class="rec" aria-hidden="true"></span><span id="cTag">${isLive ? "Live AI view" : "AI view"}</span></span>
          <video id="cVideo" playsinline muted preload="auto" ${isLive ? "hidden" : ""}></video>
          <img id="cLive" alt="Latest analysed frame" ${isLive ? "" : "hidden"}>
          <svg class="cmd-boxes" id="cBoxes" preserveAspectRatio="xMidYMid meet" aria-hidden="true" ${isLive ? "" : "hidden"}></svg>
          <button class="cmd-play" id="cPlay" aria-label="Play drive" ${isLive ? "hidden" : ""}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4l13 8-13 8z"/></svg></button>
          <div class="cmd-empty label" id="cEmpty" hidden>No AI-view video for this drive. Re-process it to generate one.</div>
        </div>
        <div class="cmd-panel">
        <div class="timeline" id="cTimeline" role="slider" tabindex="0" aria-label="Drive timeline">
          <div class="track"></div><div class="fill" id="cFill"></div><div id="cTicks"></div><span class="head" id="cHead"></span>
        </div>
        <div class="cmd-controls">
          <button class="btn sm" id="cToggle" ${isLive ? "hidden" : ""}><i data-lucide="play"></i><span>Play</span></button>
          <span class="time" id="cTime">0:00</span>
          <button class="btn sm danger" id="cStop" ${isLive ? "" : "hidden"}>Stop &amp; save</button>
          <select class="select" id="cRate" aria-label="Playback speed" ${isLive ? "hidden" : ""}><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select>
        </div>
        </div>
        <div class="cmd-stats" id="cStats"></div>
        <div class="cmd-analytics" id="cAnalytics"></div>
      </div>
      <div class="cmd-map"><div class="map" id="cMap"></div><div class="closure-note" id="cClosure" hidden></div></div>
      <aside class="cmd-alerts" aria-label="Alerts">
        <header><h2>Alerts <span class="count" id="cCount">0</span></h2><button class="btn sm" id="cSound" aria-pressed="true"><i data-lucide="volume-2"></i></button></header>
        <ol class="alert-list" id="cAlerts" aria-live="polite"></ol>
        <p class="alerts-empty" id="cAlertsEmpty">Alerts appear the moment the AI confirms a hazard.</p>
      </aside>
    </div>`;
  icons(el);
  $("#cRun", el).addEventListener("change", (e) => { if (e.target.value) location.hash = `#/command/${encodeURIComponent(e.target.value)}`; });

  // ---------- shared state
  const map = makeMap($("#cMap", el));
  const routeLayer = L.layerGroup().addTo(map), condLayer = L.layerGroup().addTo(map), closLayer = L.layerGroup().addTo(map), hzLayer = L.featureGroup().addTo(map);
  let vehicle = null, trail = null, follow = true, soundOn = true, audio = null, raf = 0, ws = null, closures = [];
  let items = [];               // case-like hazard objects (from the saved run, or streamed live)
  let revealT = null, routeLL = [], km = 0, liveStats = null, done = false;
  const alerted = new Set();
  const { cases } = await store.cases().catch(() => ({ cases: [] }));
  const caseOf = (hid) => cases.find((c) => c.run_id === id && c.hazard_id === hid);

  const toItem = (f) => {
    const p = f.properties, c = !isLive && caseOf(p.id);
    return { ...p, hazard_id: p.id, run_id: c ? id : null, number: c?.number || p.id, priority: c?.priority || PRIO_OF[p.severity] || "P3",
      status: c?.status || "new", label: CAT[p.category]?.label || p.category, geometry: f.geometry,
      snapshot: p.snapshot ? `/runs/${encodeURIComponent(id)}/${p.snapshot}` : null };
  };

  // ---------- rendering
  const visible = () => items.filter((i) => revealT == null || i.video_time_s <= revealT);
  function drawHazards() {
    hzLayer.clearLayers();
    const v = visible();
    v.forEach((i) => addCase(hzLayer, i));
    renderCondition(condLayer, isLive ? (trail?.getLatLngs().map((p) => [p.lat, p.lng]) || []) : routeLL, v);
    drawStats(v);
  }
  function drawStats(v = visible()) {
    const scanned = isLive ? (liveStats?.route_km || 0) : km;
    const h = healthOf(v.map((i) => i.severity), scanned);
    const p1 = v.filter((i) => i.priority === "P1").length;
    $("#cStats", el).innerHTML = `
      <div><span class="label">Hazards</span><b class="num">${v.length}</b></div>
      <div class="${p1 ? "signal" : ""}"><span class="label">P1 critical</span><b class="num">${p1}</b></div>
      <div><span class="label">Scanned</span><b class="num">${scanned.toFixed(2)}<span style="font-size:.5em"> km</span></b></div>
      <div class="${h.tone === "bad" ? "signal" : ""}"><span class="label">Health</span><b class="num">${h.grade}<span style="font-size:.5em"> ${h.score}</span></b></div>`;
    const byType = {};
    v.forEach((i) => { byType[i.label] = (byType[i.label] || 0) + 1; });
    $("#cAnalytics", el).innerHTML = `<div class="section-head"><h2>By type</h2><span class="label">${isLive ? `${liveStats?.fps ?? 0} fps · latency ${liveStats?.latency_s ?? 0}s` : ""}</span></div>
      ${bars(Object.entries(byType).sort((a, b) => b[1] - a[1]), { red: (n) => ["Flooding", "Landslide", "Power line down", "Fallen tree"].includes(n) })}`;
  }

  // ---------- alerts
  const ensureAudio = () => { try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume?.(); } catch { /* no audio */ } };
  function beep(prio) {
    if (!soundOn || !audio || prio === "P3") return;
    (prio === "P1" ? [880, 660, 880] : [660]).forEach((fq, k) => {
      const o = audio.createOscillator(), g = audio.createGain(), t0 = audio.currentTime + k * .12;
      o.type = "square"; o.frequency.value = fq;
      g.gain.setValueAtTime(.0001, t0); g.gain.exponentialRampToValueAtTime(.08, t0 + .01); g.gain.exponentialRampToValueAtTime(.0001, t0 + .1);
      o.connect(g).connect(audio.destination); o.start(t0); o.stop(t0 + .11);
    });
  }
  function alertHtml(i) {
    const cl = closures.find((c) => c.hazard_id === i.hazard_id);
    const route = !BLOCKING.has(i.category) || i.priority === "P3" ? ""
      : cl?.detour ? `<div class="route">↪ Detour ${cl.detour.km} km · ~${cl.detour.minutes} min</div>`
      : cl ? `<div class="route bad">✕ Road blocked · no alternative</div>` : `<div class="route bad">Road may be impassable</div>`;
    return `<div class="t">${prioTag(i.priority)}<span>${esc(i.label)}</span></div>
      <div class="m">${esc(i.number)} · ${Math.round(i.confidence * 100)}% · t=${mmss(i.video_time_s)}${i.extent_m >= 15 ? ` · ${i.extent_m} m` : ""}</div>
      <div class="m">${coord(i.centroid)}</div>${route}
      <div class="m" style="color:#000">→ ${esc(ACTION[i.category] || "Inspect")}</div>
      <div class="m" data-auth></div>
      <div class="m" data-wx></div>
      <div class="acts"><button class="link-btn" data-locate="${esc(i.hazard_id)}">Locate</button>
        ${i.run_id ? `<a class="link-btn" href="${caseHref(i)}">Case</a>` : ""}
        <a class="link-btn" href="https://www.google.com/maps/search/?api=1&query=${i.centroid[1]},${i.centroid[0]}" target="_blank" rel="noopener">Maps</a></div>`;
  }
  // responsible NHAI office + first phone number under each alert (looked up per location)
  function fillAuthority(li, i) {
    weatherAt(i.centroid, isLive ? null : i.first_seen).then((w) => {
      const slot = li.querySelector("[data-wx]");
      if (!slot || !w) return;
      slot.innerHTML = `${wxIcon(w)} ${esc(w.label)}, ${Math.round(w.temp_c)}°C${w.rain_24h ? ` · ${w.rain_24h} mm/24h` : ""}${w.flags.length ? ` · <b>${esc(w.flags[0])}</b>` : ""}`;
      icons(slot);
    });
    nearestAuthority(i.centroid).then((a) => {
      const slot = li.querySelector("[data-auth]");
      if (!slot) return;
      slot.innerHTML = a ? `⚑ ${esc(a.office_code)}${a.phones[0] ? ` · <a href="tel:${a.phones[0].replace(/[^\d+]/g, "")}">${esc(a.phones[0])}</a>` : ""}` : "";
    });
  }
  function addAlert(i, animate) {
    const list = $("#cAlerts", el), ex = list.querySelector(`[data-id="${CSS.escape(i.hazard_id)}"]`);
    if (ex) { ex.className = `alert ${i.priority}`; ex.innerHTML = alertHtml(i); fillAuthority(ex, i); return; }
    alerted.add(i.hazard_id);
    const li = document.createElement("li");
    li.className = `alert ${i.priority}${animate ? " in" : ""}`;
    li.dataset.id = i.hazard_id;
    li.innerHTML = alertHtml(i);
    fillAuthority(li, i);
    list.prepend(li);
    $("#cCount", el).textContent = alerted.size;
    $("#cAlertsEmpty", el).hidden = true;
    if (animate) beep(i.priority);
  }
  function clearAlerts() { alerted.clear(); $("#cAlerts", el).innerHTML = ""; $("#cCount", el).textContent = "0"; $("#cAlertsEmpty", el).hidden = false; }
  $("#cAlerts", el).addEventListener("click", (e) => {
    const hid = e.target.closest("[data-locate]")?.dataset.locate;
    const i = items.find((x) => x.hazard_id === hid);
    if (!i) return;
    follow = false;
    map.flyTo([i.centroid[1], i.centroid[0]], Math.max(map.getZoom(), 18), { duration: .5 });
  });
  $("#cSound", el).addEventListener("click", (e) => {
    soundOn = !soundOn; ensureAudio();
    e.currentTarget.setAttribute("aria-pressed", soundOn);
    e.currentTarget.innerHTML = `<i data-lucide="${soundOn ? "volume-2" : "volume-x"}"></i>`;
    icons(e.currentTarget);
  });
  document.addEventListener("pointerdown", ensureAudio, { once: true });

  // ---------- closures
  async function loadClosures() {
    if (!items.some((i) => BLOCKING.has(i.category) && i.priority !== "P3")) return;
    const d = await api(`/api/runs/${encodeURIComponent(id)}/closures`).catch(() => null);
    if (!d) return;
    closures = d.closures;
    renderClosures(closLayer, closures);
    const box = $("#cClosure", el);
    box.innerHTML = `<b>Closures &amp; detours</b>${closures.map((c) => `<div>✕ <b>${esc(CAT[c.category]?.label ?? c.category)}</b> (${esc(caseOf(c.hazard_id)?.number || c.hazard_id)}) blocks the road →
      ${c.detour ? `detour <b>${c.detour.km} km · ~${c.detour.minutes} min</b> <button class="link-btn" data-detour="${esc(c.hazard_id)}">View</button>` : "<b>no alternative road</b>"}</div>`).join("")}`;
    box.hidden = false;
    items.filter((i) => alerted.has(i.hazard_id)).forEach((i) => addAlert(i, false));
  }
  $("#cClosure", el).addEventListener("click", (e) => {
    const c = closures.find((x) => x.hazard_id === e.target.closest("[data-detour]")?.dataset.detour);
    if (!c?.detour) return;
    follow = false;
    map.flyToBounds(L.latLngBounds(c.detour.coords.map(([x, y]) => [y, x])).extend([c.point[1], c.point[0]]), { padding: [50, 50], duration: .7 });
  });

  function placeVehicle(lat, lon, heading) {
    const ll = [lat, lon];
    if (!vehicle) vehicle = L.marker(ll, { icon: vehicleIcon(heading), zIndexOffset: 1000, keyboard: false }).addTo(routeLayer);
    else { vehicle.setLatLng(ll); vehicle.setIcon(vehicleIcon(heading)); }
    if (follow && !map.getBounds().pad(-0.2).contains(ll)) map.panTo(ll, { animate: true, duration: .3 });
  }

  // ======================================================================== live
  if (isLive) {
    trail = L.polyline([], { color: ROUTE_COL, weight: 5, opacity: .95 }).addTo(routeLayer);
    const routePlan = L.polyline([], { color: ROUTE_COL, weight: 3, opacity: .45, dashArray: "6 8" }).addTo(routeLayer);
    // phone sessions: raw camera frames arrive at full rate, detections come separately as boxes
    const drawBoxes = ({ w, h, dets }) => {
      const svg = $("#cBoxes", el);
      svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
      svg.innerHTML = dets.map(([x1, y1, x2, y2, cat, conf]) =>
        `<rect x="${x1}" y="${y1}" width="${x2 - x1}" height="${y2 - y1}"/><text x="${x1}" y="${Math.max(y1 - 6, 14)}">${esc(cat)} ${conf.toFixed(2)}</text>`).join("");
    };
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live/${encodeURIComponent(id)}`);
    ws.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      switch (ev.type) {
        case "status":
          $("#cTime", el).textContent = { queued: "Waiting for GPU…", calibrating: "Calibrating camera…", running: "Running", finalizing: "Saving drive…" }[ev.state] || ev.state;
          break;
        case "route":
          routePlan.setLatLngs(ev.feature.geometry.coordinates.map(([x, y]) => [y, x]));
          if (routePlan.getLatLngs().length > 1) map.fitBounds(routePlan.getBounds(), { padding: [50, 50], maxZoom: 17 });
          break;
        case "snapshot":
          trail.setLatLngs(ev.trail.map(([x, y]) => [y, x]));
          items = ev.features.map(toItem);
          items.forEach((i) => addAlert(i, false));
          liveStats = ev.stats;
          drawHazards();
          break;
        case "pose":
          trail.addLatLng([ev.lat, ev.lon]);
          placeVehicle(ev.lat, ev.lon, ev.heading);
          if (map.getZoom() < 15) map.setView([ev.lat, ev.lon], 17);
          break;
        case "hazard": {
          const i = toItem(ev.feature);
          const k = items.findIndex((x) => x.hazard_id === i.hazard_id);
          if (k >= 0) items[k] = i; else items.push(i);
          drawHazards();
          addAlert(i, ev.new);
          break;
        }
        case "frame": $("#cLive", el).src = `data:image/jpeg;base64,${ev.jpeg}`; break;
        case "boxes": drawBoxes(ev); break;
        case "stats":
          liveStats = ev;
          $("#cTime", el).textContent = `${ev.frames} frames · ${ev.dropped} dropped`;
          if (ev.progress != null) { $("#cFill", el).style.width = `${ev.progress * 100}%`; $("#cHead", el).style.left = `${ev.progress * 100}%`; }
          drawStats();
          break;
        case "error":
          $("#cTime", el).textContent = `Error: ${ev.message}`;
          done = true;
          break;
        case "done":
          done = true;
          store.invalidate();
          if (ev.run_id) {
            toast(`Drive saved: ${ev.summary?.hazards ?? ""} hazards are now cases`);
            setTimeout(() => { location.hash = `#/command/${encodeURIComponent(ev.run_id)}`; }, 900);
          } else {
            toast("Nothing was streamed: session not saved");
            setTimeout(() => { location.hash = "#/drives"; }, 1500);
          }
          break;
      }
    };
    ws.onclose = () => { if (!done) $("#cTime", el).textContent = "Disconnected"; };
    $("#cStop", el).addEventListener("click", async (e) => {
      e.currentTarget.disabled = true;
      await api(`/api/live/${encodeURIComponent(id)}/stop`, { method: "POST" }).catch(() => {});
    });
    drawStats();
    return () => { ws?.close(); map.remove(); };
  }

  // ======================================================================== playback
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  items = run.hazards.features.map(toItem);
  routeLL = (run.trajectory.features[0]?.geometry.coordinates || []).map(([x, y]) => [y, x]);
  const media = run.summary.media || {};
  const rows = media.timeline ? (await fetch(`/runs/${encodeURIComponent(id)}/${media.timeline}`).then((r) => r.json()).catch(() => ({ rows: [] }))).rows : [];
  const video = $("#cVideo", el);

  if (!media.annotated || !rows.length) {
    // no AI video: show the whole drive statically
    $("#cEmpty", el).hidden = false; $("#cPlay", el).hidden = true; video.hidden = true;
    drawRoute(routeLayer, routeLL.map(([a, b]) => [b, a]));
    km = run.summary.route_km || 0;
    items.forEach((i) => addAlert(i, false));
    drawHazards();
    const b = L.featureGroup([...routeLayer.getLayers(), ...hzLayer.getLayers()]).getBounds();
    if (b.isValid()) map.fitBounds(b, { padding: [40, 40], maxZoom: 17 });
    loadClosures();
    return () => map.remove();
  }

  video.src = `/runs/${encodeURIComponent(id)}/${media.annotated}`;
  drawRoute(routeLayer, routeLL.map(([a, b]) => [b, a]), { faint: true });
  trail = L.polyline([], { color: ROUTE_COL, weight: 5, opacity: .95 }).addTo(routeLayer);
  revealT = -1;
  const dur = () => video.duration || rows.at(-1)?.[0] || 1;
  $("#cTicks", el).innerHTML = items.map((i) => `<button class="tick ${i.priority}" style="left:${Math.min(99.4, (i.video_time_s / (rows.at(-1)?.[0] || 1)) * 100)}%"
    data-t="${i.video_time_s}" title="${esc(i.number)} ${esc(i.label)} · ${mmss(i.video_time_s)}" aria-label="Jump to ${esc(i.label)} at ${mmss(i.video_time_s)}"></button>`).join("");
  const b0 = L.latLngBounds(routeLL);
  if (b0.isValid()) map.fitBounds(b0, { padding: [40, 40], maxZoom: 17 });
  drawHazards();
  loadClosures();

  function poseAt(t) {
    if (t <= rows[0][0]) return rows[0];
    if (t >= rows.at(-1)[0]) return rows.at(-1);
    let lo = 0, hi = rows.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (rows[m][0] <= t) lo = m; else hi = m; }
    const a = rows[lo], b = rows[hi], k = (t - a[0]) / (b[0] - a[0] || 1);
    return [t, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k, b[3] ?? a[3], a[4]];
  }
  function reveal(t) {
    const prev = revealT;
    revealT = t;
    if (prev != null && t < prev) {                      // scrubbed back: rebuild quietly
      clearAlerts();
      items.filter((i) => i.video_time_s <= t).sort((a, b) => a.video_time_s - b.video_time_s).forEach((i) => addAlert(i, false));
      drawHazards();
      return;
    }
    const fresh = items.filter((i) => i.video_time_s <= t && i.video_time_s > (prev ?? -1));
    if (!fresh.length) return;
    drawHazards();
    fresh.sort((a, b) => a.video_time_s - b.video_time_s).forEach((i) => {
      addAlert(i, true);
      hzLayer.eachLayer((g) => g.eachLayer?.((l) => { if (l.options?.title?.startsWith(i.number)) l.getElement()?.querySelector(".mk")?.classList.add("drop"); }));
    });
  }
  let lastStats = 0;
  function tick() {
    const t = video.currentTime || 0;
    $("#cFill", el).style.width = `${(t / dur()) * 100}%`;
    $("#cHead", el).style.left = `${(t / dur()) * 100}%`;
    $("#cTime", el).textContent = `${mmss(t)} / ${mmss(dur())}`;
    const p = poseAt(t);
    placeVehicle(p[1], p[2], p[3]);
    const done = rows.filter((r) => r[0] <= t).map((r) => [r[1], r[2]]).concat([[p[1], p[2]]]);
    trail.setLatLngs(done);
    reveal(t);
    const now = performance.now();
    if (now - lastStats > 400) {
      lastStats = now;
      km = done.reduce((s, q, k) => (k ? s + haversine(done[k - 1], q) : 0), 0) / 1000;
      drawStats();
    }
    if (!video.paused && !video.ended) raf = requestAnimationFrame(tick);
  }
  const seek = (t) => { video.currentTime = Math.max(0, Math.min(t, dur())); tick(); };
  const toggle = () => {
    ensureAudio();
    if (video.paused || video.ended) { if (video.ended) seek(0); follow = true; video.play(); } else video.pause();
  };
  video.addEventListener("loadedmetadata", () => { const t = Number(query.get("t")); if (t) seek(Math.max(0, t - 1)); else tick(); });
  video.addEventListener("play", () => { $("#cPlay", el).hidden = true; $("#cToggle span", el).textContent = "Pause"; cancelAnimationFrame(raf); tick(); });
  video.addEventListener("pause", () => { $("#cPlay", el).hidden = false; $("#cToggle span", el).textContent = "Play"; });
  video.addEventListener("seeked", tick);
  $("#cPlay", el).addEventListener("click", toggle);
  $("#cToggle", el).addEventListener("click", toggle);
  $("#cRate", el).addEventListener("change", (e) => { video.playbackRate = Number(e.target.value); });
  $("#cTicks", el).addEventListener("click", (e) => { const b = e.target.closest(".tick"); if (b) { e.stopPropagation(); seek(Number(b.dataset.t) - 1); } });
  $("#cTimeline", el).addEventListener("click", (e) => {
    if (e.target.closest(".tick")) return;
    const r = e.currentTarget.getBoundingClientRect();
    seek(((e.clientX - r.left) / r.width) * dur());
  });
  $("#cTimeline", el).addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") seek(video.currentTime + 5);
    if (e.key === "ArrowLeft") seek(video.currentTime - 5);
    if (e.key === " ") { e.preventDefault(); toggle(); }
  });
  return () => { video.pause(); cancelAnimationFrame(raf); map.remove(); };
}
