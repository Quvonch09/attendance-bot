#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/opt/cue-attendance-bot"
RELEASE_ID="${1:-}"
if [[ ! "$RELEASE_ID" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Expected a full Git commit SHA."
  exit 2
fi
RELEASE_DIR="$BASE_DIR/releases/$RELEASE_ID"
if [[ ! -f "$RELEASE_DIR/bot.py" || ! -f "$RELEASE_DIR/webapp_api.py" || ! -f "$RELEASE_DIR/requirements.txt" || ! -f "$RELEASE_DIR/webapp/index.html" ]]; then
  echo "Release files are missing from $RELEASE_DIR."
  exit 2
fi

# Install only into this bot's isolated virtual environment.
if [[ ! -x "$BASE_DIR/venv/bin/python" ]]; then
  python3 -m venv "$BASE_DIR/venv"
fi
"$BASE_DIR/venv/bin/python" -m pip install --disable-pip-version-check -q -r "$RELEASE_DIR/requirements.txt"
"$BASE_DIR/venv/bin/python" -m py_compile "$RELEASE_DIR/bot.py" "$RELEASE_DIR/webapp_api.py"

# Switch only this bot's code atomically. Keep every server-side environment
# setting, including the Telegram token and Web App URLs, unchanged.
ln -sfn "$RELEASE_DIR" "$BASE_DIR/current.next"
mv -Tf "$BASE_DIR/current.next" "$BASE_DIR/current"

if [[ -s "$BASE_DIR/.env" ]] && grep -q '^BOT_TOKEN=.' "$BASE_DIR/.env"; then
  sudo -n /usr/bin/systemctl enable cue-attendance-bot.service
  sudo -n /usr/bin/systemctl restart cue-attendance-bot.service
  if ! /usr/bin/systemctl is-active --quiet cue-attendance-bot.service; then
    echo "The bot service did not become active. Check: journalctl -u cue-attendance-bot.service"
    exit 1
  fi
  echo "Bot release deployed and service is active."
else
  echo "Code deployed. Service remains disabled because its protected server-side BOT_TOKEN file is missing."
fi
