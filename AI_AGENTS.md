# AI Agent Summary

This repository centers on the Xibo media sync workflow.

## Primary entry point

- `scripts/sync_xibo.py` is the main script to run.
- The sync logic now lives in `scripts/xibo_sync/`, but this script remains the entry point.

## Where to look first

- `docs/` contains the user and technical documentation for the sync tool.
- `docs/USER_GUIDE.md` explains setup and day-to-day usage.
- `docs/TECHNICAL.md` explains the repository layout, sync flow, and extension points.

## Xibo installation used here

- The bundled Xibo CMS installation lives in `xibo/xibo-docker-4.4.2/`.
- Use `xibo/xibo-docker-4.4.2/docker-compose.yml` for the Docker-based CMS stack.
- The Xibo API specification is in `xibo/docs/swagger.json`.

## Player reference

- `xibo/xibo-player/` contains notes for the open source Xibo player.
- The player runs on Electron and has a separate Arexibo option.
- Current local notes indicate Arexibo is not suitable for offline or non-internet setups.

## What to keep aligned

- Keep the root `Readme.md` short and user-facing.
- Keep this file updated when the main script, Docker path, API spec, or player instructions change.