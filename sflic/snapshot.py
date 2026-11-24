"""Snapshot read/write, manifest and SHA-256 hashing.

A snapshot is the audit evidence for one run:

    <dir>/manifest.json
    <dir>/soql/<name>.json
    <dir>/tooling/<name>.json
    <dir>/metadata/<name>.json
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sflic import __version__

KINDS = ("soql", "tooling", "metadata")
MANIFEST = "manifest.json"
NAME_RE = re.compile(r"^[a-z0-9_]+$")


class SnapshotError(Exception):
    """The snapshot directory itself is missing or unreadable."""


class MissingDataError(Exception):
    """A data file a check needs is absent or failed to collect."""


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso_utc(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_dirname(org_id: str, collected_at: datetime) -> str:
    return f"{org_id}_{collected_at.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def relpath(kind: str, name: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown data kind {kind!r}; expected one of {KINDS}")
    if not NAME_RE.match(name):
        raise ValueError(f"invalid data name {name!r}; use lowercase letters, digits, _")
    return f"{kind}/{name}.json"


def write_snapshot(
    path: Path,
    *,
    org_id: str,
    org: dict[str, Any],
    instance_url: str,
    api_version: str,
    collecting_user: str,
    collected_at: datetime,
    data: dict[str, dict[str, Any]],
    errors: dict[str, str],
    warnings: list[str] | None = None,
    queries: dict[str, str] | None = None,
) -> Path:
    """Write data files and a manifest hashing each of them. Refuses to overwrite."""
    path.mkdir(parents=True, exist_ok=False)
    files: dict[str, str] = {}
    for kind, items in data.items():
        for name, payload in items.items():
            rel = relpath(kind, name)
            target = path / rel
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
            files[rel] = sha256_file(target)

    manifest = {
        "org_id": org_id,
        "org": org,
        "instance_url": instance_url,
        "api_version": api_version,
        "collected_at": iso_utc(collected_at),
        "collecting_user": collecting_user,
        "tool_version": __version__,
        "files": dict(sorted(files.items())),
        "errors": dict(sorted(errors.items())),
        "warnings": warnings or [],
        # the query or metadata spec behind each data file, so comparisons can tell
        # whether two snapshots collected the same data
        "queries": dict(sorted((queries or {}).items())),
    }
    (path / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    return path


class Snapshot:
    """Read-only view of a snapshot directory. Checks read data only through this."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        manifest_path = self.path / MANIFEST
        try:
            self.manifest: dict[str, Any] = json.loads(manifest_path.read_text("utf-8"))
        except FileNotFoundError as e:
            raise SnapshotError(f"no {MANIFEST} in {self.path}") from e
        except json.JSONDecodeError as e:
            raise SnapshotError(f"{manifest_path} is not valid JSON: {e}") from e

    def collected_at(self) -> datetime:
        """Reference time for age-based checks, so re-evaluation is reproducible."""
        try:
            return datetime.strptime(self.manifest["collected_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MissingDataError("manifest has no valid collected_at") from e

    def soql(self, name: str) -> Any:
        return self._read("soql", name)

    def tooling(self, name: str) -> Any:
        return self._read("tooling", name)

    def metadata(self, name: str) -> Any:
        return self._read("metadata", name)

    def _read(self, kind: str, name: str) -> Any:
        rel = relpath(kind, name)
        error = self.manifest.get("errors", {}).get(rel)
        if error:
            raise MissingDataError(f"{rel} failed to collect: {error}")
        try:
            return json.loads((self.path / rel).read_text("utf-8"))
        except FileNotFoundError as e:
            raise MissingDataError(f"{rel} is not in the snapshot") from e
        except json.JSONDecodeError as e:
            raise MissingDataError(f"{rel} is not valid JSON: {e}") from e

    def verify(self) -> list[str]:
        """Compare data files against manifest hashes. Returns a list of problems."""
        issues: list[str] = []
        recorded: dict[str, str] = self.manifest.get("files", {})
        for rel, expected in recorded.items():
            file = self.path / rel
            if not file.is_file():
                issues.append(f"{rel}: listed in manifest but missing")
            elif sha256_file(file) != expected:
                issues.append(f"{rel}: SHA-256 does not match manifest")
        for kind in KINDS:
            for file in sorted((self.path / kind).glob("*.json")):
                rel = f"{kind}/{file.name}"
                if rel not in recorded:
                    issues.append(f"{rel}: present but not listed in manifest")
        return issues
