from __future__ import annotations

import os
import shutil
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
LEGACY_PEOPLE_DB_PATH = SRC_DIR / "pages" / "people" / "people_cards.db"
MOVED_PEOPLE_DB_PATH = SRC_DIR / "pages" / "people_display" / "people_cards.db"


def resolve_people_local_db_path() -> Path:
    explicit_path = os.getenv("THE_LIST_LOCAL_DB_PATH", "").strip()
    if explicit_path:
        return Path(explicit_path).expanduser()

    explicit_dir = os.getenv("THE_LIST_LOCAL_DB_DIR", "").strip()
    if explicit_dir:
        return Path(explicit_dir).expanduser() / "people_cards.db"

    return PROJECT_ROOT / "bases_de_datos" / "people_cards.db"


def ensure_people_local_db_path(target_path: Path | None = None) -> Path:
    path = (target_path or resolve_people_local_db_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        legacy_candidates = [LEGACY_PEOPLE_DB_PATH, MOVED_PEOPLE_DB_PATH]
        for candidate in legacy_candidates:
            legacy_path = candidate.expanduser()
            if path.exists():
                break
            if not legacy_path.exists():
                continue
            if path.resolve() == legacy_path.resolve():
                continue
            shutil.copy2(legacy_path, path)
            break
    except OSError:
        pass
    return path
