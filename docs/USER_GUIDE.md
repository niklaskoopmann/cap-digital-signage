# User Guide

This guide explains how to run the sync tool, how it behaves, and how to start Xibo with Docker.

## What this tool does

The sync scripts copy media from the local `media/` folder into the Xibo CMS library.

One script is available:

- `scripts/sync_xibo.py`: use this when the machine is already on the network that can reach Xibo.

The script is now a launcher that calls the modular implementation in `scripts/xibo_sync/`, but the run command stays the same.

The main sync actions are:

1. Read configuration from `scripts/.env`.
2. Show the current settings in the terminal.
3. Optionally let you edit the settings interactively.
4. Scan the local media folder.
5. Read the media library from Xibo.
6. Compare local and remote media.
7. Upload missing files.
8. Optionally delete remote-only files.
9. Optionally create/publish/assign layouts for uploaded media and ask players to show them.
	 - If `CREATE_LAYOUT_PER_UPLOAD=true`, the script will create a full-screen layout for each newly
		 uploaded media item by creating a stored layout in Xibo and applying the uploaded media as the
		 layout background after checking the layout out for editing. This is a convenience workflow for
		 immediately displaying single images on players.
	 - If `ASSIGN_LAYOUT_ON_CHANGE=true` and `DISPLAY_GROUP_ID` is set, the created layout is assigned
		 to the group so it enters the group's schedule.
	 - If `PUBLISH_ON_CHANGE=true`, the layout is published after creation so players see a published
		 version to play.
	 - If `IMMEDIATE_SHOW_ON_CHANGE=true`, the script sends a change-layout action to the display
		 group which is delivered to online players and will make them show the layout immediately.

The calendar workflow is separate from media sync. It selects the newest timestamped calendar JSON
snapshot and replaces the configured Xibo DataSet with that snapshot.

## Before you start

You need:

- Python 3.10 or newer.
- Access to the Xibo CMS.
- A filled `scripts/.env` file.
- Your media files in the `media/` folder.
- The local virtual environment at `scripts/.venv`.

Install the Python dependencies once:

```powershell
cd C:\Path\To\cap-digital-signage\scripts
\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Activate `scripts/.venv` before every Python or pip command so package installs and execution stay local to this repository.

## How to run the sync

### Safe preview first

Use dry-run mode if you want to see what would happen without uploading or deleting anything:

```powershell
\.venv\Scripts\Activate.ps1
python .\sync_xibo.py --dry-run --yes
```

### Calendar preview

Preview the newest calendar snapshot without changing Xibo:

```powershell
\.venv\Scripts\Activate.ps1
python .\sync_xibo.py --upload-calendar --dry-run --yes
```

Run the calendar replacement:

```powershell
\.venv\Scripts\Activate.ps1
python .\sync_xibo.py --upload-calendar --yes
```

When `CALENDAR_JSON_PATH` points to a directory, the script selects the newest file named like
`office_calendar_events_2026-08-19_13-10-01.json`, based on the timestamp in the filename.

### Real sync

If the machine is already on the correct network:

```powershell
\.venv\Scripts\Activate.ps1
python .\sync_xibo.py --yes
```

## Command-line options

- `--config`: open the interactive configuration editor and exit.
- `--yes` or `-y`: run non-interactively and accept defaults.
- `--dry-run`: show the plan but do not upload or delete.
- `--delete` / `--no-delete`: force deletion behavior for that run.

Deletion behavior is controlled in two places:

- `DELETE_REMOTE_NOT_LOCAL` in `scripts/.env` sets the default answer.
- `--delete` or `--no-delete` overrides that default for a single run.

If deletion is enabled, the script only deletes remote items that are not present locally. When `ONLY_DELETE_MANAGED_TAG=true`, it further restricts deletes to items managed by this sync tool.

After each upload, the script checks Xibo's library for the new media item. If Xibo accepts the request but does not show the file as a valid library item, the run stops with an error so unsupported files are not treated as synced.

New uploads are tagged only after that verification passes, and they are tagged again after the upload succeeds. This keeps future delete runs safe because managed media can be recognized even after a restart.

Example:

```powershell
\.venv\Scripts\Activate.ps1
python .\sync_xibo.py --dry-run --yes
```

## What the settings mean

The most important entries in `scripts/.env` are:

- `CMS_BASE_URL`: the Xibo CMS address.
- `AUTH_MODE`: `oauth` is recommended for a protected CMS.
- `CMS_CLIENT_ID` and `CMS_CLIENT_SECRET`: OAuth credentials from Xibo.
- `LOCAL_MEDIA_DIR`: the folder containing your media.
- `COMPARE_MODE`: `filename` is easiest to understand; `hash` compares file hashes.
- `MANAGED_TAG`: protects unrelated Xibo items from deletion.
- `ONLY_DELETE_MANAGED_TAG`: keep this enabled unless you are sure.
- `DELETE_REMOTE_NOT_LOCAL`: default answer for the delete question.
- `DISPLAY_GROUP_ID`: optional Xibo display group to refresh after sync.
- `CALENDAR_JSON_PATH`: calendar snapshot directory or JSON file; relative paths resolve from `scripts/`.
- `CALENDAR_DATASET_NAME`: target DataSet, default `office_calendar_events`.
- `CALENDAR_DATASET_CODE`: optional stable DataSet code.
- `CALENDAR_UPLOAD_CANCELLED_EVENTS`: `false` excludes cancelled events; set `true` to include them.

Calendar runs replace the DataSet contents. This avoids duplicates because Xibo's CSV import API
does not support an upsert key. The uploaded schema includes `eventIdentifier` and the other
curated calendar fields described in the technical documentation.

## Calendar HTML Packages

The `--upload-calendar-html` command generates self-contained HTML calendar widgets, packages them
as `.htz` files, and deploys each one to a named layout in Xibo — no manual CMS steps required.

### Prerequisites

- Python 3.10+ with the dependencies from `requirements.txt` installed.
- Activate `scripts/.venv` before running commands.
- A working Xibo CMS reachable at `CMS_BASE_URL`.
- `CALENDAR_JSON_PATH` pointing to a directory containing a valid calendar snapshot.
- Target layouts do not need a pre-created region.
	If a layout has no regions, the deploy flow attempts to create a full-screen region automatically
	and then assigns the package to that region's playlist.

### Running

Preview without changing anything:

```powershell
cd scripts
\.venv\Scripts\Activate.ps1
python sync_xibo.py --upload-calendar-html --dry-run --yes
```

Deploy all configured views:

```powershell
\.venv\Scripts\Activate.ps1
python sync_xibo.py --upload-calendar-html --yes
```

### Configuration

The calendar HTML generation is configured by environment variables in `scripts/.env`:

```env
# ---- Calendar HTML packages ----
# Enable calendar HTML package generation
CALENDAR_ENABLE_HTML=true

# Views to generate (comma-separated)
CALENDAR_HTML_VIEWS=today,this_week,next_2_weeks

# Optional: path to the template directory (defaults to scripts/templates/calendar)
# CALENDAR_TEMPLATE_DIR=templates/calendar

# Publish layouts automatically after each package upload
CALENDAR_AUTO_PUBLISH=true

# IANA timezone for event filtering and time display labels (default: Europe/Berlin).
# The calendar uses this timezone for both selecting which events appear (local calendar days)
# and formatting times on the display (e.g., 14:00 for UTC 12:00 during CEST summer time).
# Daylight saving transitions are handled automatically (CEST/CET for Europe/Berlin).
# Example: Europe/Berlin, Europe/Amsterdam, UTC
CALENDAR_TIMEZONE=Europe/Berlin

# Delete local packages older than this many days
CALENDAR_PACKAGE_RETENTION_DAYS=1
```

**Important**: View names in `CALENDAR_HTML_VIEWS` must have corresponding configuration files:

- Each view name (e.g., `today`, `this_week`) requires a file named `views/<name>.json` in the
  template directory.
- Each `.json` file specifies the view's title, time window, and the Xibo layout name to deploy to.

The bundled templates come with three views: `today`, `this_week`, and `next_2_weeks`. Their
layout names are `Calendar Today`, `Calendar This Week`, and `Calendar Next 2 Weeks`.

### Customizing Views

#### Edit an existing view's title or window

1. Open the view's config file (e.g., `scripts/templates/calendar/views/today.json`)
2. Edit the `title`, `window_days`, or `layout_name` fields as needed
3. Re-run the sync

Example: to change the "Today" view to show 3 days instead of 1:

```json
{
  "title": "Today & 2 More Days",
  "window_days": 3,
  "layout_name": "Calendar Today"
}
```

#### Add a custom view

1. Create a new config file `scripts/templates/calendar/views/<myview>.json`:

```json
{
  "title": "My Custom View",
  "window_days": 30,
  "layout_name": "Calendar Custom"
}
```

2. Add the view name to `CALENDAR_HTML_VIEWS` in `scripts/.env`:

```env
CALENDAR_HTML_VIEWS=today,this_week,next_2_weeks,myview
```

3. Re-run the sync.

No Python code changes required! The new view will be generated and deployed just like the bundled views.

### Preview Templates

To preview a template with sample data before running the full sync:

```powershell
cd scripts
\.venv\Scripts\Activate.ps1
python render_template_preview.py
```

This generates a `template.preview.html` file in the template directory. Open it in VS Code
(right-click > **Open in Simple Browser**) or a web browser to see how your template looks with
sample events.

For a custom template directory:

```powershell
\.venv\Scripts\Activate.ps1
python render_template_preview.py --template-dir ../my-templates/calendar
```

### Troubleshooting

**"View config not found" error**
Check that the view name in `CALENDAR_HTML_VIEWS` has a corresponding `.json` file in the
`views/` subdirectory of your template directory. For example, a view named `myview` requires
`views/myview.json`.

**Region auto-creation fails for a layout**
When a target layout has no regions, the deploy flow attempts to create one automatically.
If this step fails, verify the API user can edit layouts and create regions, then re-run.

**"No calendar packages were generated"**
Check that `CALENDAR_JSON_PATH` resolves to a directory containing a valid snapshot file, and
that `CALENDAR_HTML_VIEWS` contains at least one valid view name with a corresponding `.json` config
file.

**Empty events in a view**
The generated package renders "No events scheduled" when no events fall in the view window. This
is expected — verify the snapshot is current and the configured `CALENDAR_TIMEZONE` matches the
timezone of your events.

**Wrong event times**
Set `CALENDAR_TIMEZONE` to the correct IANA timezone (e.g. `Europe/Amsterdam`). Events with
a different embedded timezone will log a warning and be displayed in the event's own timezone.

## The first run

On the first run, the script will usually show the current settings and ask whether you want to edit them.

Recommended first run flow:

1. Put a few test media files into `media/`.
2. Make sure `scripts/.env` points to the correct CMS.
3. Run a dry-run.
4. Check the output.
5. Run the real sync.

## Running Xibo in Docker

The repository contains an Xibo Docker distribution under `xibo/xibo-docker-4.4.2/`.

### Windows

Recommended setup:

- Install Docker Desktop.
- Enable the WSL2 backend if available.
- Start Docker Desktop.

Then run:

```powershell
cd C:\Path\To\cap-digital-signage\xibo\xibo-docker-4.4.2
docker compose up -d
```

Open the CMS in your browser, usually at `http://localhost:8080` or the port configured in the compose file.

### Linux

Install Docker and the Docker Compose plugin, then run:

```bash
cd /path/to/cap-digital-signage/xibo/xibo-docker-4.4.2
docker compose up -d
```

Open the CMS in your browser, usually at `http://<host-ip>:8080`.

## Xibo Player on Linux Ubuntu

The `xibo/xibo-player/` folder contains notes for the open source player options. For a Linux Ubuntu target machine, the Electron player is the practical default.

### Electron player

Install or unpack the Xibo Electron player on the target machine, then run:

```bash
xiboplayer
```

The player keeps its config under:

```text
/home/digitalsignage/.config/xiboplayer/electron
```

Use the player README in `xibo/xibo-player/Readme.md` for the upstream links and extra notes.

### Arexibo

Arexibo is also listed in the player notes, but the current local documentation says it does not work well for offline or non-internet setups. Use Electron if the target machine needs to run without reliable internet access.

## First steps in Xibo

After the CMS starts for the first time:

1. Complete the web installer.
2. Log in as an administrator.
3. Go to Administration → Applications.
4. Create a new Application using Client Credentials.
5. Copy the `Client Id` and `Client Secret` into `scripts/.env`.
6. Edit the created Application.
7. Under General, select `Authorisation Code`, `Client Credentials` and `Is Confidential`
8. Under Sharing, select all Scopes.
9. Optionally create a dedicated folder for synced media and store the folder ID in `MANAGED_FOLDER_ID`.

## Troubleshooting

- If deletion does not happen, check `DELETE_REMOTE_NOT_LOCAL` and `ONLY_DELETE_MANAGED_TAG`.