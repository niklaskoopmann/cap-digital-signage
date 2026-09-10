# AI Agent Summary

This repository centers on the Xibo media sync workflow.

## Primary entry point

- `scripts/sync_xibo.py` is the main script to run.
- The sync logic now lives in `scripts/xibo_sync/`, but this script remains the entry point.
- For any Python or pip command, run from `scripts/` and activate `scripts/.venv` first:
  `.\.venv\Scripts\Activate.ps1`.

## Where to look first

- `docs/` contains the user and technical documentation for the sync tool.
- `docs/USER_GUIDE.md` explains setup and day-to-day usage.
- `docs/TECHNICAL.md` explains the repository layout, sync flow, and extension points.

## Xibo installation used here

- The bundled Xibo CMS installation lives in `xibo/xibo-docker-4.4.2/`.
- Use `xibo/xibo-docker-4.4.2/docker-compose.yml` for the Docker-based CMS stack.
- The Xibo API specification is in `xibo/docs/swagger.json`.
- Calendar snapshots are uploaded with `scripts/sync_xibo.py --upload-calendar`; the newest
	`office_calendar_events_yyyy-MM-dd_HH-mm-ss.json` file is selected and replaces the target DataSet.
- `scripts/sync_xibo.py --upload-calendar-html` renders configured Jinja views to 1920x1080 PNGs in
  `LOCAL_MEDIA_DIR` and uploads them as normal media. It does not deploy legacy reusable calendar
  layouts, but `CREATE_LAYOUT_PER_UPLOAD=true` applies the normal per-media layout lifecycle. Install
  the Python dependency and browser with `pip install -r requirements.txt` and `playwright install chromium`.

## Player reference

- `xibo/xibo-player/` contains notes for the open source Xibo player.
- The player runs on Electron and has a separate Arexibo option.
- Current local notes indicate Arexibo is not suitable for offline or non-internet setups.

## What to keep aligned

- Keep the root `Readme.md` short and user-facing.
- Keep this file updated when the main script, Docker path, API spec, or player instructions change.
- In Xibo 4.4, publish media-created layouts after every final mutation, including ownership tags,
  and re-resolve the canonical ID before assigning or showing the layout. Cleanup may discard a
  locked draft only when its exact `xibo-sync-media:<mediaId>` ownership tag proves it is sync-owned.

## Testing

- Automated tests live under `scripts/tests/unit/` (isolated logic) and `scripts/tests/integration/`
  (multiple modules cooperating, e.g. `app.py` orchestration against a mocked `XiboClient`).
- Live-CMS system tests live under `scripts/tests/system/`. They are persistent, feature-specific
  test modules that invoke `sync_xibo.py` without `--dry-run` and query the CMS with their own
  test client. They must be excluded from the default pytest collection and require an explicit
  test-only `.env` path (for example, `SYSTEM_TEST_ENV_FILE`) rather than reading `scripts/.env`.
- Every new piece of custom logic added to `scripts/xibo_sync/` must ship with a test: unit test in
  the matching module's test file, plus an integration test if the change wires modules together.
- Run tests with `python -m pytest` from `scripts/` after activating `scripts/.venv` (install `scripts/requirements-dev.txt` first). See
  the "Testing" section in `docs/TECHNICAL.md` for full details.
- The change pipeline is Digital Signage Coder, Digital Signage Tester, Digital Signage Code
  Reviewer, then Digital Signage System Tester. The final system tester runs non-dry-run,
  feature-specific checks only against the running local Docker CMS, based on `docs/PLAN.md` and
  the coder's changes. It verifies results through the CMS API and records live failures in
  `docs/SYSTEM_TEST_RESULTS.md` for the coder.
