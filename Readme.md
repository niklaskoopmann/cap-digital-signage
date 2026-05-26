# Xibo Media Sync (Windows Terminal App)

This project syncs a local media folder (images/videos) to a running **Xibo CMS** instance reachable via an **air‑gapped Wi‑Fi hotspot**.

It is designed for the following directory layout:

```
main/
  images/         # local media (your images/videos)
  scripts/        # scripts + config
    sync_xibo.py
    requirements.txt
    .env
    secrets.json
    logs/         # created automatically if LOG_FILE is set
```

---

## What the script does

1. **Connects to the Wi‑Fi hotspot** on Windows (even if the user never connected before).
   - If no WLAN profile exists yet, it **creates/imports** one from SSID + password.
2. **Loads configuration** from `scripts/.env`.
3. Starts an **interactive configuration editor**:
   - You can change values in the terminal.
   - Press **ENTER** to keep the current value.
   - Changes are **persisted back to `.env`**.
4. Asks **per-run** if remote media should be deleted:
   - **Upload only** (safe)
   - **Upload + delete remote items missing locally** (optional)
5. **Compares** local media vs. Xibo CMS Library
   - `COMPARE_MODE=filename`: compares filenames
   - `COMPARE_MODE=hash`: compares sha256 hashes stored as Xibo tags (`sha256:<hash>`)
6. **Uploads** new local media with a **progress bar** (tqdm).
7. Optionally **deletes** remote media not present locally (scoped by `MANAGED_TAG` for safety).
8. Optionally triggers **Collect Now** for a given `DISPLAY_GROUP_ID` (refresh player content).
9. **Disconnects from Wi‑Fi** and prints success/failure.

---

## Files and configuration

### `scripts/requirements.txt`
Python dependencies:
- `requests`: HTTP API calls
- `python-dotenv`: loads `.env`
- `tqdm`: upload progress bars
- `requests-toolbelt`: multipart upload progress monitoring
- `rich`: pretty CLI UI

Install dependencies:

```powershell
cd main\scripts
py -m pip install -r requirements.txt
```

---

### `scripts/.env`
Your configuration (Wi‑Fi + CMS + behavior). Important keys:

- **Wi‑Fi**
  - `WIFI_SSID`, `WIFI_PROFILE`
  - `WIFI_PASSWORD`
  - `WIFI_AUTH` (`WPA2PSK`, `WPAPSK`, `open`)
  - `WIFI_CIPHER` (`AES`, `TKIP`)

- **Xibo**
  - `CMS_BASE_URL` (for you: `http://192.168.1.1`)

- **Diffing**
  - `COMPARE_MODE=filename|hash`

- **Deletion**
  - `DELETE_REMOTE_NOT_LOCAL` (default)
  - `ONLY_DELETE_MANAGED_TAG=true` (recommended)
  - `MANAGED_TAG=xibo-sync` (script uploads with this tag)

- **Player Refresh** (optional)
  - `DISPLAY_GROUP_ID` (if set, script can call Collect Now)

---

### `scripts/secrets.json`
Prepared for **future OAuth** mode:

```json
{
  "client_id": "",
  "client_secret": ""
}
```

Currently, if `AUTH_MODE=none`, this file is not used.

---

### `scripts/sync_xibo.py`
The terminal app that performs:
- Wi‑Fi connect / disconnect
- interactive config editing and persistence to `.env`
- diff local vs remote
- upload with progress bar
- optional delete
- optional Collect Now

---

## How to run

1. Put your images/videos into:
   - `main/images/`

2. Configure `.env`:
   - The first run will prompt you interactively.
   - Make sure `WIFI_SSID` and `WIFI_PASSWORD` match your hotspot.

3. Install dependencies:

```powershell
cd main\scripts
py -m pip install -r requirements.txt
```

4. Run the script:

```powershell
cd main\scripts
py .\sync_xibo.py
```

The script will:
- ask you to edit config values
- ask whether you want to delete remote items for this run
- print step-by-step progress

---

## Notes / Troubleshooting

### 401 Unauthorized from the API
If Xibo API auth is still enabled, unauthenticated calls will fail with **401/403**.
- For now you request `AUTH_MODE=none`.
- If you see 401, the CMS is still protected — later you can switch to `AUTH_MODE=oauth` and fill `secrets.json`.

### Deletion safety
By default, deletions are limited to media tagged with `MANAGED_TAG`.
This prevents accidental deletion of unrelated items in the CMS library.

### Hash compare mode
`COMPARE_MODE=hash` relies on sha256 tags (`sha256:<hash>`). That means:
- Files uploaded by this script get such a hash tag
- Existing files in Xibo without such tags will not match until re-uploaded/tagged

---

## License / Disclaimer
Use at your own risk. Especially be careful with deletion options in production environments.
