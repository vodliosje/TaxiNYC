"""Run recording.

Every operation that touches data opens a `RunRecorder`. It stamps a run id,
captures the environment, times the work, and writes exactly one
`pipeline_runs` row - including when the run fails, which is the case that
matters when you are asked "what produced this partition?".
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from taxi.core import duck
from taxi.core.hashing import code_version
from taxi.core.logging import get_logger
from taxi.metadata.store import MetadataStore, as_json, environment, join_keys, new_run_id

log = get_logger(__name__)


@dataclass
class RunRecorder:
    """Context manager that produces one pipeline_runs row."""

    store: MetadataStore
    run_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    contract_fingerprint: str = ""

    run_id: str = ""
    rows_read: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    source_partitions: list[str] = field(default_factory=list)
    partitions_written: list[str] = field(default_factory=list)
    downstream_assets_rebuilt: list[str] = field(default_factory=list)
    final_status: str = "SUCCESS"
    error_message: str = ""

    _start: float = 0.0
    _start_time: datetime | None = None

    def __enter__(self) -> "RunRecorder":
        self.run_id = self.run_id or new_run_id(self.run_type)
        self._start = time.perf_counter()
        self._start_time = datetime.now()
        log.info("run %s (%s) started", self.run_id, self.run_type)
        return self

    def skip(self, reason: str) -> None:
        self.final_status = "SKIPPED"
        self.error_message = reason

    def fail(self, message: str) -> None:
        self.final_status = "FAILED"
        self.error_message = message

    def add_partition(self, key: str) -> None:
        if key not in self.partitions_written:
            self.partitions_written.append(key)

    def add_asset(self, name: str) -> None:
        if name not in self.downstream_assets_rebuilt:
            self.downstream_assets_rebuilt.append(name)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.final_status = "FAILED"
            self.error_message = f"{exc_type.__name__}: {exc}"
            log.error("run %s failed: %s", self.run_id, self.error_message)
            log.debug("%s", "".join(traceback.format_exception(exc_type, exc, tb)))

        end_time = datetime.now()
        env = environment()
        try:
            duckdb_version = duck.duckdb_version()
        except ModuleNotFoundError:  # pragma: no cover
            duckdb_version = "unavailable"

        self.store.append(
            "pipeline_runs",
            [
                {
                    "run_id": self.run_id,
                    "run_type": self.run_type,
                    "code_version": code_version(self.store.settings.root),
                    "contract_fingerprint": self.contract_fingerprint,
                    "duckdb_version": duckdb_version,
                    "python_version": env["python_version"],
                    "host": env["host"],
                    "start_time": self._start_time,
                    "end_time": end_time,
                    "duration_seconds": round(time.perf_counter() - self._start, 4),
                    "source_partitions": join_keys(self.source_partitions),
                    "rows_read": self.rows_read,
                    "rows_accepted": self.rows_accepted,
                    "rows_rejected": self.rows_rejected,
                    "partitions_written": join_keys(self.partitions_written),
                    "downstream_assets_rebuilt": join_keys(self.downstream_assets_rebuilt),
                    "final_status": self.final_status,
                    "error_message": self.error_message,
                    "parameters": as_json(self.parameters),
                }
            ],
        )
        log.info(
            "run %s %s (%.2fs, read=%s accepted=%s rejected=%s)",
            self.run_id, self.final_status, time.perf_counter() - self._start,
            self.rows_read, self.rows_accepted, self.rows_rejected,
        )
        return False  # never swallow the exception
