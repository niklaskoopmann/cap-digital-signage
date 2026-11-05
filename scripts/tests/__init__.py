"""Shared isolated system-test configuration helpers."""

import json
import os
from pathlib import Path

from xibo_sync.env_io import (
	CREDENTIAL_KEYS, CMS_CREDENTIAL_KEYS, atomic_write, dotenv_assignments,
	read_json_file, write_json_file,
)


def load_system_settings(path: Path) -> dict[str, str]:
	root = Path(__file__).resolve().parents[2]
	forbidden = {root / "scripts" / ".env", root / "services" / "calendar_render_service" / ".env"}
	secret_path = Path(os.environ.get("SYSTEM_TEST_SECRETS_FILE", str(path.parent / "secrets.json"))).resolve()
	forbidden_secrets = {location.parent / "secrets.json" for location in forbidden}
	if path.resolve() in forbidden or secret_path in forbidden_secrets:
		raise RuntimeError("System tests require isolated non-production configuration and credentials")
	values, _ = dotenv_assignments(path)
	if CREDENTIAL_KEYS.intersection(values):
		raise RuntimeError("Move test credentials out of the test dotenv file into its secrets.json")
	values.update(read_json_file(secret_path, CMS_CREDENTIAL_KEYS))
	values.update({key: os.environ[key] for key in CMS_CREDENTIAL_KEYS if key in os.environ})
	return values


def write_system_settings(path: Path, values: dict[str, str]) -> None:
	secrets = {key: value for key, value in values.items() if key in CREDENTIAL_KEYS}
	atomic_write(path, "".join(f"{key}={json.dumps(value, ensure_ascii=False)}\n"
							  for key, value in values.items() if key not in CREDENTIAL_KEYS))
	write_json_file(path.parent / "secrets.json", secrets, CREDENTIAL_KEYS)
