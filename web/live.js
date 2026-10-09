// Phone-as-dashcam: stream rear camera frames + GPS fixes to the TerraTrace server.
// Binary frame message = [float64 little-endian capture time (ms)] + [JPEG bytes].
const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
let sessionId = params.get("s");
const FPS = Math.min(10, Math.max(1, Number(params.get("fps")) || 5));
const WIDTH = 960;

let ws = null, stream = null, watchId = null, wakeLock = null, timer = null;
let running = false, inflight = false, sent = 0, sentWindow = [];
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
  const fd = new FormData();
  fd.set("name", "phone drive");
  const r = await fetch("/api/live/device", { method: "POST", body: fd });
  sessionId = (await r.json()).id;
  history.replaceState(null, "", `?s=${encodeURIComponent(sessionId)}&fps=${FPS}`);
}

function connect() {
  return new Promise((resolve, reject) => {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live/${encodeURIComponent(sessionId)}/ingest`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { setNet("ok", "connected"); resolve(); };
    ws.onerror = () => reject(new Error("Could not open the live connection to the laptop. Check the phone is on the same Wi-Fi/hotspot and the TerraTrace HTTPS server is running."));
    ws.onclose = () => { setNet("bad", "disconnected"); inflight = false; if (running) setTimeout(reconnect, 1500); };
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "ack") {
        inflight = false;
        $("#hz").textContent = msg.hazards;
        $("#lat").textContent = `${msg.latency_s}s`;
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
  if (!running || inflight || !ws || ws.readyState !== WebSocket.OPEN) return;
  const v = $("#cam");
  if (!v.videoWidth) return;
  const t = Date.now();
  canvas.width = WIDTH;
  canvas.height = Math.round(v.videoHeight * WIDTH / v.videoWidth);
  ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
  inflight = true;                                   // back-pressure: one frame in flight
  canvas.toBlob(async (blob) => {
    if (!blob || !ws || ws.readyState !== WebSocket.OPEN) { inflight = false; return; }
    const head = new ArrayBuffer(8);
    new DataView(head).setFloat64(0, t, true);
    ws.send(await new Blob([head, blob]).arrayBuffer());
    sent++;
    sentWindow.push(performance.now());
    sentWindow = sentWindow.filter((x) => performance.now() - x < 3000);
    $("#rate").textContent = `${(sentWindow.length / 3).toFixed(1)} fps`;
  }, "image/jpeg", 0.7);
}

function onPosition(pos) {
  const { latitude, longitude, accuracy } = pos.coords;
  const good = accuracy <= 15, ok = accuracy <= 50;
  $("#gpsDot").className = `dot ${good ? "ok" : ok ? "warn" : "bad"}`;
  $("#gps").textContent = `GPS ±${Math.round(accuracy)} m`;
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "gps", t: pos.timestamp || Date.now(), lat: latitude, lon: longitude, acc: accuracy }));
  }
}

async function checkSession() {
  if (!sessionId) return;
  const r = await fetch(`/api/live/${encodeURIComponent(sessionId)}`).catch(() => null);
  if (!r) return setNet("bad", "server unreachable");
  if (r.status === 404) {
    sessionId = null;                     // stale/unknown link: a new session is made on START
    $("#hint").textContent = "That session has ended or belongs to another server. Tap START to begin a new one; the dashboard will offer to watch it.";
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
    timer = setInterval(sendFrame, 1000 / FPS);
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
  if (watchId != null) navigator.geolocation.clearWatch(watchId);
  stream?.getTracks().forEach((t) => t.stop());
  wakeLock?.release?.();
  if (save && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
  setTimeout(() => ws?.close(), 500);
  $("#go").textContent = "Start";
  $("#go").classList.remove("stop");
  $("#go").setAttribute("aria-label", "Start streaming");
  $("#hint").textContent = save ? `Stopped after ${sent} frames — the run is being saved on the dashboard.` : $("#hint").textContent;
  if (save) sessionId = null;                         // a new start creates a fresh session
}

$("#go").addEventListener("click", () => (running ? stop(true) : start()));
checkSession();
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && running && !wakeLock) {
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* optional */ }
  }
});
