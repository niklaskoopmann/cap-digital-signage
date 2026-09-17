"""Run one-shot calendar cycles from a container-local cron schedule."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config, load_config
from .service import setup_logging


JOB_COMMAND = (sys.executable, "-m", "calendar_render_service.service")
FLOCK_COMMAND = shutil.which("flock") or "/usr/bin/flock"
SUPERCRONIC_COMMAND = str(Path(shutil.which("supercronic") or "/usr/local/bin/supercronic").resolve())
LOCK_PATH = "/tmp/calendar-render-service.lock"
CRONTAB_PATH = Path("/tmp/calendar-render-service.crontab")


def build_crontab(cfg: Config) -> str:
    """Build the single cron entry for one render cycle."""
    command = " ".join(JOB_COMMAND)
    return f"{cfg.render_cron} {FLOCK_COMMAND} -n {LOCK_PATH} {command}\n"


def write_crontab(cfg: Config, path: Path = CRONTAB_PATH) -> Path:
    """Write the generated crontab and return its path."""
    path.write_text(build_crontab(cfg), encoding="utf-8")
    return path


def run_startup_cycle(cfg: Config) -> None:
    """Run the optional initial cycle without allowing it to stop cron."""
    if not cfg.run_on_start:
        return
    try:
        result = subprocess.run(list(JOB_COMMAND), check=False)
    except OSError:
        logging.warning("Startup calendar render cycle could not be launched", exc_info=True)
        return
    if result.returncode:
        logging.warning("Startup calendar render cycle failed with return code %d", result.returncode)


def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    crontab_path = write_crontab(cfg)
    run_startup_cycle(cfg)
    logging.info("Calendar scheduler active: cron=%s timezone=%s", cfg.render_cron, os.getenv("TZ", cfg.calendar_timezone))
    os.execv(SUPERCRONIC_COMMAND, [SUPERCRONIC_COMMAND, "-passthrough-logs", str(crontab_path)])


if __name__ == "__main__":
    main()