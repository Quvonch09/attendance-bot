#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this one-time bootstrap as root."
  exit 1
fi

ACCOUNT="cue-attendance-bot"
BASE_DIR="/opt/cue-attendance-bot"
STATE_DIR="/var/lib/cue-attendance-bot"
UNIT_NAME="cue-attendance-bot.service"
SUDOERS_PATH="/etc/sudoers.d/cue-attendance-bot"
UNIT_SOURCE="${1:-}"

if [[ -z "$UNIT_SOURCE" || ! -f "$UNIT_SOURCE" ]]; then
  echo "Pass the reviewed systemd unit file as the first argument."
  exit 2
fi
if getent passwd "$ACCOUNT" >/dev/null; then
  echo "Refusing to overwrite existing account $ACCOUNT."
  exit 3
fi
for target in "$BASE_DIR" "$STATE_DIR" "/etc/systemd/system/$UNIT_NAME" "$SUDOERS_PATH"; do
  if [[ -e "$target" ]]; then
    echo "Refusing to overwrite existing path $target."
    exit 3
  fi
done

read -r DEPLOY_PUBLIC_KEY || true
read -r KEY_TYPE KEY_DATA KEY_COMMENT <<< "$DEPLOY_PUBLIC_KEY"
if [[ "$KEY_TYPE" != "ssh-ed25519" || ! "$KEY_DATA" =~ ^[A-Za-z0-9+/]+={0,2}$ ]]; then
  echo "Expected one ssh-ed25519 public key on stdin."
  exit 2
fi

SUDOERS_TMP="$(mktemp /tmp/cue-attendance-sudoers.XXXXXX)"
trap 'rm -f "$SUDOERS_TMP"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl enable cue-attendance-bot.service, /usr/bin/systemctl restart cue-attendance-bot.service\n' "$ACCOUNT" > "$SUDOERS_TMP"
chmod 0440 "$SUDOERS_TMP"
visudo -cf "$SUDOERS_TMP" >/dev/null

# All paths below are dedicated to this bot; the script refuses pre-existing targets.
useradd --system --user-group --create-home --home-dir "$STATE_DIR" --shell /bin/bash "$ACCOUNT"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 0750 "$BASE_DIR" "$BASE_DIR/releases"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 0700 "$STATE_DIR/.ssh"
printf 'restrict %s %s %s\n' "$KEY_TYPE" "$KEY_DATA" "${KEY_COMMENT:-cue-attendance-github-actions}" > "$STATE_DIR/.ssh/authorized_keys"
chown "$ACCOUNT:$ACCOUNT" "$STATE_DIR/.ssh/authorized_keys"
chmod 0600 "$STATE_DIR/.ssh/authorized_keys"
install -o root -g root -m 0644 "$UNIT_SOURCE" "/etc/systemd/system/$UNIT_NAME"
install -o root -g root -m 0440 "$SUDOERS_TMP" "$SUDOERS_PATH"
systemctl daemon-reload

printf 'Prepared dedicated account, SSH key, bot-only directory, sudo rule, and %s.\n' "$UNIT_NAME"
printf 'The service remains disabled and stopped until a rotated BOT_TOKEN is configured.\n'
