#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${KURA_API_URL:-http://100.65.100.2:8320}"
EMAIL="${KURA_ADMIN_EMAIL:-jonas@kura.dev}"

# Login
echo "Kura Telemetry — $BASE_URL"
read -rsp "Passwort für $EMAIL: " PASSWORD
echo

TOKEN=$(curl -sS "$BASE_URL/v1/auth/email/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | jq -r '.access_token // empty')

if [[ -z "$TOKEN" ]]; then
  echo "Login fehlgeschlagen."
  exit 1
fi
echo "Login OK."
echo

api() {
  curl -sS "$BASE_URL$1" -H "Authorization: Bearer $TOKEN" | jq
}

while true; do
  echo "--- Telemetry ---"
  echo "1) Overview (24h)"
  echo "2) Anomalien (24h)"
  echo "3) Signale (24h, letzte 120)"
  echo "4) Signale filtern (custom)"
  echo "5) Overview (custom Stunden)"
  echo "q) Beenden"
  read -rp "> " choice

  case "$choice" in
    1) api "/v1/admin/agent/telemetry/overview?window_hours=24" ;;
    2) api "/v1/admin/agent/telemetry/anomalies?window_hours=24&limit=10" ;;
    3) api "/v1/admin/agent/telemetry/signals?window_hours=24&limit=120" ;;
    4)
      read -rp "signal_type (leer=alle): " stype
      read -rp "window_hours [72]: " whours
      whours="${whours:-72}"
      read -rp "limit [200]: " slimit
      slimit="${slimit:-200}"
      url="/v1/admin/agent/telemetry/signals?window_hours=$whours&limit=$slimit"
      [[ -n "$stype" ]] && url="$url&signal_type=$stype"
      api "$url"
      ;;
    5)
      read -rp "window_hours: " whours
      api "/v1/admin/agent/telemetry/overview?window_hours=$whours"
      ;;
    q|Q) echo "Bye."; exit 0 ;;
    *) echo "?" ;;
  esac
  echo
done
