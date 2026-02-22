from __future__ import annotations
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from google.cloud import secretmanager


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SECRETS_DIR = _REPO_ROOT / "secrets"


def setup_secrets(env: str) -> dict[str, Path]:
    """
    Given env-provided secrets (ENV_FILE, SERVICE_ACCOUNT_KEY), write them to disk.
    Returns a mapping of the env var names to the file paths that were used.
    """
    _SECRETS_DIR.mkdir(parents=True, exist_ok=True)

    secret_files_path: dict[str, Path] = {
        "ENV_FILE": _SECRETS_DIR / f"env.{env}",
    }
    if env == "prod":
        secret_files_path["SERVICE_ACCOUNT_KEY"] = _SECRETS_DIR / "the-list-webapp-prod-sa.json"
    elif env == "dev":
        secret_files_path["SERVICE_ACCOUNT_KEY"] = _SECRETS_DIR / "the-list-webapp-dev-sa.json"

    for env_var, file_path in secret_files_path.items():
        value = os.environ.get(env_var)
        if not value:
            continue
        if not file_path.exists():
            file_path.write_text(value)
        else:
            print(f"File {file_path} already exists, skipping")
        if env_var == "SERVICE_ACCOUNT_KEY":
            # Set GOOGLE_APPLICATION_CREDENTIALS to point to the service account key file, if not it should be in the env.dev
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(file_path)
        


@lru_cache(maxsize=1)
def _sm_client() -> secretmanager.SecretManagerServiceClient:
    return secretmanager.SecretManagerServiceClient()

@lru_cache(maxsize=256)
def _sm_get(resource: str) -> str:
    """Retrieve a secret value from Google Cloud Secret Manager."""
    resp = _sm_client().access_secret_version(name=resource)
    return resp.payload.data.decode("utf-8")

def get_secret(name: str, default: Optional[str] = None) -> str:
    """
    Resolution order:
      1) NAME (env/.env)
      2) NAME_RESOURCE (Secret Manager resource path)
      3) else raise RuntimeError
    """
    if (v := os.getenv(name)) is not None:
        return v
    if (r := os.getenv(f"{name}_RESOURCE")):
        return _sm_get(r)
    if default is not None:
        return default
    raise RuntimeError(f"Missing {name} (or {name}_RESOURCE)")
