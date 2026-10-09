// Go live, like the control room: create a session, connect a phone by QR code (or use this
// device), then watch it live — camera feed with detection boxes, route, hazard alerts.
// #/live          start a session
// #/live/<id>     watch / pair a running session (links from the QR flow)
import { api, CAT, esc } from "/js/core.js";
import { makeMap, SEV_COL } from "/js/map.js";
import { refreshBalance } from "/app/app.js";

const SEV_PRIO = { high: "P1", medium: "P2", low: "P3" };
const isPhone = matchMedia("(pointer: coarse)").matches;

export default async function render(el, [sid]) {
  return sid ? watch(el, sid, null) : start(el);
}

function start(el) {
  el.innerHTML = `
    <div><a class="label" href="#/home">← Home</a><h1 class="u-h1">Go live</h1></div>
    <section class="card form">
      <p style="margin:0">A phone becomes your dashcam: it streams camera and GPS, the AI checks the road as you drive, and hazards appear on the map in real time. The drive is saved with your coins when you stop.</p>
      <ul class="how">
        <li><b>1</b> Create a session. Scan the QR code with the phone in the car, or use this phone.</li>
        <li><b>2</b> Mount the phone on the windscreen, landscape, horizon near the middle.</li>
        <li><b>3</b> Tap START on the phone and allow camera and location.</li>
      </ul>
      <p class="err-box" id="err" hidden></p>
      <button class="btn primary block" id="go"><i data-lucide="radio"></i>Create live session</button>
    </section>`;
  el.querySelector("#go").addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try {
      const r = await api("/api/me/live", { method: "POST" });
      history.replaceState(null, "", `#/live/${encodeURIComponent(r.id)}`);
      el.innerHTML = "";
      cleanup = await watch(el, r.id, r);
    } catch (ex) {
      el.querySelector("#err").textContent = ex.message;
      el.querySelector("#err").hidden = false;
      e.currentTarget.disabled = false;
    }
  });
  let cleanup = null;
  return () => cleanup?.();
}

function qrSvg(url) {
  if (!window.qrcode) return "";
  const qr = qrcode(0, "M");
  qr.addData(url);
  qr.make();
  return qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
}

async function watch(el, sid, links) {
  el.innerHTML = `
    <div><a class="label" href="#/home">← Home</a><h1 class="u-h1">Live drive</h1></div>
    <section class="card" id="pair" ${links ? "" : "hidden"}>
      <div class="section-head"><h2>Connect a phone</h2><span class="label">Waiting for the camera</span></div>
      <div class="pair-row">
        <div class="qr-box" id="qr"></div>
        <div style="display:grid;gap:10px;align-content:start">
          <span class="label">Scan with the phone's camera, or open:</span>
          <div id="urls"></div>
          <p class="err-box" id="warn" hidden>This page is open over plain HTTP: open the https:// address on port 8443 so phones can use the camera.</p>
          <ul class="how">
            <li>Same Wi-Fi as this computer, or this computer on the phone's hotspot.</li>
            <li>Accept the certificate warning (Chrome: Advanced → Proceed; Safari: Show details → visit).</li>
            <li>The phone doesn't need to sign in: the code links it to your account for this drive only.</li>
          </ul>
          <a class="btn ${isPhone ? "primary" : ""}" id="here"><i data-lucide="smartphone"></i>Use this ${isPhone ? "phone" : "device"} instead</a>
        </div>
      </div>
    </section>
    <section class="live-stage" id="stage">
      <img id="frame" alt="Live camera with AI detections" hidden>
      <svg class="live-boxes" id="boxes" preserveAspectRatio="xMidYMid meet" aria-hidden="true"></svg>
      <span class="live-tag"><i class="dot live"></i><span id="state">Waiting for the phone…</span></span>
    </section>
    <section class="stats" id="stats" style="grid-template-columns:repeat(4,1fr)">
      <div><b id="sHz">0</b><span>hazards</span></div><div><b id="sKm">0.0</b><span>km</span></div>
      <div><b id="sFps">0</b><span>fps analysed</span></div><div><b id="sTime">0:00</b><span>elapsed</span></div>
    </section>
    <div class="u-map" id="map"></div>
    <section><div class="section-head"><h2>Alerts</h2><span class="label" id="aCount">0</span></div>
      <div class="list" id="alerts"><div class="card empty" id="aEmpty">Hazards the AI confirms appear here as you drive.</div></div></section>
    <div style="display:grid;grid-template-columns:1fr;gap:10px">
      <button class="btn danger block" id="stop"><i data-lucide="square"></i>Stop &amp; save drive</button>
    </div>`;
  const $ = (s) => el.querySelector(s);
  window.lucide?.createIcons({ root: el });

  if (!links) {                                    // reopened from #/live/<id>: fetch the pairing links
    const s = await api(`/api/me/live/${encodeURIComponent(sid)}`).catch(() => null);
    if (!s) { el.innerHTML = `<div class="card empty">This live session has ended.<br><a class="btn primary" style="margin-top:14px" href="#/live">Start a new one</a></div>`; return; }
    if (["queued", "running"].includes(s.state)) { links = s; $("#pair").hidden = false; }
  }
  if (links) {
    $("#qr").innerHTML = qrSvg(links.phone_urls[0]);
    $("#urls").innerHTML = links.phone_urls.map((u) => `<a class="url" href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join("");
    $("#warn").hidden = links.secure;
    $("#here").href = links.this_device;
  }

  // map: trail + vehicle + hazards
  const map = makeMap($("#map"), { layers: false });
  map.setView([20.6, 78.9], 4);
  const trail = L.polyline([], { color: "#111", weight: 5 }).addTo(map);
  const hz = L.layerGroup().addTo(map);
  let car = null, alerts = new Map(), started = null, done = false, timer = null;

  const drawBoxes = (b) => {
    const svg = $("#boxes");
    svg.setAttribute("viewBox", `0 0 ${b.w} ${b.h}`);
    svg.innerHTML = b.dets.map(([x1, y1, x2, y2, cat, conf]) =>
      `<rect x="${x1}" y="${y1}" width="${x2 - x1}" height="${y2 - y1}"/><text x="${x1 + 4}" y="${Math.max(y1 - 8, 24)}">${esc(cat.replace("_", " "))} ${Math.round(conf * 100)}%</text>`).join("");
  };
  const addHazard = (f, fresh) => {
    const p = f.properties, [lon, lat] = p.centroid, label = CAT[p.category]?.label || p.category;
    L.circleMarker([lat, lon], { radius: 9, color: "#fff", weight: 2, fillColor: SEV_COL[SEV_PRIO[p.severity]], fillOpacity: 1 }).bindTooltip(label).addTo(hz);
    const html = `<div class="hz" style="grid-template-columns:1fr"><div><b>${esc(label)}</b> <span class="chip prio-${SEV_PRIO[p.severity]}"><i></i>${esc(p.severity)}</span>
      <div class="m">${Math.round(p.confidence * 100)}% sure · ${lat.toFixed(5)}, ${lon.toFixed(5)}</div></div></div>`;
    if (alerts.has(p.id)) { alerts.get(p.id).innerHTML = html; return; }
    const d = document.createElement("div");
    d.innerHTML = html;
    alerts.set(p.id, d);
    $("#alerts").prepend(d);
    $("#aEmpty")?.remove();
    $("#aCount").textContent = alerts.size;
    if (fresh) navigator.vibrate?.(120);
  };
  const setState = (t) => { const n = $("#state"); if (n) n.textContent = t; };
  const tick = () => { if (started) { const s = (Date.now() - started) / 1000; $("#sTime").textContent = `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`; } };

  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live/${encodeURIComponent(sid)}`);
  ws.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    switch (ev.type) {
      case "status": if (ev.state === "running") setState("Connected · waiting for frames"); else if (ev.state === "finalizing") setState("Saving drive…"); break;
      case "snapshot":
        trail.setLatLngs(ev.trail.map(([x, y]) => [y, x]));
        ev.features.forEach((f) => addHazard(f, false));
        break;
      case "frame":
        $("#frame").src = `data:image/jpeg;base64,${ev.jpeg}`;
        $("#frame").hidden = false;
        if (!started) { started = Date.now(); timer = setInterval(tick, 1000); $("#pair").hidden = true; setState("Live"); }
        break;
      case "boxes": drawBoxes(ev); break;
      case "pose": {
        const ll = [ev.lat, ev.lon];
        trail.addLatLng(ll);
        if (!car) { car = L.circleMarker(ll, { radius: 8, color: "#fff", weight: 3, fillColor: "#111", fillOpacity: 1 }).addTo(map); map.setView(ll, 17); }
        else { car.setLatLng(ll); if (!map.getBounds().pad(-0.2).contains(ll)) map.panTo(ll); }
        break;
      }
      case "hazard": addHazard(ev.feature, ev.new); break;
      case "stats":
        $("#sHz").textContent = ev.hazards; $("#sKm").textContent = ev.route_km.toFixed(1); $("#sFps").textContent = Math.round(ev.fps);
        break;
      case "error": setState(`Error: ${ev.message}`); done = true; break;
      case "done":
        done = true;
        clearInterval(timer);
        setState(ev.run_id ? "Saved" : "Nothing was streamed");
        $("#stop").outerHTML = ev.run_id
          ? `<a class="btn primary block" href="#/drives/${encodeURIComponent(ev.run_id)}"><i data-lucide="coins"></i>See hazards &amp; coins</a>`
          : `<a class="btn block" href="#/live">Start a new session</a>`;
        window.lucide?.createIcons({ root: el });
        refreshBalance();
        break;
    }
  };
  ws.onclose = () => { if (!done) setState("Disconnected from the server"); };

  $("#stop").addEventListener("click", async (e) => {
    if (!confirm("Stop the live drive and save it?")) return;
    e.currentTarget.disabled = true;
    setState("Saving drive…");
    await api(`/api/me/live/${encodeURIComponent(sid)}/stop`, { method: "POST" }).catch(() => {});
  });

  return () => { done = true; clearInterval(timer); ws.onclose = null; ws.close(); map.remove(); };
}
