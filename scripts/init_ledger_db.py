"""Bootstrap the ledger database using Alembic migrations."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    db_url = os.getenv("LEDGER_DATABASE_URL")
    if not db_url:
        raise SystemExit("LEDGER_DATABASE_URL must be set before running this script")

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "head")
    print("Ledger database migrated to head")


if __name__ == "__main__":
    main()
