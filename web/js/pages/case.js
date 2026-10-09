import { $, $$, api, ago, authorityName, avatar, catTile, coord, date, esc, icons, mailLinks, mmss, phoneLinks, prioTag, short, sla, statusTag, store, toast, ACTION, STATUSES, STATUS_LABEL } from "../core.js";
import { addCase, drawRoute, makeMap, renderClosures } from "../map.js";

const NEXT = { new: ["verified", "Verify"], verified: ["assigned", "Assign"], assigned: ["in_progress", "Start work"], in_progress: ["resolved", "Resolve"] };

export default async function render(el, [runId, hazardId]) {
  let data = await api(`/api/cases/${encodeURIComponent(runId)}/${encodeURIComponent(hazardId)}`);
  let map = null;
  // NHAI offices near this hazard, for the authority override
  const [lon0, lat0] = data.case.centroid;
  const offices = (await api(`/api/authorities?lat=${lat0}&lon=${lon0}&n=10`).catch(() => ({ offices: [] }))).offices;

  const reportUrl = (c) => `${location.origin}/report.html?run=${encodeURIComponent(c.run_id)}&hazard=${encodeURIComponent(c.hazard_id)}`;
  const authorityText = (c) => {
    const a = c.authority, e = c.escalation;
    return [`${c.number} · ${c.label} (${c.priority})`, `Location: ${c.centroid[1].toFixed(6)}, ${c.centroid[0].toFixed(6)}`,
      `Map: https://www.google.com/maps/search/?api=1&query=${c.centroid[1]},${c.centroid[0]}`, `Report: ${reportUrl(c)}`,
      c.weather ? `Weather at detection: ${c.weather.label}, ${Math.round(c.weather.temp_c)}°C, ${c.weather.rain_24h} mm rain in 24 h${c.weather.flags.length ? ` (${c.weather.flags.join("; ")})` : ""}` : "", "",
      a ? `Responsible: NHAI ${a.office_code}, ${a.designation}${a.officer_name ? ` (${a.officer_name})` : ""}` : "Responsible: no NHAI office nearby",
      a ? `Address: ${a.address}` : "", a?.phones.length ? `Phone: ${a.phones.join(" / ")}` : "", a?.emails.length ? `Email: ${a.emails.join(" / ")}` : "",
      e ? `Escalation: NHAI ${e.office_code}, ${e.designation}${e.phones.length ? `, ${e.phones.join(" / ")}` : ""}` : ""].filter((x, i) => x || i === 5).join("\n");
  };
  const mailto = (c) => {
    const to = [...(c.authority?.emails || []), ...(c.escalation?.emails || [])].slice(0, 1).join(",");
    const cc = (c.escalation?.emails || []).filter((m) => m !== to).join(",");
    const q = new URLSearchParams({ subject: `Road hazard ${c.number}: ${c.label} (${c.priority})`, body: authorityText(c) });
    if (cc) q.set("cc", cc);
    return `mailto:${to}?${q.toString().replace(/\+/g, "%20")}`;
  };
  // weather at capture + 48 h outlook, loaded after first paint
  let wx;
  const wxCard = () => {
    const cell = (cap, val, title = "") => `<div title="${esc(title)}"><span class="tc-cap">${cap}</span><b>${val}</b></div>`;
    if (wx === undefined) return cell("Weather", "Loading…");
    const w = wx?.at_capture, o = wx?.outlook;
    if (!w) return cell("Weather", `<span class="muted">Not available</span>`);
    const a = w.at;
    const note = [...w.flags.map((f) => `<span class="chip warn"><i></i>${esc(f)}</span>`),
      o ? `<span class="tc-cap">Next 48 h: ${o.rain_48h} mm${o.rain_in_h != null ? `, rain in ~${o.rain_in_h} h` : ""} · <b style="color:var(--ink)">${esc(o.advice)}</b></span>` : ""].filter(Boolean);
    return `
      ${cell(`Weather${a.is_day ? "" : " · night"}`, `<i data-lucide="${esc(a.icon)}" class="wx" aria-hidden="true"></i> ${Math.round(a.temp_c)}°C ${esc(a.label)}`, `${w.source} · ${wx.attribution}`)}
      ${cell("Rain 24 h · 72 h", `${w.rain_24h} · ${w.rain_72h} mm`, `${a.precip_mm} mm in the hour of detection`)}
      ${cell("Wind · gusts", `${a.wind_kmh} · ${a.gust_kmh} km/h`)}
      ${cell(a.visibility_km == null ? "Humidity" : "Humidity · vis.", `${a.humidity}%${a.visibility_km == null ? "" : ` · ${a.visibility_km} km`}`)}
      ${note.length ? `<div class="tc-wx-note">${note.join("")}</div>` : ""}`;
  };
  const drawWx = () => { const box = $("#wxBody", el); if (box) { box.innerHTML = wxCard(); icons(box); } };
  const loadWx = () => api(`/api/cases/${encodeURIComponent(runId)}/${encodeURIComponent(hazardId)}/weather`)
    .then((r) => { wx = r; }).catch(() => { wx = null; }).finally(drawWx);

  const authorityCard = (c) => {
    const a = c.authority, e = c.escalation;
    if (!a) return `<section class="card"><div class="section-head"><h2>Responsible authority</h2></div>
      <p class="muted" style="margin:0">No NHAI office within 300 km of this hazard. Assign the department above.</p></section>`;
    return `<section class="card">
      <div class="section-head"><h2>Responsible authority</h2><span class="label">${c.authority_auto ? "Auto · nearest office" : "Set manually"}</span></div>
      <dl class="facts">
        <dt>Office</dt><dd><strong>NHAI ${esc(a.office_code)}</strong><br><span class="muted">${esc(a.designation)}</span></dd>
        ${a.officer_name ? `<dt>Officer</dt><dd>${esc(a.officer_name)}</dd>` : ""}
        <dt>Address</dt><dd>${esc(a.address)}<br><span class="muted">${esc(a.city)}, ${esc(a.state)} · ${a.distance_km} km from hazard</span></dd>
        <dt>Phone</dt><dd>${phoneLinks(a) || `<span class="muted">Not listed</span>`}</dd>
        <dt>Email</dt><dd>${mailLinks(a) || `<span class="muted">Not listed</span>`}</dd>
        ${e ? `<dt>Escalation</dt><dd>NHAI ${esc(e.office_code)}<br><span class="muted">${esc(e.designation)}, ${esc(e.city)}</span>
          ${e.phones.length || e.emails.length ? `<br>${[phoneLinks(e), mailLinks(e)].filter(Boolean).join(" · ")}` : ""}</dd>` : ""}
        ${a.note ? `<dt>Note</dt><dd class="muted">${esc(a.note)}</dd>` : ""}
      </dl>
      <div class="actions" style="margin-top:14px">
        <a class="btn primary" href="${esc(mailto(c))}"><i data-lucide="mail"></i>Email authority</a>
        <button class="btn" id="copyAuth"><i data-lucide="copy"></i>Copy details</button>
      </div>
      <p class="label" style="margin:12px 0 0">Source: NHAI office list, ${esc(a.data_as_of)}. Verify contacts before dispatch.</p>
    </section>`;
  };

  const draw = () => {
    const { case: c, activity, closure, departments } = data;
    const [lon, lat] = c.centroid;
    const idx = STATUSES.indexOf(c.status);
    const next = NEXT[c.status];
    const sl = sla(c);
    const video = c.drive.annotated ? `/runs/${encodeURIComponent(c.run_id)}/annotated.mp4#t=${Math.max(0, c.video_time_s - 1)}` : null;
    el.innerHTML = `
      <header class="page-head">
        <div><span class="crumb"><a href="#/cases" style="text-decoration:none">Cases</a> / ${esc(c.number)}</span>
          <h1 style="display:flex;align-items:center;gap:12px">${catTile(c.category)}${esc(c.label)}</h1></div>
        <div class="tools">
          <a class="btn" href="/report.html?run=${encodeURIComponent(c.run_id)}&hazard=${encodeURIComponent(c.hazard_id)}" target="_blank" rel="noopener"><i data-lucide="file-text"></i>Report</a>
          <a class="btn primary" href="#/command/${encodeURIComponent(c.run_id)}?t=${c.video_time_s}"><i data-lucide="radar"></i>Watch in Command</a>
        </div>
      </header>

      <div class="case-top">
        <section class="track-card">
          <div class="tc-row">
            <div><span class="tc-cap">Case number</span><b>${esc(c.number)}</b></div>
            <div style="display:flex;gap:6px">${prioTag(c.priority)}${statusTag(c.status)}</div>
          </div>
          <hr class="divider">
          <div class="tc-time">
            <div><span class="tc-cap">Detected</span><b>${short(new Date(c.first_seen).getTime())}</b></div>
            <div class="tc-track"><span class="pin-mini" style="left:${(sl.done ? 1 : sl.frac) * 100}%"></span></div>
            <div class="right"><span class="tc-cap">${sl.done ? "Resolved" : "Target fix"}</span><b>${short(sl.done ? new Date(c.resolved_at).getTime() : sl.end)}</b>
              ${sl.overdue ? `<span class="chip warn" style="margin-top:4px"><i></i>Overdue</span>` : ""}</div>
          </div>
          <hr class="divider">
          <div class="tc-facts" id="wxBody">${wxCard()}</div>
          <hr class="divider">
          <div class="tc-resp">
            ${avatar(c.assignee || c.department, "lg")}
            <div class="who"><span class="tc-cap">Responsible</span><b>${esc(c.assignee || c.department)}</b>
              <span class="tc-cap" title="${esc(authorityName(c.authority))}">${c.authority ? `NHAI ${esc(c.authority.office_code)}` : "No NHAI office nearby"}</span></div>
            ${c.authority?.phones[0] ? `<a class="btn round" href="tel:${c.authority.phones[0].replace(/[^\d+]/g, "")}" aria-label="Call ${esc(c.authority.office_code)}"><i data-lucide="phone"></i></a>` : ""}
            <a class="btn round" href="https://www.google.com/maps/search/?api=1&query=${lat},${lon}" target="_blank" rel="noopener" aria-label="Open in Google Maps"><i data-lucide="map-pin"></i></a>
            <button class="btn round" id="toNote" aria-label="Add a note"><i data-lucide="message-square"></i></button>
          </div>
        </section>
        <section class="card">
          <div class="section-head"><h2>Workflow</h2><span class="label">Updated ${ago(c.updated_at)}</span></div>
          <div class="stepper" role="list">${STATUSES.map((st, i) => `
            <button class="step ${i < idx ? "done" : i === idx ? "current" : ""}" data-status="${st}" role="listitem" aria-current="${i === idx}">
              <span class="sq" aria-hidden="true"></span><span class="label">${STATUS_LABEL[st]}</span></button>`).join("")}</div>
          <div class="actions" style="margin-top:18px">
            ${next ? `<button class="btn primary" data-set="${next[0]}"><i data-lucide="arrow-right"></i>${next[1]}</button>` : `<button class="btn" data-set="new"><i data-lucide="rotate-ccw"></i>Reopen</button>`}
            ${c.status !== "resolved" && next?.[0] !== "resolved" ? `<button class="btn" data-set="resolved"><i data-lucide="check"></i>Close as resolved</button>` : `<span></span>`}
          </div>
          <div class="dlg-body" style="padding:20px 0 0">
            <div class="row">
              <label class="field"><span>Priority</span><select class="select" id="prio">${["P1", "P2", "P3"].map((p) => `<option ${p === c.priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
              <label class="field"><span>Department</span><select class="select" id="dept">${departments.map((d) => `<option ${d === c.department ? "selected" : ""}>${esc(d)}</option>`).join("")}</select></label>
            </div>
            <label class="field"><span>Authority (NHAI)</span><select class="select" id="auth">
              ${c.authority ? "" : `<option value="">No office nearby</option>`}
              ${[...(c.authority && !offices.some((o) => o.office_code === c.authority.office_code) ? [c.authority] : []), ...offices].map((o) =>
                `<option value="${esc(o.office_code)}" ${o.office_code === c.authority?.office_code ? "selected" : ""}>${esc(o.office_code)} · ${o.distance_km} km</option>`).join("")}
            </select></label>
            <label class="field"><span>Owner</span><input class="input" id="owner" value="${esc(c.assignee || "")}" placeholder="Crew / engineer name" maxlength="80"></label>
          </div>
        </section>
      </div>

      <div class="g-7-5">
        <div class="stack">
          <section>
            <div class="evidence" id="evidence">
              <span class="cap label">${c.snapshot ? "Detection frame" : "No image"}</span>
              ${c.snapshot ? `<img src="${esc(c.snapshot)}" alt="AI detection frame for ${esc(c.number)}">` : `<div style="aspect-ratio:16/9"></div>`}
            </div>
            <div class="evidence-tabs" role="tablist">
              <button class="on" data-ev="photo" role="tab">Photo</button>
              <button data-ev="video" role="tab" ${video ? "" : "disabled"}>AI video ${video ? `· ${mmss(c.video_time_s)}` : "(none)"}</button>
              <a href="https://www.google.com/maps/search/?api=1&query=${lat},${lon}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Maps</a>
            </div>
          </section>
          <section>
            <div class="section-head"><h2>Location</h2><span class="label">${coord(c.centroid)}</span></div>
            <div class="map mini-map" id="miniMap" style="box-shadow:var(--shadow)"></div>
          </section>
          ${closure ? `<section class="closure-box ${closure.detour ? "" : "blocked"}">
            <span class="label">Road closure</span>
            <strong>${esc(c.label)} blocks this road.</strong>
            <span>${closure.detour ? `Detour available: <b>${closure.detour.km} km · ~${closure.detour.minutes} min</b>, keeping ${closure.detour.clearance_m} m clear (dashed on the map).` : "No alternative road found: prioritise clearing."}</span>
          </section>` : ""}
        </div>

        <div class="stack">
          ${authorityCard(c)}
          <section class="card">
            <div class="section-head"><h2>Facts</h2></div>
            <dl class="facts">
              <dt>Recommended</dt><dd><strong>${esc(ACTION[c.category] || "Inspect")}</strong></dd>
              <dt>Detected</dt><dd>${date(c.first_seen)} · video ${mmss(c.video_time_s)}</dd>
              <dt>Confidence</dt><dd>${Math.round(c.confidence * 100)}% over ${c.frames} frames</dd>
              ${c.extent_m >= 15 ? `<dt>Extent</dt><dd>${c.extent_m} m along the road</dd>` : ""}
              <dt>Coordinates</dt><dd>${coord(c.centroid)} (WGS84)</dd>
              <dt>GPS quality</dt><dd>${esc(c.gps_quality)}</dd>
              <dt>Case opened</dt><dd>${date(c.created_at)}</dd>
            </dl>
          </section>

          <section class="card">
            <div class="section-head"><h2>Activity</h2><span class="label">${activity.length}</span></div>
            <form class="note-box" id="noteForm">
              <textarea class="textarea" id="note" placeholder="Add a note for the crew or the record…" maxlength="1000"></textarea>
              <div style="display:flex;justify-content:flex-end"><button class="btn primary sm" type="submit"><i data-lucide="send"></i>Add note</button></div>
            </form>
            <ol class="feed" style="margin-top:12px">${activity.map((a) => `<li class="${a.kind}"><div>${esc(a.text)}
              <div class="meta">${esc(a.actor)} · ${date(a.ts)}</div></div></li>`).join("")}</ol>
          </section>
        </div>
      </div>`;
    icons(el);

    // mini map: drive route, this hazard, closure + detour
    map?.remove();
    map = makeMap($("#miniMap", el));
    const route = L.layerGroup().addTo(map), hz = L.featureGroup().addTo(map), cl = L.layerGroup().addTo(map);
    api(`/api/runs/${encodeURIComponent(c.run_id)}`).then((run) => {
      for (const f of run.trajectory.features) drawRoute(route, f.geometry.coordinates);
    }).catch(() => {});
    addCase(hz, c, { popup: false });
    if (closure) renderClosures(cl, [closure]);
    map.setView([lat, lon], 17);

    // evidence tabs
    $$(".evidence-tabs [data-ev]", el).forEach((b) => b.addEventListener("click", () => {
      $$(".evidence-tabs [data-ev]", el).forEach((x) => x.classList.toggle("on", x === b));
      const box = $("#evidence", el);
      box.querySelector("img, video, div:not(.cap)")?.remove();
      if (b.dataset.ev === "video" && video) {
        box.insertAdjacentHTML("beforeend", `<video src="${video}" controls autoplay muted playsinline></video>`);
        box.querySelector(".cap").textContent = "AI view";
      } else {
        box.insertAdjacentHTML("beforeend", c.snapshot ? `<img src="${esc(c.snapshot)}" alt="AI detection frame">` : `<div style="aspect-ratio:16/9"></div>`);
        box.querySelector(".cap").textContent = "Detection frame";
      }
    }));
  };

  const save = async (patch, msg) => {
    try {
      data = await store.update(data.case, patch);
      draw();
      if (msg) toast(msg);
    } catch (err) {
      toast(`Couldn't save: ${esc(err.message)}`);
    }
  };

  draw();
  loadWx();
  el.addEventListener("click", (e) => {
    const s = e.target.closest("[data-set], .step[data-status]");
    if (!s) return;
    const to = s.dataset.set || s.dataset.status;
    if (to !== data.case.status) save({ status: to }, `${esc(data.case.number)} → ${STATUS_LABEL[to].toUpperCase()}`);
  });
  el.addEventListener("change", (e) => {
    if (e.target.id === "prio") save({ priority: e.target.value }, `Priority set to ${e.target.value}`);
    if (e.target.id === "dept") save({ department: e.target.value, status: data.case.status === "verified" || data.case.status === "new" ? "assigned" : undefined },
      `Assigned to ${esc(e.target.value)}`);
    if (e.target.id === "auth" && e.target.value) save({ authority: e.target.value }, `Authority set to ${esc(e.target.value)}`);
    if (e.target.id === "owner") save({ assignee: e.target.value }, "Owner updated");
  });
  el.addEventListener("click", async (e) => {
    if (!e.target.closest("#copyAuth")) return;
    try { await navigator.clipboard.writeText(authorityText(data.case)); toast("Authority details copied"); }
    catch { toast("Couldn't copy: clipboard blocked"); }
  });
  el.addEventListener("click", (e) => { if (e.target.closest("#toNote")) { $("#note", el)?.focus(); $("#note", el)?.scrollIntoView({ behavior: "smooth", block: "center" }); } });
  el.addEventListener("submit", (e) => {
    if (e.target.id !== "noteForm") return;
    e.preventDefault();
    const note = $("#note", el).value.trim();
    if (note) save({ note }, "Note added");
  });
  return () => map?.remove();
}
