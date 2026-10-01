"""One logging configuration for CLI, library and tests."""
from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Iterator

_CONFIGURED = False
FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


def configure(level: str | int | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    resolved = level or os.environ.get("TAXI_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=resolved, format=FORMAT, datefmt="%H:%M:%S", stream=sys.stderr, force=True
    )
    logging.getLogger("py4j").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(name)


@contextmanager
def timed(logger: logging.Logger, label: str, level: int = logging.INFO) -> Iterator[dict]:
    """Time a block and expose the elapsed seconds to the caller.

    Every duration recorded in run metadata comes from here, so timing is
    measured the same way everywhere.
    """
    holder: dict = {"label": label, "seconds": 0.0}
    started = time.perf_counter()
    try:
        yield holder
    finally:
        holder["seconds"] = round(time.perf_counter() - started, 4)
        logger.log(level, "%s finished in %.3fs", label, holder["seconds"])
