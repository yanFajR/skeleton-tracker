#!/usr/bin/env bash
set -eu

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="$project_root/.venv/bin/python"
runner="$project_root/scripts/run_server.sh"
unit_name="motion-tracker.service"
unit_directory="$HOME/.config/systemd/user"
unit_file="$unit_directory/$unit_name"

if [[ ! -x "$python" ]]; then
    printf 'Python virtual environment not found: %s\nCreate .venv and install requirements first.\n' "$python" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    printf 'systemctl is required to install the Linux user service.\n' >&2
    exit 1
fi

quote_systemd_value() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '"%s"' "$value"
}

mkdir -p "$unit_directory"
temporary_unit="$(mktemp "$unit_directory/.motion-tracker.XXXXXX")"
trap 'rm -f -- "$temporary_unit"' EXIT

cat > "$temporary_unit" <<EOF
[Unit]
Description=Motion Tracker
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=$(quote_systemd_value "$project_root")
ExecStart=/usr/bin/env bash $(quote_systemd_value "$runner")
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

mv -f -- "$temporary_unit" "$unit_file"
trap - EXIT
systemctl --user daemon-reload
systemctl --user enable --now "$unit_name"

printf 'Enabled and started %s for user %s.\n' "$unit_name" "$USER"
printf 'Open http://127.0.0.1:8000\n'
printf 'View logs with: journalctl --user -u %s -f\n' "$unit_name"
