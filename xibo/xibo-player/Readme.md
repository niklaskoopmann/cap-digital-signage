Unofficial open source player:
https://github.com/xiboplayer/xiboplayer
https://www.xiboplayer.org/players/pwa

## Electron

> Note: does not work offline.

https://www.xiboplayer.org/players/electron
https://github.com/xiboplayer/xiboplayer-electron

### Install and run on Linux Ubuntu

1. Install or unpack the Electron player on the target machine.
2. Start it from a shell with:

```bash
xiboplayer
```

3. Review or edit the player configuration here:

```text
/home/digitalsignage/.config/xiboplayer/electron
```

### Shortcuts

Player shortcuts (disabled by default — enable via controls in config.json):
Key	Requires	Action
T	controls.keyboard.debugOverlays: true	Toggle timeline overlay
D	controls.keyboard.debugOverlays: true	Toggle download overlay
V	controls.keyboard.videoControls: true	Toggle video controls
→ / PageDown	controls.keyboard.playbackControl: true	Skip to next layout
← / PageUp	controls.keyboard.playbackControl: true	Go to previous layout
Space	controls.keyboard.playbackControl: true	Pause / resume playback
R	controls.keyboard.playbackControl: true	Revert to scheduled layout

## Arexibo

https://www.xiboplayer.org/players/arexibo

Arexibo is the open source player wrapper for kiosk-style deployments, but it is not a good fit for offline-only machines based on the current notes in this repository.

Start by pasting in shell:

```bash
arexibo /home/Documents/arexibo/
```

## Installation

The upstream project links above are the source of truth for downloads and release notes. Keep this file as a short local reminder for the repository.

