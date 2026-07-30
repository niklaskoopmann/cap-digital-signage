# Technical Documentation

This document is for maintainers and future adapters of the Xibo media sync scripts.

## Overview

The repository currently contains two sync entry points:

- `scripts/sync_xibo_no_wifi.py`: syncs local media to Xibo CMS over an already-available network connection.
- `scripts/sync_xibo.py`: same sync logic, but first attempts to connect to a configured Wi‑Fi hotspot on Windows via `netsh`.

The scripts are intentionally single-file command-line tools with a Rich-based terminal UI.

## Repository Layout

- `media/`: local media source directory used by the sync scripts.
- `scripts/`: Python code and runtime configuration.
  - `sync_xibo_no_wifi.py`: sync script without Wi‑Fi connect/disconnect.
  - `sync_xibo.py`: sync script with Windows Wi‑Fi connect/disconnect.
  - `.env`: local configuration and credentials.
  - `requirements.txt`: Python dependencies.
  - `logs/`: optional log output target.
- `xibo/`: Xibo CMS Docker assets, templates, and documentation.

## Runtime Configuration

Both scripts load environment variables from `scripts/.env`.

### Common keys

- `CMS_BASE_URL`: Base URL for the Xibo CMS, for example `http://localhost` or `http://192.168.1.1`.
- `CMS_VERIFY_TLS`: Whether to verify TLS certificates.
- `CMS_TIMEOUT_SECONDS`: Timeout for HTTP requests.
- `AUTH_MODE`: `none` or `oauth`.
- `CMS_CLIENT_ID` / `CMS_CLIENT_SECRET`: OAuth client credentials from Xibo.
- `LOCAL_MEDIA_DIR`: Folder containing local media files, usually `../media`.
- `MEDIA_EXTENSIONS`: File extensions that count as media.
- `COMPARE_MODE`: `filename` or `hash`.
- `MANAGED_TAG`: Tag used to mark media managed by the sync script.
- `ONLY_DELETE_MANAGED_TAG`: If `true`, deletes are restricted to tagged items.
- `MANAGED_FOLDER_ID`: Optional Xibo folderId filter.
- `UPLOAD_NEW_LOCAL`: If `true`, uploads new local items.
- `DELETE_REMOTE_NOT_LOCAL`: Default delete choice for interactive runs.
- `DRY_RUN`: If `true`, suppresses changes.
- `DISPLAY_GROUP_ID`: Optional display group to trigger Collect Now.
- `TRIGGER_COLLECTNOW_ON_CHANGES`: Whether to trigger Collect Now after modifications.
- `XIBO_UPLOAD_FIELD`: Multipart field name used by the upload API.
- `HASH_TAG_PREFIX`: Prefix used for sha256 tags, default `sha256:`.
- `LOG_LEVEL`: Logging level.
- `LOG_FILE`: Optional file log destination.

### Wi‑Fi keys used by `sync_xibo.py`

- `WIFI_SSID`: Target SSID.
- `WIFI_PROFILE`: Windows WLAN profile name.
- `WIFI_PASSWORD`: WPA/WPA2 password.
- `WIFI_AUTH`: `WPA2PSK`, `WPAPSK`, or `open`.
- `WIFI_CIPHER`: `AES` or `TKIP`.
- `WIFI_CONNECT_TIMEOUT_SECONDS`: Wait time for the connection to become active.

## Sync Flow

### `sync_xibo_no_wifi.py`

1. Load `.env` and show a settings summary.
2. Optionally run the configuration wizard and persist changes back to `.env`.
3. Build a local file index from `LOCAL_MEDIA_DIR`.
4. Call the Xibo API to list library items.
5. Build a remote index from library metadata.
6. Compare local and remote keys.
7. Upload missing local items if enabled.
8. Delete remote-only items if the user requested deletion.
9. Optionally trigger Collect Now.

### `sync_xibo.py`

Same flow as above, plus:

1. Ensure a WLAN profile exists for the configured SSID.
2. Connect using `netsh wlan connect`.
3. Wait for the active SSID to match the configured SSID.
4. Run the sync flow.
5. Disconnect from Wi‑Fi at the end.

## Comparison Modes

### `filename`

- Local key: file name
- Remote key: library item name, `fileName`, or `originalFileName`

This is the simplest mode and works well when files are unique by name.

### `hash`

- Local key: `sha256:<hash>` tag computed from file content
- Remote key: first matching tag beginning with `HASH_TAG_PREFIX`

This mode only works reliably if uploaded items already have a hash tag. New uploads can be tagged after upload.

## Upload Behavior

Uploads use `requests_toolbelt.MultipartEncoder` and a progress bar from Rich.

Implementation notes:

- Several multipart field names are tried to support Xibo version differences.
- Upload success is accepted for HTTP `200` and `201`.
- After upload, the script tags the item with `MANAGED_TAG` and, in hash mode, the hash tag as well.

## Deletion Safety

Deletion is intentionally conservative.

- The default configuration prompts before deleting.
- If `ONLY_DELETE_MANAGED_TAG=true`, the script only deletes items whose tags include `MANAGED_TAG`.
- Use `MANAGED_FOLDER_ID` if you want to restrict sync to a specific folder.

## OAuth Notes

The scripts use the Xibo OAuth client credentials flow:

- Token endpoint: `/api/authorize/access_token`
- Grant type: `client_credentials`
- Token refresh is attempted when expiry is near or when a request is rejected with `401` or `403`.

## Wi‑Fi Notes

`sync_xibo.py` uses Windows `netsh` for WLAN management.

Important constraints discovered during testing:

- Windows Location services may need to be enabled.
- Running the script may require Administrator privileges.
- If these are not available, use `sync_xibo_no_wifi.py` instead.

## How to extend

Common extension points:

- Add more CLI options with `argparse`.
- Add additional metadata tagging after uploads.
- Expand the delete guard logic.
- Add support for another compare mode.
- Replace the `netsh` Wi‑Fi integration with a different provider if Windows policy allows it.

## Recommended Maintenance Practice

When changing behavior:

1. Update this file first or in the same change.
2. Keep `Readme.md` focused on quick-start and user guidance.
3. Add a dry-run verification step before any destructive run.
4. Run `python -m py_compile scripts/*.py` or the equivalent syntax check.

## Docker / Xibo CMS Reference

The repo includes a full Xibo Docker distribution under `xibo/xibo-docker-4.4.2/`.

Typical setup flow:

1. Start the stack with `docker compose up -d` in that directory.
2. Open the CMS web UI.
3. Complete the initial installer.
4. Create an OAuth application in Administration → Applications.
5. Copy the client credentials into `scripts/.env`.

For user-facing run instructions, see [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
