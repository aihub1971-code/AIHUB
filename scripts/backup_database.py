"""Create an application-consistent SQLite backup without exposing report contents."""
from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New backup filename; must not exist.")
    args = parser.parse_args()
    source = Path(os.environ.get("AI_HUB_DATABASE_PATH", str(PROJECT_ROOT / "data/ai_hub.sqlite3")))
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    source = source.resolve()
    target = args.output.resolve()
    if not source.is_file():
        parser.error("The configured database does not exist; start AI HUB first.")
    if target == source or target.exists():
        parser.error("Choose a new backup path; existing files are never overwritten.")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="ai-hub-backup-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as current:
            with closing(sqlite3.connect(temporary)) as backup:
                current.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("The backup failed the SQLite integrity check.")
        # Hard-link publication refuses to replace a destination created meanwhile.
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"SQLite backup created and verified: {target}")
    print("Copy this backup off the service disk to your private backup destination.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
