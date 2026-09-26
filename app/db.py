"""SQLite access and schema management.

SQLite, not Postgres, and deliberately: the whole dataset is one workbook of a
few tens of thousands of rows, it has no concurrent writers, and a file-backed
database means the project clones and runs with no service to install. Every
query here is ordinary SQL - window functions included - so moving to Postgres
is a connection-string change, not a rewrite.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import settings

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def connect(path: str | None = None) -> sqlite3.Connection:
    database = Path(path or settings.database_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def apply_script(connection: sqlite3.Connection, name: str) -> None:
    connection.executescript((SQL_DIR / name).read_text(encoding="utf-8"))
    connection.commit()


def initialise(connection: sqlite3.Connection) -> None:
    """Create tables, then views. Order matters - views read the tables."""
    apply_script(connection, "schema.sql")
    apply_script(connection, "metrics.sql")


def query(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]
