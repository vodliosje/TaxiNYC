"""Append-only metadata store.

One generic writer for every table in `schemas.TABLES`. Each table is a Parquet
file plus a CSV mirror; both are rewritten atomically on every append so a
crashed run cannot leave a torn ledger.

Why Parquet + CSV and not SQLite: the metadata has to be readable by the same
engine that reads the data (DuckDB), diffable in git-adjacent workflows, and
pasteable into docs as evidence. CSV is the mirror, Parquet is the source.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from taxi.core import duck
from taxi.core.logging import get_logger
from taxi.metadata.schemas import TABLES, column_names, ddl

log = get_logger(__name__)


def new_run_id(run_type: str, now: datetime | None = None) -> str:
    """Sortable, human-readable, collision-safe."""
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{run_type}-{uuid.uuid4().hex[:8]}"


def environment() -> dict[str, str]:
    return {
        "python_version": platform.python_version(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "cpu_count": str(os.cpu_count() or 0),
        "executable": sys.executable,
    }


def as_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def join_keys(items: Iterable[Any]) -> str:
    return ",".join(str(item) for item in items)


class MetadataStore:
    """Reader/writer for the metadata ledgers."""

    def __init__(self, settings):
        self.settings = settings
        self.dir = settings.paths.metadata

    # -- locations -------------------------------------------------------------
    def path(self, table: str, suffix: str = "parquet") -> Path:
        return self.dir / f"{table}.{suffix}"

    def exists(self, table: str) -> bool:
        return self.path(table).is_file()

    # -- writing ---------------------------------------------------------------
    def append(self, table: str, rows: Sequence[dict[str, Any]]) -> int:
        """Append rows, preserving history. Returns the new total row count."""
        if table not in TABLES:
            raise KeyError(f"unknown metadata table '{table}'")
        if not rows:
            return self.count(table)

        self.dir.mkdir(parents=True, exist_ok=True)
        columns = column_names(table)
        target = self.path(table)

        with duck.connect(self.settings) as con:
            con.execute(ddl(table))
            if target.is_file():
                # BY NAME tolerates a ledger written before a column was added.
                con.execute(
                    f'INSERT INTO "{table}" BY NAME '
                    f"SELECT * FROM read_parquet('{target.as_posix()}')"
                )
            placeholders = ", ".join("?" for _ in columns)
            payload = [[_coerce(row.get(name)) for name in columns] for row in rows]
            con.executemany(
                f'INSERT INTO "{table}" VALUES ({placeholders})', payload
            )
            duck.copy_to_parquet(
                con, f'SELECT * FROM "{table}"', target, self.settings
            )
            csv_path = self.path(table, "csv")
            tmp_csv = self.settings.paths.tmp / f".{table}.{os.getpid()}.csv"
            tmp_csv.parent.mkdir(parents=True, exist_ok=True)
            con.execute(
                f"COPY (SELECT * FROM \"{table}\") TO '{tmp_csv.as_posix()}' (HEADER, DELIMITER ',')"
            )
            os.replace(tmp_csv, csv_path)
            total = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]

        log.debug("metadata: appended %d row(s) to %s (total %d)", len(rows), table, total)
        return int(total)

    # -- reading ---------------------------------------------------------------
    def relation(self, table: str) -> str | None:
        """SQL snippet for reading this ledger, or None if it does not exist yet."""
        target = self.path(table)
        return duck.scan(target, hive=False) if target.is_file() else None

    def count(self, table: str) -> int:
        if not self.exists(table):
            return 0
        with duck.connect(self.settings, read_only=False) as con:
            return int(
                con.execute(f"SELECT count(*) FROM {self.relation(table)}").fetchone()[0]
            )

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Run SQL with every existing ledger registered as a view of its name."""
        with duck.connect(self.settings) as con:
            for table in TABLES:
                relation = self.relation(table)
                if relation:
                    con.execute(f'CREATE OR REPLACE VIEW "{table}" AS SELECT * FROM {relation}')
                else:
                    columns = ", ".join(
                        f'CAST(NULL AS {dtype}) AS "{name}"'
                        for name, dtype in TABLES[table].items()
                    )
                    con.execute(
                        f'CREATE OR REPLACE VIEW "{table}" AS '
                        f"SELECT {columns} WHERE FALSE"
                    )
            cursor = con.execute(sql)
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def latest_per(self, table: str, key: str) -> list[dict[str, Any]]:
        """Most recent row per key - the 'current state' view over an append-only log."""
        order = "ingestion_time" if table == "source_manifest" else "run_id"
        return self.query(
            f"""
            SELECT * EXCLUDE (__rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY "{key}" ORDER BY "{order}" DESC NULLS LAST
                ) AS __rn
                FROM "{table}"
            ) WHERE __rn = 1
            ORDER BY "{key}"
            """
        )


def _coerce(value: Any) -> Any:
    """Lists/dicts become JSON so a ledger column is always a scalar."""
    if isinstance(value, (list, dict, tuple, set)):
        return as_json(list(value) if isinstance(value, (tuple, set)) else value)
    if isinstance(value, Path):
        return str(value)
    return value
