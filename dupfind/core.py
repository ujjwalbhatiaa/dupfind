"""Core duplicate-detection logic for dupfind.

Pure standard library. The scan is two-phase on purpose:

1. Walk the given roots and bucket every regular file by its size.
2. Only files whose size bucket has more than one entry get hashed
   (SHA-256, streamed in chunks). Files sharing both size and hash are
   duplicates.

Unreadable files and directories are recorded as warnings and skipped —
the scan never crashes on a permission error.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

CHUNK_SIZE = 1024 * 1024  # 1 MiB reads while hashing


@dataclass
class DuplicateGroup:
    """One set of files with identical content."""

    sha256: str
    size: int
    paths: list[Path]

    @property
    def duplicate_count(self) -> int:
        """Files beyond the first copy."""
        return max(0, len(self.paths) - 1)

    @property
    def bytes_reclaimable(self) -> int:
        """Bytes freed by deleting every copy except the first."""
        return self.duplicate_count * self.size


@dataclass
class ScanResult:
    files_scanned: int
    groups: list[DuplicateGroup] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duplicate_files(self) -> int:
        return sum(g.duplicate_count for g in self.groups)

    @property
    def bytes_reclaimable(self) -> int:
        return sum(g.bytes_reclaimable for g in self.groups)

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "duplicate_groups": [
                {
                    "sha256": g.sha256,
                    "size": g.size,
                    "files": [str(p) for p in g.paths],
                }
                for g in self.groups
            ],
            "duplicate_files": self.duplicate_files,
            "bytes_reclaimable": self.bytes_reclaimable,
            "warnings": self.warnings,
        }


def hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file, streamed so big files stay cheap."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _is_excluded(path: Path, patterns: list[str]) -> bool:
    """True when a glob pattern matches the file name or any path part."""
    name = path.name
    parts = path.parts
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(str(path), pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def _iter_files(
    roots: list[Path],
    *,
    min_size: int,
    exclude: list[str],
    follow_symlinks: bool,
    warnings: list[str],
):
    """Yield candidate files under the roots, honouring size/exclude rules."""
    for root in roots:
        if not root.exists():
            warnings.append(f"path does not exist: {root}")
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for dirpath, dirnames, filenames in os.walk(
                root, followlinks=follow_symlinks
            ):
                current = Path(dirpath)
                # Prune excluded directories so we never descend into them.
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not _is_excluded(current / d, exclude)
                ]
                for name in filenames:
                    candidates.append(current / name)
        for path in candidates:
            try:
                if path.is_symlink() and not follow_symlinks:
                    continue
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except OSError as exc:
                warnings.append(f"cannot access {path}: {exc}")
                continue
            if size < min_size:
                continue
            if _is_excluded(path, exclude):
                continue
            yield path, size


def find_duplicates(
    roots: list[Path],
    *,
    min_size: int = 0,
    exclude: list[str] | None = None,
    follow_symlinks: bool = False,
) -> ScanResult:
    """Scan roots and return groups of files with identical content.

    Files are grouped by size first; only same-size candidates are hashed.
    Groups are sorted by reclaimable bytes (largest first), and the paths
    inside each group are sorted so the kept copy is deterministic.
    """
    exclude = list(exclude or [])
    warnings: list[str] = []

    by_size: dict[int, list[Path]] = {}
    scanned = 0
    for path, size in _iter_files(
        roots,
        min_size=min_size,
        exclude=exclude,
        follow_symlinks=follow_symlinks,
        warnings=warnings,
    ):
        scanned += 1
        by_size.setdefault(size, []).append(path)

    groups: list[DuplicateGroup] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash: dict[str, list[Path]] = {}
        for path in paths:
            try:
                digest = hash_file(path)
            except OSError as exc:
                warnings.append(f"cannot read {path}: {exc}")
                continue
            by_hash.setdefault(digest, []).append(path)
        for digest, dup_paths in by_hash.items():
            if len(dup_paths) > 1:
                groups.append(
                    DuplicateGroup(
                        sha256=digest, size=size, paths=sorted(dup_paths)
                    )
                )

    groups.sort(key=lambda g: (-g.bytes_reclaimable, g.paths[0]))
    return ScanResult(files_scanned=scanned, groups=groups, warnings=warnings)
