#!/bin/sh
# Install the unit, create the state tree, and lock down the config/env permissions.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$HOME/.config/social-librarian"
STATE="$HOME/.local/share/social-librarian"
UNITS="$HOME/.config/systemd/user"

mkdir -p "$CONF" "$STATE/archive" "$UNITS"
[ -f "$CONF/config.toml" ] || install -m 600 "$REPO/deploy/config.example.toml" "$CONF/config.toml"
[ -f "$CONF/env" ] || { install -m 600 /dev/null "$CONF/env"; echo "fill in $CONF/env (see README)"; }
chmod 600 "$CONF/config.toml" "$CONF/env"
install -m 644 "$REPO/deploy/social-librarian.service" "$UNITS/"
# The unit carries the deployment path; rewrite it if this repo lives elsewhere.
sed -i "s#/personal/projects/social-librarian#$REPO#g" "$UNITS/social-librarian.service"
systemctl --user daemon-reload
systemctl --user enable --now social-librarian.service
systemctl --user is-active social-librarian.service
echo "installed. sweep is DRY until SOCIAL_LIBRARIAN_SWEEP_APPLY=1 in $UNITS/social-librarian.service"
