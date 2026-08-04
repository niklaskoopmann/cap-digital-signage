# User Guide

This guide explains how to run the sync tool, how it behaves, and how to start Xibo with Docker.

## What this tool does

The sync scripts copy media from the local `media/` folder into the Xibo CMS library.

One script is available:

- `scripts/sync_xibo.py`: use this when the machine is already on the network that can reach Xibo.

The main sync actions are:

1. Read configuration from `scripts/.env`.
2. Show the current settings in the terminal.
3. Optionally let you edit the settings interactively.
4. Scan the local media folder.
5. Read the media library from Xibo.
6. Compare local and remote media.
7. Upload missing files.
8. Optionally delete remote-only files.
9. Optionally ask Xibo players to refresh content.

## Before you start

You need:

- Python 3.10 or newer.
- Access to the Xibo CMS.
- A filled `scripts/.env` file.
- Your media files in the `media/` folder.

Install the Python dependencies once:

```powershell
cd C:\Path\To\cap-digital-signage\scripts
py -m pip install -r requirements.txt
```

## How to run the sync

### Safe preview first

Use dry-run mode if you want to see what would happen without uploading or deleting anything:

```powershell
py .\sync_xibo.py --dry-run --yes
```

### Real sync

If the machine is already on the correct network:

```powershell
py .\sync_xibo.py --yes
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

New uploads are tagged during upload and are tagged again after the upload succeeds. This keeps future delete runs safe because managed media can be recognized even after a restart.

Example:

```powershell
py .\sync_xibo.py --dry-run --yes
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

- If you see `401 Unauthorized`, verify `AUTH_MODE=oauth` and the client credentials.
- If deletion does not happen, check `DELETE_REMOTE_NOT_LOCAL` and `ONLY_DELETE_MANAGED_TAG`.