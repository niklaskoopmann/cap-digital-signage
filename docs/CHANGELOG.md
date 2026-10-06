# Changelog

This document records planned and implemented repository changes. Agents use it to identify related
work that has already been completed.

## Entry Format

### YYYY-MM-DD - Change title
- Status: `Planned` or `Implemented`
- Summary: Concise description of the change.
- Plan: `docs/PLAN.md`

## Changes

### 2026-10-06 - Two-phase sync with Microsoft Graph download
- Status: `Planned`
- Summary: Add phase 1 that downloads Outlook calendar events (`calendarView`) and SharePoint media through Microsoft Graph (MSAL client credentials) while online, pause for the manual switch to the Xibo hotspot with a verified health check, then run the existing media sync and calendar DataSet upload as phase 2. Adds `SYNC_MODE`/`--mode` (`full`, `download`, `upload`), renames `LOCAL_MEDIA_DIR` to `SHAREPOINT_MEDIA_DIR`, adds `CALENDAR_IMAGE_DIR`, and removes `--upload-calendar`.
- Plan: [docs/PLAN.md](PLAN.md)

### 2026-09-17 - Cron-based scheduling for the calendar render service
- Status: `Implemented`
- Summary: Move the calendar render service off its in-process sleep loop onto `supercronic` cron scheduling inside the container. The job becomes a one-shot process that runs a single cycle and exits with a meaningful status, with `CALENDAR_RENDER_CRON` (default `0 0 * * *`), `CALENDAR_RENDER_RUN_ON_START`, and `TZ`; `flock` prevents overlapping cycles so a crashed or killed cycle can no longer stop or drift the schedule.
- Plan: `docs/PLAN.md`
- Validation: 28 focused and 183 full offline tests passed; live Docker validation passed for image checksum verification, startup execution, scheduled ticks, overlap protection, startup failure isolation, timezone handling, and CMS cleanup.

### 2026-09-14 - Bounded multi-event calendar layout and embedded background
- Status: `Implemented`
- Summary: Render calendar images with one column through four events, two across-row columns through eight events, an explicit `8 of N events` cap beyond eight, and a self-contained background image that resolves in Playwright-generated Xibo uploads without viewport scrolling. Added asset-path validation, boundary tests, and a local Playwright layout harness.
- Validation: Bundled preview rendered successfully; local Playwright harness passed 6 cases; full offline pytest passed 170 tests.
- Plan: `docs/PLAN.md`

### 2026-09-14 - Use speaking names for calendar DataSet columns
- Status: `Implemented`
- Summary: The main calendar uploader now migrates legacy positional `c11`-`c18` DataSet headings to speaking names in place, uses safe names `availability` and `eventType` instead of reserved `showAs` and `type`, and removes obsolete aliases after import while preserving canonical column IDs and data.
- Plan: `docs/PLAN.md`

### 2026-09-14 - Calendar render service logging and startup run
- Status: `Implemented`
- Summary: The calendar render service now configures console logging (level via `LOG_LEVEL`, default `INFO`) so cycle progress, warnings, and failures reach the container logs, and runs an initial render/upload cycle immediately on startup or restart instead of only waiting for the next local-midnight schedule.
- Plan: `docs/PLAN.md`

### 2026-09-11 - Calendar render service old-layout cleanup and schedule inheritance
- Status: `Implemented`
- Summary: Extends the host-local calendar render service so each daily cycle also finds the previous cycle's layout/media for the same view (via a stable per-view tag), clones forward any explicit Xibo schedule events attached to the old layout onto the new one, then removes the old layout, its schedule events, its display group assignment, and its media, gated by a new opt-out flag. Cleanup failures are isolated per item and do not fail the cycle.
- Validation: `scripts/.venv` test suite passed with `python -m pytest` for the full repo after the render-service cleanup fix and related documentation updates.
- Plan: `docs/PLAN.md`

### 2026-09-10 - Host-local calendar render service for staleness fix
- Status: `Implemented`
- Summary: Added a new Docker service on the Xibo host that reads the already-synced calendar DataSet via the Xibo CMS API and re-renders/re-uploads `today`/`this_week`/`next_2_weeks` PNGs on its own daily schedule, reusing `scripts/xibo_sync`'s upload/layout pipeline, fixing displayed calendars going stale when the uploader PC does not run for multiple days. `--upload-calendar-html` is kept unchanged as a manual/fallback path. Also fixes the current calendar DataSet upload column-mapping bug against a real export shape fixture, and adds a `CALENDAR_EVENT_RETENTION_DAYS` (default 30) cutoff enforced both at uploader import and at render-service fetch time so old events stop accumulating in the Xibo DataSet.
- Validation: 148 offline tests passed; the WSL live harness passed both per-upload layout modes and cleanup; and the deployed container completed a real DataSet render cycle with 7 events, 3 PNGs, and 3 uploads.
- Plan: `docs/PLAN.md`

### 2026-09-10 - Electron-compatible calendar PNG media workflow
- Status: `Implemented`
- Summary: Replaced calendar HTZ deployment with Playwright-rendered 1920x1080 PNGs in `LOCAL_MEDIA_DIR`, verified media uploads with calendar tags, shared optional per-upload full-screen layouts, and non-persistent dry-run behavior.
- Plan: `docs/PLAN.md`

### 2026-09-01 - Historical implemented-feature changelog backfill
- Status: `Implemented`
- Summary: Added concise, commit-supported historical records for completed sync, calendar, layout, and cleanup features.
- Plan: `docs/PLAN.md`

### 2026-09-01 - Planner workflow and agent artifact lifecycle
- Status: `Implemented`
- Summary: The planner now clarifies material uncertainties, creates missing plan and changelog files, and records planned work. The coder marks validated changes as implemented; failure-report agents create their temporary report files on demand.
- Plan: `docs/PLAN.md`

### 2026-09-01 - Sync-owned layout and media cleanup
- Status: `Implemented`
- Summary: Added tested cleanup for remote-only sync-owned layouts and their media, including schedule and display-group handling, while retaining unrelated layouts.
- Plan: `docs/PLAN.md`

### 2026-08-25 - Timezone-aware calendar display
- Status: `Implemented`
- Summary: Corrected calendar display timezone handling so displayed times use the configured IANA timezone.
- Plan: `docs/PLAN.md`

### 2026-08-24 - Template-driven HTML calendar packages
- Status: `Implemented`
- Summary: Added configurable Jinja2 calendar HTML package generation, per-view configuration, editable templates, and local preview support.
- Plan: `docs/PLAN.md`

### 2026-08-20 - Calendar DataSet upload and offline tests
- Status: `Implemented`
- Summary: Added JSON calendar snapshot import to Xibo DataSets and the pytest unit/integration test structure for offline validation.
- Plan: `docs/PLAN.md`

### 2026-08-18 - Layout creation compatibility
- Status: `Implemented`
- Summary: Corrected Xibo layout creation and publication interactions for the per-upload layout workflow.
- Plan: `docs/PLAN.md`

### 2026-08-17 - Per-upload full-screen layouts
- Status: `Implemented`
- Summary: Added optional creation, publication, assignment, and immediate display of a full-screen layout for each uploaded media item.
- Plan: `docs/PLAN.md`

### 2026-08-04 - Modular sync and upload verification
- Status: `Implemented`
- Summary: Split the sync script into focused modules and added post-upload library validation before media is treated as successfully synced.
- Plan: `docs/PLAN.md`
