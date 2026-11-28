# Motion Tracker

Motion Tracker is a local web application for reviewing sports video. It overlays MediaPipe pose landmarks and shows selected-frame elbow angles. It is an analysis aid, not an automated coach or a technique pass/fail system.

## Prerequisites

- Windows (initial target) or Linux
- Python 3.14.5 (the current project environment); MediaPipe 1.1.1 is installed in `.venv`.

## Environment setup

From the project folder, create the dedicated environment if it does not exist:

```powershell
python -m venv .venv
```

Use its interpreter directly (no activation is required):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start the application

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in a browser. The server binds only to the local machine.

## Automatic startup and restart (Linux)

After creating `.venv` and installing `requirements.txt`, install a per-user systemd service from the project folder:

```bash
bash scripts/install_systemd_user_service.sh
```

The service starts now and at future user logins. systemd restarts it five seconds after Uvicorn exits. It runs as your account and stays bound to `127.0.0.1:8000`. Follow its logs with `journalctl --user -u motion-tracker.service -f`.

To start the service at system boot before you log in, enable lingering for your account (this may require administrator approval):

```bash
sudo loginctl enable-linger "$USER"
```

Remove the service with:

```bash
bash scripts/uninstall_systemd_user_service.sh
```

## Login

The application is protected by a single local account. The default credentials are:

- Username: `admin`
- Password: `AdminAman123`

Sign in at `/login`; every page, video, and API route requires an active session. Sessions last eight hours and are stored in an HMAC-signed `HttpOnly`, `SameSite=Lax` cookie. The signing secret is generated once into `data/.auth_secret` (ignored by Git) so sessions survive restarts.

Set `MOTION_TRACKER_USERNAME`, `MOTION_TRACKER_PASSWORD`, or `MOTION_TRACKER_PASSWORD_HASH` before starting the server to override the default account. `MOTION_TRACKER_PASSWORD_HASH` accepts the output of `auth.hash_password(...)` and takes precedence over `MOTION_TRACKER_PASSWORD`. Set `MOTION_TRACKER_SECRET_KEY` to supply the signing secret explicitly, and `MOTION_TRACKER_COOKIE_SECURE=1` when the app is served over HTTPS.

Passwords are stored as salted PBKDF2-HMAC-SHA256 digests (600,000 iterations) and verified with `hmac.compare_digest`. This project has no SQL database, so SQL injection has no attack surface: session and comparison metadata live in JSON files, and IDs plus generated filenames are validated before use. Login failures are throttled to five attempts per minute per client before a temporary lockout.

## Troubleshooting

- If `python` is not found, install Python and reopen PowerShell.
- If the browser cannot connect, ensure Uvicorn is still running and use the exact local URL above.
- On Windows, an asyncio `ConnectionResetError` with `WinError 10054` means the browser or another client closed its connection. By itself, it does not indicate a pose-detection failure. Keep the page open while processing. When the page shows a generic error, the persisted record in `data/sessions.json` or `data/comparisons.json` holds the stored status and message; the application does not write exception details to a log yet.
- If a result video still cannot play, check that the browser is up to date and that its H.264 playback support is enabled.
- Keep dependencies inside `.venv`; do not install project packages system-wide.

Further development should wait for approval before starting another stage.
