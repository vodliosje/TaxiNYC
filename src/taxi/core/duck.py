"""DuckDB access layer.

Everything that touches the engine goes through here so that:
  * pragmas (threads, memory, temp dir) are identical in every process,
  * Parquet writes are atomic and consistently encoded,
  * row-set fingerprints are computed one way only.

`duckdb` is imported lazily so the pure-logic modules (contract, months,
dependency graph) stay importable and testable without the engine installed.
"""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from taxi.core.logging import get_logger

log = get_logger(__name__)

NULL_SENTINEL = "\\x00"


def _duckdb():
    try:
        import duckdb  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise ModuleNotFoundError(
            "duckdb is required for this operation. Install with: pip install -e ."
        ) from exc
    return duckdb


def duckdb_version() -> str:
    return _duckdb().__version__


@contextmanager
def connect(settings, read_only: bool = False, database: str = ":memory:") -> Iterator[Any]:
    """Configured DuckDB connection. Pragmas come from settings.engine."""
    duckdb = _duckdb()
    engine = settings.engine
    con = duckdb.connect(database=database, read_only=read_only)
    try:
        tmp = Path(settings.root / engine.get("temp_directory", "data/_tmp"))
        tmp.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET threads TO {int(engine.get('threads', 4))}")
        con.execute(f"SET memory_limit = '{engine.get('memory_limit', '6GB')}'")
        con.execute(f"SET temp_directory = '{tmp.as_posix()}'")
        con.execute(
            "SET preserve_insertion_order = "
            f"{str(bool(engine.get('preserve_insertion_order', False))).lower()}"
        )
        yield con
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def scan(path: str | Path, hive: bool = True, union_by_name: bool = True) -> str:
    """SQL snippet reading a Parquet path or glob.

    `union_by_name` matters for real TLC data: column order and presence drift
    between monthly releases.
    """
    opts = [f"'{Path(path).as_posix()}'"]
    if hive:
        opts.append("hive_partitioning = true")
    if union_by_name:
        opts.append("union_by_name = true")
    return f"read_parquet({', '.join(opts)})"


def describe(con, relation_sql: str) -> list[tuple[str, str]]:
    """[(column_name, duckdb_type)] for any relation, without scanning rows."""
    rows = con.execute(f"DESCRIBE SELECT * FROM {relation_sql} LIMIT 0").fetchall()
    return [(row[0], row[1]) for row in rows]


def table_exists(path: Path) -> bool:
    if path.is_file():
        return True
    return path.is_dir() and any(path.rglob("*.parquet"))


# --------------------------------------------------------------------------- #
# Fingerprints
# --------------------------------------------------------------------------- #
def row_fingerprint_expr(columns: Sequence[str], alias: str | None = None) -> str:
    """Per-row hash expression over an explicit column list.

    NULLs get an explicit sentinel so NULL and '' cannot collide. Building the
    expression from a column list (rather than casting the whole row) keeps the
    hash stable against column *order* changes and works on every DuckDB build.
    """
    prefix = f"{alias}." if alias else ""
    parts = [f"coalesce(CAST({prefix}{c} AS VARCHAR), '{NULL_SENTINEL}')" for c in columns]
    return f"hash(concat_ws('\\x1f', {', '.join(parts)}))"


def fingerprint(con, relation_sql: str, columns: Sequence[str]) -> dict[str, Any]:
    """Order-independent fingerprint of a row set.

    Returns row_count plus two commutative aggregates. XOR alone cannot see a
    duplicated pair (it cancels); the sum can. Together with the count, the
    triple is a strong equality check that does not depend on row order,
    file layout, or thread count.

    Comparable within one DuckDB version, which every run records.
    """
    expr = row_fingerprint_expr(columns, alias="t")
    row = con.execute(
        f"""
        SELECT count(*)                                   AS row_count,
               coalesce(bit_xor({expr}), 0)::UBIGINT      AS xor_hash,
               coalesce(sum(({expr})::HUGEINT), 0)::HUGEINT AS sum_hash
        FROM {relation_sql} AS t
        """
    ).fetchone()
    row_count, xor_hash, sum_hash = int(row[0]), int(row[1]), int(row[2])
    return {
        "row_count": row_count,
        "fingerprint": f"{row_count:d}:{xor_hash & 0xFFFFFFFFFFFFFFFF:016x}:"
                       f"{sum_hash & ((1 << 128) - 1):032x}",
    }


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def copy_to_parquet(
    con,
    select_sql: str,
    target: Path,
    settings,
    order_by: Sequence[str] | None = None,
    tmp_dir: Path | None = None,
) -> Path:
    """Write a query result to one Parquet file, atomically.

    A partition is a single file, written to a temp location and moved into
    place with os.replace. Readers therefore never observe a half-written
    partition, and a failed run leaves the previous partition intact.

    Deterministic output requires an explicit ORDER BY: DuckDB is free to
    return rows in any order otherwise.
    """
    storage = settings.storage
    tmp_root = Path(tmp_dir or settings.paths.tmp)
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_root / f".{target.stem}.{os.getpid()}.tmp.parquet"

    ordered = select_sql
    if order_by:
        ordered = f"SELECT * FROM ({select_sql}) ORDER BY {', '.join(order_by)}"

    con.execute(
        f"""
        COPY ({ordered}) TO '{tmp_path.as_posix()}'
        (FORMAT PARQUET,
         COMPRESSION {storage.get('compression', 'zstd').upper()},
         ROW_GROUP_SIZE {int(storage.get('row_group_size', 122880))})
        """
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_path, target)
    # Drop stale files from an earlier layout so the partition holds exactly
    # what this run wrote. Scoped to this target's own name so sibling files
    # in a shared directory (e.g. other metadata ledgers) are left alone.
    for stale in target.parent.glob(f"{target.stem}*.parquet"):
        if stale != target:
            stale.unlink()
    return target


def drop_partition(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
