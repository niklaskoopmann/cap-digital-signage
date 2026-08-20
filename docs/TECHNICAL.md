# Technical Documentation

This document is for maintainers and future adapters of the Xibo media sync scripts.

## Overview

The repository contains one sync entry point:

- `scripts/sync_xibo.py`: thin launcher for the modular sync package.

The implementation now lives under `scripts/xibo_sync/` so the configuration, UI, API client, media indexing, and config wizard can evolve independently.

## Repository Layout

- `media/`: local media source directory used by the sync scripts.
- `scripts/`: Python code and runtime configuration.
  - `sync_xibo.py`: launcher script.
  - `xibo_sync/`: modular sync implementation.
  - `.env`: local configuration and credentials.
  - `requirements.txt`: Python dependencies.
  - `logs/`: optional log output target.
- `xibo/`: Xibo CMS Docker assets, templates, and documentation.

`tzdata` is included in the runtime requirements because Windows Python installations do not
always provide the IANA timezone database used by `zoneinfo`. Calendar timezone names such as
`UTC` and `America/New_York` therefore depend on this package.

## Runtime Configuration

Both scripts load environment variables from `scripts/.env`.

### Common keys

- `CMS_BASE_URL`: Base URL for the Xibo CMS, for example `http://localhost` or `http://192.168.1.1`.
- `CMS_VERIFY_TLS`: Whether to verify TLS certificates.
- `CMS_TIMEOUT_SECONDS`: Timeout for HTTP requests.
- `AUTH_MODE`: `none` or `oauth`.
- `CMS_CLIENT_ID` / `CMS_CLIENT_SECRET`: OAuth client credentials from Xibo.
- `LOCAL_MEDIA_DIR`: Folder containing local media files, usually `../media`.
- `CALENDAR_JSON_PATH`: Calendar snapshot directory or JSON file. Relative paths resolve from `scripts/`.
- `CALENDAR_DATASET_NAME`: Target Xibo DataSet name, default `office_calendar_events`.
- `CALENDAR_DATASET_CODE`: Optional Xibo DataSet code used during lookup.
- `CALENDAR_UPLOAD_CANCELLED_EVENTS`: Include cancelled events in the replacement snapshot when `true`.
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

### Layout / Display workflow (optional)

The sync tool can optionally create a simple full-screen layout for newly uploaded media, publish
that layout, assign it to a display group, and instruct players to show it immediately. These
behaviors are controlled via environment variables (disabled by default unless you enable them):

- `CREATE_LAYOUT_PER_UPLOAD`: When `true`, a full-screen layout is created for each uploaded media
  item using POST `/layout`, then the uploaded media is attached as the layout background before
  returned layout metadata is used for follow-up steps.
- `ASSIGN_LAYOUT_ON_CHANGE`: When `true`, created layouts are assigned to `DISPLAY_GROUP_ID` using
  `/displaygroup/{id}/layout/assign` so they become part of the group's schedule.
- `PUBLISH_ON_CHANGE`: When `true`, the script will call `/layout/publish/{id}` to publish the layout
  so players can play the new version immediately.
- `IMMEDIATE_SHOW_ON_CHANGE`: When `true`, the script attempts to send a change-layout action
  (`/displaygroup/{id}/action/changeLayout`) which instructs online players in the group to switch
  to the provided layout immediately. This will interrupt the current schedule while the action is
  in effect and requires players to be online and the API credentials to have sufficient privileges.

Notes:
- The implementation creates a real layout record with POST `/layout`, checks it out with PUT
  `/layout/checkout/{layoutId}`, then applies the uploaded media as the layout background with PUT
  `/layout/background/{layoutId}`. If checkout returns 422 "already checked out" (e.g. a stale lock
  left over from an earlier failed run), it discards that stale checkout with PUT
  `/layout/discard/{layoutId}` and retries checkout before setting the background. Once background
  is set, the draft is intentionally left as-is - discarding at that point abandons the edit and
  previously left the layout in a broken, still-locked state that blocked deleting it from the CMS
  GUI. `publish_layout()` is what finalizes (and releases) the draft afterward. Publish happens
  before tagging, since Xibo rejects tag changes on Draft layouts. If you need templated layouts or
  multi-region designs, extend the client helpers to create or modify layouts with templates and
  regions.
- `collect_now` (already implemented) remains useful: it triggers players to pull new library files.
  `IMMEDIATE_SHOW_ON_CHANGE` is delivered via XMR change-layout actions and may be used in
  combination with `collect_now` to speed up delivery to offline caches.
- `XIBO_UPLOAD_FIELD`: Multipart field name used by the upload API.
- `HASH_TAG_PREFIX`: Prefix used for sha256 tags, default `sha256:`.
- `LOG_LEVEL`: Logging level.
- `LOG_FILE`: Optional file log destination.

### Calendar upload

`--upload-calendar` selects the newest file matching
`office_calendar_events_YYYY-MM-DD_HH-MM-SS.json` when `CALENDAR_JSON_PATH` is a directory.
Selection uses the timestamp in the filename. The JSON must contain a Microsoft Graph-style
top-level `value` array.

The workflow creates the configured local DataSet when it is missing, ensures the curated calendar
columns exist, and imports the current snapshot with `overwrite=1`. This replacement behavior is
intentional: Xibo's CSV import endpoint has no unique-key/upsert option, so append-only imports
would duplicate events on every run. `eventIdentifier` preserves the source event ID for
downstream filtering and identification. Xibo-compatible camelCase headings are used because
the CMS rejects underscore headings and broadly rejects headings containing reserved tokens. The
remaining physical headings use opaque `c11`-`c18` names; their source mappings are documented in
the calendar schema. Cancelled events are excluded by default.

### Calendar HTML packages

`--upload-calendar-html` generates self-contained HTML calendar packages (`.htz` ZIP files), one
per configured view, and deploys each to a named Xibo layout.

#### Generation pipeline

1. Load the calendar JSON snapshot selected by `CALENDAR_JSON_PATH`.
2. For each view in `CALENDAR_HTML_VIEWS`, call `generate_calendar_packages()` from
   `calendar_html.py`. Each package is a ZIP archive containing a single `index.html` with
   embedded CSS — no external dependencies.
3. Packages are written to `scripts/calendar_packages/` with names such as
   `calendar_today_2026-08-20.htz`.
4. For each package + layout name pair, call `deploy_calendar_package_to_layout()` in `client.py`.

#### Deploy pipeline (`deploy_calendar_package_to_layout`)

1. **Upload** – `upload_html_package()` uploads the `.htz` file to the Xibo library using the
   existing `upload_media()` infrastructure. Mime type: `application/zip` (sent as
   `application/octet-stream` via the field-fallback path; Xibo accepts both for ZIP files).
2. **Layout** – `get_or_create_calendar_layout()` does a name-based lookup. If missing, it creates
   a new layout with the best-matching 1920×1080 resolution.
3. **Checkout** – the layout is checked out for editing before the package is assigned.
4. **Assign** – `assign_html_package_to_layout()` fetches `GET /layout?layoutId={layoutId}` with
  `embed=regions,playlists` to find the first region's `regionPlaylist.playlistId`. If the layout
  has no regions, it creates a 1920×1080 frame with `POST /region/{layoutId}` and re-fetches the
  layout. It then calls `POST /playlist/library/assign/{playlistId}` with `media[]={mediaId}`.
5. **Publish** – `publish_layout()` is called when `CALENDAR_AUTO_PUBLISH=true`.
6. **Re-resolve** – after publish the layout ID is re-fetched by name (Xibo may replace the
   draft with a new published ID).
7. **Tag** – the layout is tagged with `calendar-html`, `calendar-<view>`, and the date stamp.
8. **Display group** – optional assignment and immediate-show via the shared `DISPLAY_GROUP_ID`
   and `IMMEDIATE_SHOW_ON_CHANGE` settings.

> **⚠ CMS verification note**: `assign_html_package_to_layout` uses the
  `GET /layout?layoutId={layoutId}&embed=regions,playlists` → optional
  `POST /region/{layoutId}` → `POST /playlist/library/assign/{playlistId}` path. Verify the
  region/playlist embedding and the `media[]` parameter form against your target CMS version
  before relying on it in production.

#### Configuration keys

| Key | Default | Description |
|-----|---------|-------------|
| `CALENDAR_ENABLE_HTML` | `false` | Enable HTML package generation. Only the `--upload-calendar-html` flag triggers the workflow; this key can be used by external orchestration. |
| `CALENDAR_HTML_VIEWS` | `today,this_week,next_2_weeks` | Comma-separated views. Valid values: `today`, `this_week`, `next_2_weeks`. |
| `CALENDAR_LAYOUT_NAMES` | `Calendar - Today,Calendar - This Week,Calendar - Next 2 Weeks` | Comma-separated layout names. Order must match `CALENDAR_HTML_VIEWS`. |
| `CALENDAR_AUTO_PUBLISH` | `true` | Publish layouts after each package assignment. |
| `CALENDAR_TIMEZONE` | `UTC` | IANA timezone name used for event filtering and display. |
| `CALENDAR_PACKAGE_RETENTION_DAYS` | `30` | Delete local `.htz` files from `scripts/calendar_packages/` whose mtime exceeds this age. |

`CALENDAR_HTML_VIEWS` and `CALENDAR_LAYOUT_NAMES` must have the same number of entries; `load_config()` raises `ValueError` otherwise. Each view name must be one of the three supported identifiers.

#### Local cleanup

After each run, `.htz` files in `scripts/calendar_packages/` whose `mtime` is older than
`CALENDAR_PACKAGE_RETENTION_DAYS` days are deleted. Xibo library cleanup is out of scope for this
phase; manage old library items via the CMS UI or the `DELETE /library/{mediaId}` endpoint.

## Sync Flow

### `sync_xibo.py`

1. Load `.env` and show a settings summary.
2. Optionally run the configuration wizard and persist changes back to `.env`.
3. Build a local file index from `LOCAL_MEDIA_DIR`.
4. Call the Xibo API to list library items.
5. Build a remote index from library metadata.
6. Compare local and remote keys.
7. Upload missing local items if enabled.
8. Delete remote-only items if the user requested deletion.
9. Optionally trigger Collect Now.

Use `python .\sync_xibo.py --upload-calendar --dry-run --yes` to inspect the selected snapshot and
row count without changing Xibo. Calendar upload does not enter the media deletion workflow.

## Testing

Automated tests live under `scripts/tests/` and use `pytest` (it also runs plain `unittest.TestCase` tests unchanged):

- `scripts/tests/unit/`: tests a single module/function in isolation. No network, no real Xibo CMS, no filesystem paths outside `tmp_path`. Covers pure logic such as `config.py`, `env_io.py`, `media.py`, and `calendar_data.py`.
- `scripts/tests/integration/`: tests how multiple modules cooperate, e.g. `app.py` orchestration against a mocked `XiboClient`. Still fully offline; the CMS boundary is mocked/faked rather than hit over HTTP.

Setup and run:

```powershell
cd scripts
python -m pip install -r requirements-dev.txt
python -m pytest            # full suite
python -m pytest -m "not integration"   # unit tests only
```

**Convention: every new piece of custom logic needs tests.** Add a unit test for new
parsing/transformation/diffing/decision logic in the module it lives in. If the change also wires
together multiple modules (e.g. a new orchestration step in `app.py` calling `client.py`), add or
extend an integration test with a mocked `XiboClient` rather than exercising a real CMS. Keep tests
deterministic and offline; never depend on a live Xibo CMS or network access.

`scripts/api_tests.py` is a manual, ad-hoc experiment script, not part of the automated suite.

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
- Upload success is accepted for HTTP `200` and `201`, but the script then verifies the item in the
  library and requires Xibo to return it as `valid`. If the upload response omits `mediaId`, the
  script falls back to an exact library-name search using the uploaded filename.
- If Xibo accepts the upload response but the library item is missing or marked invalid, the run fails so unsupported files are surfaced immediately.
- After upload verification passes, the script tags the item with `MANAGED_TAG` and, in hash mode, the hash tag as well.

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

## How to extend

Common extension points:

- Add more CLI options with `argparse` in `scripts/xibo_sync/app.py`.
- Add additional metadata tagging after uploads in `scripts/xibo_sync/client.py`.
- Expand the delete guard logic in `scripts/xibo_sync/app.py`.
- Add support for another compare mode in `scripts/xibo_sync/media.py`.
## Recommended Maintenance Practice

When changing behavior:

1. Update this file first or in the same change.
2. Keep `Readme.md` focused on quick-start and user guidance.
3. Add a dry-run verification step before any destructive run.
4. Run `python -m py_compile scripts/*.py` or the equivalent syntax check.
5. Add or update tests under `scripts/tests/` for any new or changed custom logic, then run `python -m pytest` from `scripts/` (see [Testing](#testing)).

## Docker / Xibo CMS Reference

The repo includes a full Xibo Docker distribution under `xibo/xibo-docker-4.4.2/`.
The actual compose file is `xibo/xibo-docker-4.4.2/docker-compose.yml`.

The AI-agent orientation file lives at `AI_AGENTS.md`.

Typical setup flow:

1. Start the stack with `docker compose up -d` in that directory.
2. Open the CMS web UI.
3. Complete the initial installer.
4. Create an OAuth application in Administration → Applications.
5. Copy the client credentials into `scripts/.env`.

For user-facing run instructions, see [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
