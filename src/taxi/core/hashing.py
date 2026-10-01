"""Content addressing.

Two distinct notions are kept separate on purpose:

* `file_sha256`  - bytes on disk. Detects a changed upstream source file.
* fingerprints   - order-independent hash of a *row set*, computed in SQL
                   (see taxi.core.duck.fingerprint_query). Proves two pipeline
                   runs produced the same data, even if Parquet encoding,
                   thread count or row-group boundaries differ.

Byte equality of output Parquet is *not* the determinism guarantee: it is
recorded as a secondary observation because it depends on the writer version.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def object_sha256(obj: Any) -> str:
    """Stable hash of a JSON-serialisable object (sorted keys, no whitespace drift)."""
    return text_sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str))


def code_version(root: Path | None = None) -> str:
    """Git describe if available, else a hash of the package source tree.

    Recorded on every run so a partition can always be traced to the code that
    produced it, including in a tarball with no .git directory.
    """
    root = Path(root or Path(__file__).resolve().parents[3])
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "describe", "--always", "--dirty", "--tags"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"git:{result.stdout.strip()}"
    except (OSError, subprocess.SubprocessError):
        pass

    src = root / "src" / "taxi"
    digest = hashlib.sha256()
    for path in sorted(src.rglob("*")):
        if path.suffix in {".py", ".sql"} and path.is_file():
            digest.update(path.relative_to(src).as_posix().encode())
            digest.update(path.read_bytes())
    return f"src:{digest.hexdigest()[:16]}"
