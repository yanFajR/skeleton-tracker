#!/usr/bin/env bash
set -eu

unit_name="motion-tracker.service"
unit_file="$HOME/.config/systemd/user/$unit_name"

if [[ ! -f "$unit_file" ]]; then
    printf '%s is not installed for user %s.\n' "$unit_name" "$USER"
    exit 0
fi

systemctl --user disable --now "$unit_name"
rm -- "$unit_file"
systemctl --user daemon-reload
printf 'Removed %s for user %s.\n' "$unit_name" "$USER"
