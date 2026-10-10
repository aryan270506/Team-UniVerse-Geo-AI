#!/usr/bin/env bash
# One-time setup: permanent public HTTPS URL for this Mac via a Cloudflare named tunnel,
# and auto-start of the server + tunnel at login (launchd), restarting them if they crash.
#
#   tools/setup_tunnel.sh api.yourdomain.com
#
# Needs: the domain added to your Cloudflare account (nameservers pointed at Cloudflare).
# Re-running is safe. Undo: tools/setup_tunnel.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CF="$(command -v cloudflared || echo /opt/homebrew/bin/cloudflared)"
NAME=terratrace
AGENTS="$HOME/Library/LaunchAgents"
SERVER_LABEL=com.terratrace.server
TUNNEL_LABEL=com.terratrace.tunnel

unload() {  # bootout is asynchronous: wait until the job is really gone before re-bootstrapping
  launchctl bootout "gui/$UID/$1" 2>/dev/null || true
  for _ in $(seq 20); do launchctl print "gui/$UID/$1" >/dev/null 2>&1 || return 0; sleep 0.5; done
}

if [[ "${1:-}" == "--uninstall" ]]; then
  for l in $SERVER_LABEL $TUNNEL_LABEL; do unload $l; rm -f "$AGENTS/$l.plist"; done
  echo "Removed auto-start. The tunnel and DNS record still exist in Cloudflare."
  exit 0
fi

HOST="${1:?usage: tools/setup_tunnel.sh <hostname, e.g. api.yourdomain.com>}"
[[ -x "$CF" ]] || { echo "cloudflared not found: brew install cloudflared"; exit 1; }

# 1. Log in (opens the browser once; pick the domain).
[[ -f "$HOME/.cloudflared/cert.pem" ]] || "$CF" tunnel login

# 2. Create the named tunnel once; its ID (and so the URL) never changes.
"$CF" tunnel info "$NAME" >/dev/null 2>&1 || "$CF" tunnel create "$NAME"
ID="$("$CF" tunnel list --output json --name "$NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"

# 3. Point the hostname at the tunnel (CNAME in Cloudflare DNS).
"$CF" tunnel route dns --overwrite-dns "$NAME" "$HOST"

# 4. Tunnel config: public hostname -> local server.
cat > "$HOME/.cloudflared/config.yml" <<EOF
tunnel: $ID
credentials-file: $HOME/.cloudflared/$ID.json
ingress:
  - hostname: $HOST
    service: http://localhost:8000
  - service: http_status:404
EOF

# 5. launchd agents: start at login, restart on crash.
mkdir -p "$AGENTS" "$ROOT/logs"
cat > "$AGENTS/$SERVER_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$SERVER_LABEL</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <!-- launchd's PATH lacks Homebrew, where ffmpeg/ffprobe live -->
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>ProgramArguments</key><array>
    <string>$ROOT/.venv/bin/uvicorn</string><string>server.app:app</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>8000</string>
    <string>--proxy-headers</string><string>--forwarded-allow-ips</string><string>127.0.0.1</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/server.log</string>
</dict></plist>
EOF
cat > "$AGENTS/$TUNNEL_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$TUNNEL_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$CF</string><string>tunnel</string><string>--config</string>
    <string>$HOME/.cloudflared/config.yml</string><string>run</string><string>$NAME</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/tunnel.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/tunnel.log</string>
</dict></plist>
EOF

for l in $SERVER_LABEL $TUNNEL_LABEL; do
  unload $l
  launchctl bootstrap "gui/$UID" "$AGENTS/$l.plist"
done

echo
echo "Done. Permanent URL: https://$HOST"
echo "Logs: $ROOT/logs/{server,tunnel}.log"
echo "Stop any manually started uvicorn on port 8000, or the auto-started one can't bind."
