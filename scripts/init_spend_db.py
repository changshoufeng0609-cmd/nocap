#!/usr/bin/env python3
"""Print PostgreSQL ledger schema; --apply performs explicitly approved initialization.

Do not run --apply until the owner authorizes writes to the selected database.
This script creates tables in an existing database, never a cloud resource.
"""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receipts.spend import POSTGRES_SCHEMA, SCHEMA_STATEMENTS, PostgresLedger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="initialize the existing database only after owner approval")
    args = parser.parse_args()
    if not args.apply:
        print(POSTGRES_SCHEMA, end="")
        return 0
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        database_url = os.environ.get("RECEIPTS_SPEND_DATABASE_URL", "")
        # Configuration-only validation; no connection or writes in the constructor.
        PostgresLedger(0, database_url)
        driver = importlib.import_module("psycopg")
        with driver.connect(database_url, connect_timeout=5, sslmode="require") as connection:
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute("SET LOCAL statement_timeout = '10s'")
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
        print("Spend-ledger schema initialized. No model calls were made.")
        return 0
    except Exception:
        print("Spend-ledger initialization failed. Check database configuration and access privately.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
