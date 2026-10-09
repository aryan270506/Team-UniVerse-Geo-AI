#!/usr/bin/env bash
# Self-signed HTTPS certificate so a phone on the same Wi-Fi/hotspot may use camera + GPS.
#   tools/make_cert.sh            -> certs/key.pem, certs/cert.pem (valid for this machine's LAN IPs)
#   .venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8443 \
#       --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
# Then open https://localhost:8443 on the laptop and https://<LAN-IP>:8443 on the phone
# (accept the "not private" warning once on each device).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p certs

SAN="DNS:localhost,IP:127.0.0.1"
for ip in $( (ipconfig getifaddr en0; ipconfig getifaddr en1; hostname -I 2>/dev/null) 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9.]+$' | sort -u); do
  SAN="$SAN,IP:$ip"
done

openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -keyout certs/key.pem -out certs/cert.pem \
  -subj "/CN=TerraTrace local" -addext "subjectAltName=$SAN" 2>/dev/null

echo "certificate for: $SAN"
echo "run: .venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8443 --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem"
