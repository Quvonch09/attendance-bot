#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/opt/cue-attendance-bot"
RELEASE_ID="${1:-}"
if [[ ! "$RELEASE_ID" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Expected a full Git commit SHA."
  exit 2
fi
RELEASE_DIR="$BASE_DIR/releases/$RELEASE_ID"
if [[ ! -f "$RELEASE_DIR/bot.py" || ! -f "$RELEASE_DIR/requirements.txt" ]]; then
  echo "Release files are missing from $RELEASE_DIR."
  exit 2
fi

# Install only into this bot's isolated virtual environment.
if [[ ! -x "$BASE_DIR/venv/bin/python" ]]; then
  python3 -m venv "$BASE_DIR/venv"
fi
"$BASE_DIR/venv/bin/python" -m pip install --disable-pip-version-check -q -r "$RELEASE_DIR/requirements.txt"
"$BASE_DIR/venv/bin/python" -m py_compile "$RELEASE_DIR/bot.py"

# Switch the bot's current code atomically; no other directory is touched.
ln -sfn "$RELEASE_DIR" "$BASE_DIR/current.next"
mv -Tf "$BASE_DIR/current.next" "$BASE_DIR/current"

# The Telegram token is supplied through SSH stdin from the GitHub Actions secret.
# An empty value never overwrites a previously configured token file.
INCOMING_BOT_TOKEN=""
IFS= read -r INCOMING_BOT_TOKEN || INCOMING_BOT_TOKEN=""
if [[ -n "$INCOMING_BOT_TOKEN" ]]; then
  umask 077
  TEMP_ENV="$(mktemp "$BASE_DIR/.env.XXXXXX")"
  printf 'BOT_TOKEN=%s\nADMIN_IDS=8048583227,1922170479\nOFFICE_LAT=38.8557873\nOFFICE_LON=65.8002788\nGEOFENCE_METERS=300\nTIMEZONE=Asia/Tashkent\nDATA_DIR=/var/lib/cue-attendance-bot/data\n' "$INCOMING_BOT_TOKEN" > "$TEMP_ENV"
  mv -f "$TEMP_ENV" "$BASE_DIR/.env"
fi
unset INCOMING_BOT_TOKEN

if [[ -s "$BASE_DIR/.env" ]] && grep -q '^BOT_TOKEN=.' "$BASE_DIR/.env"; then
  sudo -n /usr/bin/systemctl enable cue-attendance-bot.service
  sudo -n /usr/bin/systemctl restart cue-attendance-bot.service
  if ! /usr/bin/systemctl is-active --quiet cue-attendance-bot.service; then
    echo "The bot service did not become active. Check: journalctl -u cue-attendance-bot.service"
    exit 1
  fi
  echo "Bot release deployed and service is active."
else
  echo "Code deployed. Service remains disabled until a rotated BOT_TOKEN is added as a GitHub Actions secret."
fi
