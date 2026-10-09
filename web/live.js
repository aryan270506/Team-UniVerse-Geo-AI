// Phone-as-dashcam: stream rear camera frames + GPS fixes to the TerraTrace server.
// Binary frame message = [float64 little-endian capture time (ms)] + [JPEG bytes].
const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
let sessionId = params.get("s");
const pairKey = params.get("k");                     // from a QR code: lets this phone join without signing in
const FPS = Math.min(30, Math.max(1, Number(params.get("fps")) || 30));
// Adaptive quality for weak mobile uplinks: frame size/quality step down while the upload can't
// keep up (measured on slow 4G: ~3 fps at 1280 px) and back up when it recovers.
const LEVELS = [{ side: 1280, q: 0.6 }, { side: 960, q: 0.55 }, { side: 720, q: 0.5 }];
let level = 0, lastAdapt = 0;

let ws = null, stream = null, watchId = null, wakeLock = null, timer = null;
let running = false, inflight = 0, sent = 0, sentWindow = [], lastFix = null, fixTimer = null, startedAt = 0;
const MAX_INFLIGHT = 3;                              // frames on the wire before waiting for an ack
const canvas = document.createElement("canvas");
const ctx = canvas.getContext("2d");

function fatal(title, msg) {
  $("#errTitle").textContent = title;
  $("#errMsg").textContent = msg;
  $("#err").hidden = false;
}

function setNet(state, text) {
  $("#netDot").className = `dot ${state}`;
  $("#net").textContent = text;
}

if (!window.isSecureContext) {
  fatal("HTTPS required", "Phones only allow camera and GPS on secure pages. Open the https:// address shown on the dashboard and accept the certificate warning.");
}

async function ensureSession() {
  if (sessionId) return;
  const r = await fetch("/api/me/live", { method: "POST", headers: { "X-TerraTrace": "1" } });
  if (r.status === 401) { location.href = `/login.html?next=${encodeURIComponent("/app/#/live")}`; throw new Error("Please log in"); }
  if (!r.ok) throw new Error("Couldn't start a live session");
  sessionId = (await r.json()).id;
  history.replaceState(null, "", `?s=${encodeURIComponent(sessionId)}&fps=${FPS}`);
}

function connect() {
  return new Promise((resolve, reject) => {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live/${encodeURIComponent(sessionId)}/ingest${pairKey ? `?k=${encodeURIComponent(pairKey)}` : ""}`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { setNet("ok", "connected"); resolve(); };
    ws.onerror = () => reject(new Error("Could not open the live connection to the laptop. Check the phone is on the same Wi-Fi/hotspot and the TerraTrace HTTPS server is running."));
    ws.onclose = () => { setNet("bad", "disconnected"); inflight = 0; if (running) setTimeout(reconnect, 1500); };
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "ack") {
        inflight = Math.max(0, inflight - 1);
        $("#hz").textContent = msg.hazards;
        $("#lat").textContent = `${msg.latency_s}s`;
        drawBoxes(msg.boxes);
        explain(msg);
      } else if (msg.type === "error") {
        fatal("Session error", msg.message);
        stop(false);
      }
    };
  });
}

async function reconnect() {
  if (!running) return;
  setNet("warn", "reconnecting…");
  try { await connect(); } catch { setTimeout(reconnect, 3000); }
}

function sendFrame() {
  if (!running || inflight >= MAX_INFLIGHT || !ws || ws.readyState !== WebSocket.OPEN) return;
  const v = $("#cam");
  if (!v.videoWidth) return;
  const t = Date.now();
  const k = Math.min(1, LEVELS[level].side / Math.max(v.videoWidth, v.videoHeight));                // never upscale
  canvas.width = Math.round(v.videoWidth * k);
  canvas.height = Math.round(v.videoHeight * k);
  ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
  inflight++;                                        // back-pressure: a few frames in flight
  canvas.toBlob(async (blob) => {
    if (!blob || !ws || ws.readyState !== WebSocket.OPEN) { inflight = Math.max(0, inflight - 1); return; }
    const head = new ArrayBuffer(8);
    new DataView(head).setFloat64(0, t, true);
    ws.send(await new Blob([head, blob]).arrayBuffer());
    sent++;
    sentWindow.push(performance.now());
    sentWindow = sentWindow.filter((x) => performance.now() - x < 3000);
    const fps = sentWindow.length / 3;
    adapt(fps);
    $("#rate").textContent = `${fps.toFixed(1)} fps${level ? ` · ${LEVELS[level].side}p` : ""}`;
  }, "image/jpeg", LEVELS[level].q);
}

function adapt(fps) {
  const now = performance.now();
  if (now - lastAdapt < 4000 || now - startedAt < 4000) return;       // let each level settle
  if (fps < 8 && level < LEVELS.length - 1) { level++; lastAdapt = now; }
  else if (fps > 20 && level > 0) { level--; lastAdapt = now; }
}

// Detections from the server, drawn over the preview (the video is object-fit: cover → "slice").
function drawBoxes(b) {
  const svg = $("#boxes");
  if (!b) { svg.innerHTML = ""; return; }
  svg.setAttribute("viewBox", `0 0 ${b.w} ${b.h}`);
  svg.innerHTML = b.dets.map(([x1, y1, x2, y2, cat, conf]) =>
    `<rect x="${x1}" y="${y1}" width="${x2 - x1}" height="${y2 - y1}"/><text x="${x1 + 4}" y="${Math.max(y1 - 8, 28)}">${cat.replace("_", " ")} ${Math.round(conf * 100)}%</text>`).join("");
}

// Tell the driver why detections are (not) turning into mapped hazards.
let lastHint = 0;
function explain(m) {
  if (Date.now() - lastHint < 1500) return;
  lastHint = Date.now();
  let hint;
  if (m.state === "queued") hint = "Waiting for the detector: another session is still running on the laptop.";
  else if (!m.gps_fixes) hint = m.gps_rejected
    ? `GPS too inaccurate (${m.gps_rejected} readings over ±50 m). Detections are shown but can't be mapped: go outdoors.`
    : "No GPS yet. Detections are shown but can't be mapped until the phone gets a location.";
  else if (m.boxes?.dets.length && !m.boxes.located) hint = "Detected, but not on the road ahead: aim the phone at the road, horizon near mid-frame.";
  else if (level === LEVELS.length - 1 && sentWindow.length / 3 < 4)
    hint = "Weak upload signal: streaming at reduced quality. For full quality use Record in the app and upload later.";
  else hint = "Streaming. Hazards appear on the dashboard map in real time.";
  $("#hint").textContent = hint;
}

function sendFix(t) {
  if (!lastFix || ws?.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "gps", t, lat: lastFix.lat, lon: lastFix.lon, acc: lastFix.acc }));
}

function onPosition(pos) {
  const { latitude, longitude, accuracy } = pos.coords;
  const good = accuracy <= 15, ok = accuracy <= 50;            // the server ignores fixes coarser than 50 m
  $("#gpsDot").className = `dot ${good ? "ok" : ok ? "warn" : "bad"}`;
  $("#gps").textContent = ok ? `GPS ±${Math.round(accuracy)} m` : `GPS too weak (±${Math.round(accuracy)} m)`;
  lastFix = { lat: latitude, lon: longitude, acc: accuracy, at: Date.now() };
  sendFix(pos.timestamp || Date.now());
}

// A phone standing still often reports its position only once; repeat the last fix so
// the server keeps a valid trajectory (it needs fresh fixes to geolocate detections).
function repeatFix() {
  if (!lastFix) {
    $("#gpsDot").className = "dot bad";
    $("#gps").textContent = "GPS: no fix yet";
    return;
  }
  if (Date.now() - lastFix.at < 30000) sendFix(Date.now());
}

async function checkSession() {
  if (!sessionId) return;
  const r = await fetch(pairKey ? `/api/pair/${encodeURIComponent(sessionId)}?k=${encodeURIComponent(pairKey)}`
    : `/api/me/live/${encodeURIComponent(sessionId)}`).catch(() => null);
  if (!r) return setNet("bad", "server unreachable");
  if (r.status === 401) { location.href = `/login.html?next=${encodeURIComponent(location.pathname + location.search)}`; return; }
  if (r.status === 404) {
    sessionId = null;                     // stale/unknown link: a new session is made on START
    $("#hint").textContent = pairKey
      ? "This QR code has expired: the session was stopped. Create a new live session and scan the new code."
      : "That session has ended or belongs to another server. Tap START to begin a new one; the dashboard will offer to watch it.";
    return;
  }
  const s = await r.json();
  if (["done", "error"].includes(s.state)) { sessionId = null; $("#hint").textContent = "That session already finished. Tap START for a new one."; }
  else setNet("ok", "ready");
}

async function start() {
  $("#go").disabled = true;
  try {
    await ensureSession();
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser blocks the camera here. Open the page over https:// in Chrome or Safari.");
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
    $("#cam").srcObject = stream;
    await connect();
    watchId = navigator.geolocation.watchPosition(onPosition,
      (e) => { $("#gpsDot").className = "dot bad"; $("#gps").textContent = `GPS: ${e.message}`; },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 10000 });
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* optional */ }
    running = true;
    startedAt = performance.now();
    level = 0;
    timer = setInterval(sendFrame, 1000 / FPS);
    fixTimer = setInterval(repeatFix, 1000);
    $("#go").textContent = "Stop";
    $("#go").classList.add("stop");
    $("#go").setAttribute("aria-label", "Stop streaming and save");
    $("#hint").textContent = "Streaming. Hazards appear on the dashboard map in real time.";
  } catch (e) {
    const msg = e.name === "NotAllowedError" ? "Camera permission was denied. Allow camera access for this site in the browser settings, then reload."
      : e.name === "NotFoundError" ? "No camera found on this device." : (e.message || String(e));
    fatal("Can't start", msg);
    stop(false);
  } finally {
    $("#go").disabled = false;
  }
}

function stop(save = true) {
  running = false;
  clearInterval(timer);
  clearInterval(fixTimer);
  drawBoxes(null);
  if (watchId != null) navigator.geolocation.clearWatch(watchId);
  stream?.getTracks().forEach((t) => t.stop());
  wakeLock?.release?.();
  if (save && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
  setTimeout(() => ws?.close(), 500);
  $("#go").textContent = "Start";
  $("#go").classList.remove("stop");
  $("#go").setAttribute("aria-label", "Start streaming");
  if (save && sessionId) {
    const id = sessionId;
    $("#hint").innerHTML = pairKey
      ? `Stopped after ${sent} frames. The drive is being saved to the account that created this QR code.`
      : `Stopped after ${sent} frames. Your drive is being saved: <a href="/app/#/drives/${encodeURIComponent(id)}">see hazards &amp; coins</a>.`;
  }
  if (save) sessionId = null;                         // a new start creates a fresh session
}

$("#go").addEventListener("click", () => (running ? stop(true) : start()));
checkSession();
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && running && !wakeLock) {
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* optional */ }
  }
});
