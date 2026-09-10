# Changelog

This document records planned and implemented repository changes. Agents use it to identify related
work that has already been completed.

## Entry Format

### YYYY-MM-DD - Change title
- Status: `Planned` or `Implemented`
- Summary: Concise description of the change.
- Plan: `docs/PLAN.md`

## Changes

### 2026-09-10 - Electron-compatible calendar PNG media workflow
- Status: `Planned`
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
