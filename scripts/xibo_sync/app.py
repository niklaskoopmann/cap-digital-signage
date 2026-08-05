"""Application orchestration for the modular Xibo sync workflow."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv
from rich.prompt import Confirm

from .client import XiboClient
from .config import load_config
from .env_io import read_env_file, write_env_file
from .media import build_local_index, build_remote_index, list_local_media
from .ui import console, ui_error, ui_header, ui_info, ui_ok, ui_settings_table, ui_warn
from .wizard import config_wizard


def setup_logging(level: str, log_file: str | None, scripts_dir: Path) -> None:
    """Configure console and optional file logging for a sync run.

    Args:
        level: Logging level name such as ``INFO`` or ``DEBUG``.
        log_file: Relative or absolute file path for optional file logging.
        scripts_dir: Base directory used to resolve relative log paths.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        lf_path = (scripts_dir / log_file).resolve() if not Path(log_file).is_absolute() else Path(log_file)
        lf_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(lf_path, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


def main() -> int:
    """Run the sync CLI, including config prompts, diffing, and actions.

    Returns:
        Process exit code. ``0`` indicates success and ``2`
        ` indicates failure.
    """
    scripts_dir = Path(__file__).resolve().parent.parent
    env_path = scripts_dir / ".env"

    parser = argparse.ArgumentParser(description="Sync local media to Xibo CMS")
    parser.add_argument("--config", action="store_true", help="Run configuration wizard and exit")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive: accept defaults and skip prompts")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without uploading/deleting")
    parser.add_argument("--delete", dest="delete", action="store_true", help="Delete remote-only media without prompt")
    parser.add_argument("--no-delete", dest="delete", action="store_false", help="Do not delete remote-only media")
    parser.set_defaults(delete=None)
    args = parser.parse_args()

    ui_header("Xibo Media Sync", "Sync local media to Xibo CMS (assumes you are already on the hotspot)")

    env_data = read_env_file(env_path)
    if env_data:
        ui_settings_table(env_data)
    else:
        ui_warn("No .env found yet. You should run the configuration wizard now.")

    console.print()
    if args.config:
        config_wizard(env_path)
        return 0

    override = False if args.yes else Confirm.ask(
        "[bold]Do you want to override/edit configuration values now?[/bold]",
        default=(not env_data),
    )
    if override:
        config_wizard(env_path)
        env_data = read_env_file(env_path)

    load_dotenv(env_path)
    cfg = load_config()

    setup_logging(cfg.log_level, cfg.log_file, scripts_dir)

    if not cfg.local_media_dir.is_absolute():
        cfg.local_media_dir = (scripts_dir / cfg.local_media_dir).resolve()

    console.print()
    ui_header("Run Options", "Choose whether remote-only media should be deleted this run")
    if args.delete is not None:
        run_delete = bool(args.delete)
    elif args.yes:
        run_delete = cfg.delete_remote_not_local
    else:
        run_delete = Confirm.ask(
            "Delete media from Xibo that is NOT present locally?",
            default=cfg.delete_remote_not_local,
        )

    env_data2 = read_env_file(env_path)
    env_data2["DELETE_REMOTE_NOT_LOCAL"] = "true" if run_delete else "false"
    write_env_file(env_path, env_data2)
    ui_ok("Saved deletion choice to .env (DELETE_REMOTE_NOT_LOCAL)")

    if args.dry_run:
        cfg.dry_run = True

    ui_info(f"Local media dir: {cfg.local_media_dir}")
    ui_info(f"Compare mode: {cfg.compare_mode} | Auth mode: {cfg.auth_mode} | Dry run: {cfg.dry_run}")

    changes_made = False

    try:
        ui_info("Step 1: Checking Xibo API availability ...")
        xibo = XiboClient(cfg.cms_base_url, cfg.cms_verify_tls, cfg.cms_timeout)

        if cfg.auth_mode == "oauth":
            assert cfg.cms_client_id and cfg.cms_client_secret
            xibo.authenticate_oauth(cfg.cms_client_id, cfg.cms_client_secret)

        xibo.health_check()
        ui_ok("Xibo API reachable")

        ui_info("Step 2: Scanning local media files ...")
        local_files = list_local_media(cfg.local_media_dir, cfg.media_extensions)
        ui_ok(f"Found {len(local_files)} local media file(s)")
        local_index = build_local_index(local_files, cfg.compare_mode, cfg.hash_tag_prefix)

        ui_info("Step 3: Fetching Xibo CMS library ...")
        remote_items = xibo.list_library(cfg.managed_tag, cfg.managed_folder_id)
        remote_index = build_remote_index(remote_items, cfg.compare_mode, cfg.hash_tag_prefix)
        ui_ok(f"Fetched {len(remote_items)} remote library item(s) (filtered)")

        ui_info("Step 4: Calculating diff ...")
        to_upload = sorted(set(local_index.keys()) - set(remote_index.keys()))
        to_delete = sorted(set(remote_index.keys()) - set(local_index.keys()))
        ui_ok(f"To upload: {len(to_upload)} | Remote-only: {len(to_delete)}")

        if cfg.upload_new_local and to_upload:
            ui_info("Step 5: Uploading new media ...")
            for key in to_upload:
                f = local_index[key]
                tags = [cfg.managed_tag]
                if cfg.compare_mode == "hash":
                    tags.append(key)

                xibo.upload_media(
                    file_path=f,
                    name=f.name,
                    folder_id=cfg.managed_folder_id,
                    tags=tags,
                    preferred_field=cfg.xibo_upload_field,
                    dry_run=cfg.dry_run,
                )
                changes_made = True
            ui_ok("Uploads complete")
        else:
            ui_info("Step 5: Upload skipped (nothing to upload or UPLOAD_NEW_LOCAL=false)")

        if run_delete and to_delete:
            ui_info("Step 6: Deleting remote-only media ...")

            if cfg.only_delete_managed_tag:
                ui_warn(f"Safety ON: Only deleting items with MANAGED_TAG='{cfg.managed_tag}'")

            for key in to_delete:
                it = remote_index[key]
                media_id = str(it.get("mediaId") or it.get("id") or "")
                if not media_id:
                    logging.warning("Skipping delete (no mediaId): %s", it)
                    continue

                if cfg.only_delete_managed_tag:
                    tags = it.get("tags", "")
                    tags_str = ""
                    if isinstance(tags, str):
                        tags_str = tags
                    elif isinstance(tags, list):
                        tags_str = ",".join([t.get("tag", "") if isinstance(t, dict) else str(t) for t in tags])

                    if tags_str and cfg.managed_tag not in tags_str:
                        ui_warn(f"Skipping delete mediaId={media_id} (missing managed tag)")
                        continue

                    if not tags_str:
                        logging.warning(
                            "Deleting mediaId=%s without embedded tags because the library query was already restricted by MANAGED_TAG.",
                            media_id,
                        )

                xibo.delete_media(media_id, dry_run=cfg.dry_run)
                changes_made = True

            ui_ok("Deletion complete")
        else:
            ui_info("Step 6: Delete skipped (disabled or none to delete)")

        if cfg.trigger_collectnow_on_changes and changes_made and cfg.display_group_id:
            ui_info("Step 7: Triggering player refresh (Collect Now) ...")
            xibo.collect_now(cfg.display_group_id, dry_run=cfg.dry_run)
            ui_ok("Collect Now triggered")
        else:
            ui_info("Step 7: Refresh skipped (no changes, DISPLAY_GROUP_ID empty, or disabled)")

        ui_ok("SUCCESS: Sync completed")
        return 0

    except Exception as e:
        logging.exception("FAILED: %s", e)
        ui_error(f"Sync failed: {e}")
        return 2
