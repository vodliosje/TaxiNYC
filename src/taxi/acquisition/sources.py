"""Source acquisition.

Raw files are immutable inputs. Acquisition only ever *adds* a file; it never
rewrites one in place, and re-acquiring an existing file is a no-op unless
`force` is passed. Everything downstream reads raw and writes elsewhere, so the
raw layer is always reproducible ground truth.

Two backends, one interface:
  * https     - the real TLC monthly release
  * synthetic - deterministic local generation (see synthetic.py)
"""
from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from taxi.core.hashing import file_sha256
from taxi.core.logging import get_logger, timed
from taxi.core.months import Month

log = get_logger(__name__)

USER_AGENT = "nyc-taxi-platform/0.1 (+local analytics build)"
SYNTHETIC_LOCATION = "synthetic://generated"


@dataclass
class SourceFile:
    """A monthly source partition on local disk."""

    month: Month
    path: Path
    location: str
    dataset: str
    acquired: bool               # True if this call fetched/generated it
    file_hash: str = ""
    size_bytes: int = 0
    acquired_at: datetime | None = None
    notes: str = ""

    @property
    def source_id(self) -> str:
        return f"{self.dataset}:{self.month}"

    def exists(self) -> bool:
        return self.path.is_file()

    def stamp(self) -> "SourceFile":
        """Fill in hash/size from the file on disk."""
        if self.exists():
            self.file_hash = file_sha256(self.path)
            self.size_bytes = self.path.stat().st_size
            self.acquired_at = self.acquired_at or datetime.fromtimestamp(
                self.path.stat().st_mtime
            )
        return self


def local_path(settings, month: Month) -> Path:
    return settings.source_dir() / settings.source_filename(month.year, month.month)


def acquire_months(
    settings,
    months: Sequence[Month],
    *,
    synthetic: bool = False,
    rows: int | None = None,
    seed: int = 42,
    force: bool = False,
) -> list[SourceFile]:
    """Ensure each month's source file exists locally. Returns one record per month."""
    results: list[SourceFile] = []
    for month in months:
        target = local_path(settings, month)
        dataset = settings.dataset.get("name", "dataset")

        if target.is_file() and not force:
            log.info("%s already present (%s)", month, target.name)
            results.append(
                SourceFile(
                    month=month, path=target, dataset=dataset, acquired=False,
                    location=SYNTHETIC_LOCATION if synthetic
                    else settings.source_url(month.year, month.month),
                    notes="already present",
                ).stamp()
            )
            continue

        if synthetic:
            results.append(_generate(settings, month, target, dataset, rows, seed))
        else:
            results.append(_download(settings, month, target, dataset))
    return results


def _generate(settings, month: Month, target: Path, dataset: str, rows, seed) -> SourceFile:
    from taxi.acquisition.synthetic import SyntheticSpec, write_month

    spec = SyntheticSpec(month=month, rows=int(rows or 250_000), seed=seed)
    with timed(log, f"generate {month}"):
        info = write_month(settings, spec, target)
    return SourceFile(
        month=month, path=target, location=SYNTHETIC_LOCATION, dataset=dataset,
        acquired=True, acquired_at=info["generated_at"],
        notes=f"synthetic rows={info['rows']} seed={spec.resolved_seed()}",
    ).stamp()


def _download(settings, month: Month, target: Path, dataset: str) -> SourceFile:
    url = settings.source_url(month.year, month.month)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings.paths.tmp / f".{target.name}.{os.getpid()}.part"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s -> %s", url, target.name)
    try:
        with timed(log, f"download {month}"):
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
        os.replace(tmp, target)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"could not acquire {month} from {url}: {exc}. "
            "Use --synthetic to generate a local stand-in month instead."
        ) from exc

    return SourceFile(
        month=month, path=target, location=url, dataset=dataset,
        acquired=True, acquired_at=datetime.now(), notes="downloaded",
    ).stamp()


def acquire_reference(settings, *, synthetic: bool = False, force: bool = False) -> list[dict[str, Any]]:
    """Fetch the reference tables (taxi zone lookup) declared in settings."""
    out: list[dict[str, Any]] = []
    for ref in settings.dataset.get("reference_sources", []):
        target = settings.paths.reference / ref["filename"]
        if target.is_file() and not force:
            out.append({"name": ref["name"], "path": target, "acquired": False})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if synthetic:
            from taxi.acquisition.synthetic import write_zone_lookup

            rows = write_zone_lookup(target)
            out.append({"name": ref["name"], "path": target, "acquired": True, "rows": rows,
                        "location": SYNTHETIC_LOCATION})
            continue
        try:
            request = urllib.request.Request(ref["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                target.write_bytes(response.read())
            out.append({"name": ref["name"], "path": target, "acquired": True,
                        "location": ref["url"]})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"could not acquire reference '{ref['name']}' from {ref['url']}: {exc}. "
                "Use --synthetic for a local stand-in."
            ) from exc
    return out


def reference_path(settings, name: str = "zones") -> Path | None:
    for ref in settings.dataset.get("reference_sources", []):
        if ref["name"] == name:
            path = settings.paths.reference / ref["filename"]
            return path if path.is_file() else None
    return None


def reference_relation(settings, name: str = "zones") -> str | None:
    """SQL snippet reading a reference table, or None when it has not been acquired."""
    path = reference_path(settings, name)
    if path is None:
        return None
    if path.suffix.lower() == ".csv":
        return f"read_csv('{path.as_posix()}', header = true, auto_detect = true)"
    return f"read_parquet('{path.as_posix()}')"
