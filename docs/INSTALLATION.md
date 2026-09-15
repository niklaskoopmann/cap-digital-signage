# Installation

> Debian/Linux installation runbook for the Xibo CMS host, xibo-sync tooling, calendar generation service, Portainer, and Electron player.

- [Installation](#installation)
  - [Official References](#official-references)
  - [Target Architecture](#target-architecture)
  - [Prerequisites](#prerequisites)
  - [1. Install Docker on Debian](#1-install-docker-on-debian)
  - [2. Put the Repository on the Host](#2-put-the-repository-on-the-host)
  - [3. Configure and Start Xibo](#3-configure-and-start-xibo)
  - [4. Complete the Xibo CMS Installer](#4-complete-the-xibo-cms-installer)
  - [5. Create the Xibo OAuth Applications](#5-create-the-xibo-oauth-applications)
  - [6. Configure xibo-sync](#6-configure-xibo-sync)
  - [7. Configure the Calendar Generation Service](#7-configure-the-calendar-generation-service)
  - [8. First Launch of Portainer](#8-first-launch-of-portainer)
  - [9. Install and Register the Electron Player](#9-install-and-register-the-electron-player)
  - [10. Authorize the Xibo Player](#10-authorize-the-xibo-player)
  - [11. First Operational Checks](#11-first-operational-checks)
  - [Future Setup Step](#future-setup-step)

## Official References

- Docker Engine on Debian: <https://docs.docker.com/engine/install/debian/>
- Docker Linux post-installation steps: <https://docs.docker.com/engine/install/linux-postinstall/>
- Docker Compose: <https://docs.docker.com/compose/>
- Xibo CMS Docker installation: <https://xibosignage.com/docs/setup/xibo-on-docker>
- Xibo CMS configuration: <https://xibosignage.com/docs/setup/configuration>
- Xibo API overview: <https://xibosignage.com/docs/developer/api-overview>
- Xibo for Linux player documentation: <https://xibosignage.com/docs/player/installing-xibo-for-linux>
- Portainer Docker standalone installation: <https://docs.portainer.io/start/install-ce/server/docker/linux>

## Target Architecture

The Debian host runs the bundled Docker Compose stack in `xibo/xibo-docker-4.4.2/`:

- Xibo CMS web container on port `80`.
- Xibo XMR on port `9505`.
- MySQL, memcached, and quickchart as internal support services.
- The calendar generation service as an internal host-local container with no published port.
- Portainer on ports `9000` and `9443` for container administration.

The host also keeps a local Python environment under `scripts/.venv` for operator-run `xibo-sync` commands.

## Prerequisites

- Debian machine with sudo access.
- Network access to pull Docker images and Python packages during installation.
- This repository available on the target machine.
- DNS name or static IP address planned for the Xibo CMS host.
- Xibo licence/subscription and Portainer activation token if your deployment requires them.

## 1. Install Docker on Debian

Follow Docker's Debian guide for the authoritative package setup. The short form is:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Optional but recommended for day-to-day operation:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker version
docker compose version
```

See Docker's Linux post-installation guide before enabling non-root Docker access on a shared or untrusted machine.

## 2. Put the Repository on the Host

Place the repository in a stable location, for example:

```bash
sudo mkdir -p /opt/cap-digital-signage
sudo chown "$USER":"$USER" /opt/cap-digital-signage
cd /opt/cap-digital-signage
```

Copy, clone, or deploy the repository contents into that directory. The expected folders include `scripts/`, `services/`, `media/`, and `xibo/`.

## 3. Configure and Start Xibo

Prepare the bundled Xibo Docker configuration:

```bash
cd /opt/cap-digital-signage/xibo/xibo-docker-4.4.2
cp config.env.template config.env
```

Edit `config.env` for the target host. At minimum, review CMS host name, database settings, mail settings, and any environment-specific Xibo values required by the official Xibo Docker guide.

Start the stack:

```bash
docker compose up -d
```

Check status and logs:

```bash
docker compose ps
docker compose logs -f cms-web
```

## 4. Complete the Xibo CMS Installer

Open the CMS in a browser:

```text
http://<host-ip-or-dns>/
```

Complete the initial Xibo installer. Record the CMS administrator account in the project's secure credential store, not in version control.

## 5. Create the Xibo OAuth Applications

In the Xibo CMS, create two separate OAuth applications under `Administration > Applications`:

- `xibo-sync`: used by operator-run commands from `scripts/sync_xibo.py`.
- `calendar-generation`: used by the unattended Docker calendar render service.

Use client credentials for both applications. Store each client ID and client secret separately so the service credential can be rotated without changing the operator credential.

The applications need permissions for their configured workflows, including media upload, library lookup/tagging, DataSet read/write as applicable, layout creation/publish/tagging when `CREATE_LAYOUT_PER_UPLOAD=true`, display-group actions when assignment or immediate show is enabled, and Collect Now when configured.

## 6. Configure xibo-sync

Create and activate the Python virtual environment:

```bash
cd /opt/cap-digital-signage/scripts
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
playwright install chromium
```

Create the local environment file:

```bash
cp .env.example .env 2>/dev/null || true
```

If no `.env.example` exists in your checkout, create `scripts/.env` manually using the keys documented in [TECHNICAL.md](TECHNICAL.md). Minimum values for authenticated operation are:

```dotenv
CMS_BASE_URL=http://<host-ip-or-dns>
CMS_VERIFY_TLS=false
CMS_TIMEOUT_SECONDS=30
AUTH_MODE=oauth
CMS_CLIENT_ID=<xibo-sync-client-id>
CMS_CLIENT_SECRET=<xibo-sync-client-secret>
LOCAL_MEDIA_DIR=../media
MANAGED_TAG=xibo-sync
ONLY_DELETE_MANAGED_TAG=true
```

Run a dry-run check:

```bash
python sync_xibo.py --dry-run --yes
```

For calendar DataSet uploads, configure `CALENDAR_JSON_PATH` and run:

```bash
python sync_xibo.py --upload-calendar --dry-run --yes
```

## 7. Configure the Calendar Generation Service

Create the service environment file from the tracked template:

```bash
cd /opt/cap-digital-signage
cp services/calendar_render_service/.env.example services/calendar_render_service/.env
```

Edit `services/calendar_render_service/.env`:

```dotenv
CMS_BASE_URL=http://cms-web
CMS_VERIFY_TLS=false
CMS_CLIENT_ID=<calendar-generation-client-id>
CMS_CLIENT_SECRET=<calendar-generation-client-secret>
CALENDAR_DATASET_NAME=office_calendar_events
CALENDAR_HTML_VIEWS=today,this_week,next_2_weeks
CALENDAR_TEMPLATE_DIR=/app/templates/calendar
CALENDAR_TIMEZONE=Europe/Berlin
CALENDAR_RENDER_OUTPUT_DIR=/tmp/calendar-render
MANAGED_TAG=xibo-sync
CREATE_LAYOUT_PER_UPLOAD=false
CALENDAR_CLEANUP_OLD_VIEW_UPLOADS=true
```

Use `http://cms-web` for the service when it runs inside the same Docker Compose network. Enable `CREATE_LAYOUT_PER_UPLOAD`, `ASSIGN_LAYOUT_ON_CHANGE`, `IMMEDIATE_SHOW_ON_CHANGE`, and `DISPLAY_GROUP_ID` only after the CMS display groups and player registration are ready.

Rebuild and restart the stack so the service image is available:

```bash
cd /opt/cap-digital-signage/xibo/xibo-docker-4.4.2
docker compose up -d --build calendar-render-service
```

Watch the service logs:

```bash
docker compose logs -f calendar-render-service
```

The service runs once at startup, then daily according to `CALENDAR_TIMEZONE` and `CALENDAR_RENDER_SCHEDULE_SECONDS`.

## 8. First Launch of Portainer

Open Portainer from the Debian host or an admin workstation:

```text
http://<host-ip-or-dns>:9000
https://<host-ip-or-dns>:9443
```

On first launch:

1. Create the initial administrator user.
2. Enter the Portainer activation token if prompted by the deployment.
3. Select the local Docker environment.
4. Confirm that the Xibo stack, CMS containers, calendar render service, and Portainer volume are visible.

If the first-launch setup expires before completion, restart the Portainer container from the host and open it again promptly:

```bash
cd /opt/cap-digital-signage/xibo/xibo-docker-4.4.2
docker compose restart portainer
```

## 9. Install and Register the Electron Player

Install the Xibo Linux/Electron player according to the official player documentation and the local notes in `xibo/xibo-player/`.

The local player config reference is:

```text
xibo/xibo-player/config.json.electron
```

Configure the player to use the CMS address that is reachable from the player machine, for example:

```text
http://<host-ip-or-dns>/
```

For an all-in-one signage machine, the player, CMS, and Docker services may run on the same Debian host. For a separate display device, ensure the player can reach the CMS HTTP port and XMR port according to the Xibo player documentation.

## 10. Authorize the Xibo Player

Start the Electron player. It should display a registration or licence request in the CMS.

In the Xibo CMS:

1. Open the Displays or player authorization area.
2. Find the pending player.
3. Authorize it.
4. Assign it to the intended display group.
5. Confirm it checks in successfully.

After authorization, upload a small test image with `xibo-sync`, assign a layout if that workflow is enabled, and confirm the player collects and displays content.

## 11. First Operational Checks

Run these checks from the Debian host:

```bash
cd /opt/cap-digital-signage/xibo/xibo-docker-4.4.2
docker compose ps
docker compose logs --tail=100 cms-web
docker compose logs --tail=100 calendar-render-service
```

Run a dry-run sync:

```bash
cd /opt/cap-digital-signage/scripts
. .venv/bin/activate
python sync_xibo.py --dry-run --yes
```

If calendar upload is configured, run:

```bash
python sync_xibo.py --upload-calendar --dry-run --yes
python sync_xibo.py --upload-calendar-html --dry-run --yes
```

Only run non-dry-run commands after the CMS, OAuth applications, player authorization, and display group settings have been verified.

## Future Setup Step

A future setup step should add a boot-time script that automatically launches a hotspot on the Debian machine after startup. That script should be documented here once implemented, including the selected network manager, SSID/security settings, systemd unit, and validation commands.
