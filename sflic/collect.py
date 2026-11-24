"""Run the declared queries against the org and write a snapshot.

SfCliClient and the helpers above collect_data are shared with another project and
kept identical, so a fix can be carried between them as-is. collect() itself takes
the data declaration directly.

Read-only: this module only issues SOQL / Tooling API queries and Metadata API
retrieves (`sf project retrieve start` into a temporary directory).
"""

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote

from sflic.connect import ConnectError, OrgSession, Runner, run_sf_json
from sflic.snapshot import relpath, snapshot_dirname, utc_now, write_snapshot

ORG_QUERY = "SELECT Id, Name, OrganizationType, IsSandbox, InstanceName FROM Organization"

# kind ("soql" | "tooling" | "metadata") -> data name -> query
DataDecl = dict[str, dict[str, str]]

# metadata spec ("Type" or "Type:Name") -> component fullName -> parsed content
Retrieved = dict[str, dict[str, Any]]


class OrgClient(Protocol):
    def soql(self, query: str) -> list[dict[str, Any]]: ...
    def tooling(self, query: str) -> list[dict[str, Any]]: ...
    def retrieve(self, specs: list[str]) -> tuple[Retrieved, list[str]]: ...


class QueryError(Exception):
    pass


def strip_attributes(obj: Any) -> Any:
    """Drop Salesforce `attributes` blocks from query results."""
    if isinstance(obj, dict):
        return {k: strip_attributes(v) for k, v in obj.items() if k != "attributes"}
    if isinstance(obj, list):
        return [strip_attributes(v) for v in obj]
    return obj


def xml_to_dict(elem: ET.Element) -> Any:
    """Metadata XML to JSON-able data. Repeated tags become lists; leaf values stay strings.

    A tag that appears once is a single value, not a one-item list.
    """
    children = list(elem)
    if not children:
        return elem.text.strip() if elem.text and elem.text.strip() else None
    out: dict[str, Any] = {}
    for child in children:
        tag = child.tag.rsplit("}", 1)[-1]
        value = xml_to_dict(child)
        if tag in out:
            if not isinstance(out[tag], list):
                out[tag] = [out[tag]]
            out[tag].append(value)
        else:
            out[tag] = value
    return out


def spec_matches(spec: str, comp_type: str, full_name: str) -> bool:
    """Does a retrieved component belong to a requested spec?

    `Settings:Security` comes back typed as `SecuritySettings` with fullName `Security`.
    """
    spec_type, _, name = spec.partition(":")
    if spec_type == "Settings":
        return comp_type == f"{name}Settings" and full_name == name
    return comp_type == spec_type and (name in ("", "*") or full_name == name)


class SfCliClient:
    """Reads the org through the sf CLI, which owns and refreshes the session."""

    def __init__(self, alias: str, runner: Runner = subprocess.run) -> None:
        self.alias = alias
        self.runner = runner

    def soql(self, query: str) -> list[dict[str, Any]]:
        return self._query(query, [])

    def tooling(self, query: str) -> list[dict[str, Any]]:
        return self._query(query, ["--use-tooling-api"])

    def _query(self, query: str, extra: list[str]) -> list[dict[str, Any]]:
        cmd = ["sf", "data", "query", "--query", query, "--target-org", self.alias, *extra]
        data = run_sf_json(cmd, self.runner)
        if data.get("status") != 0:
            raise QueryError(f"{data.get('name', 'Error')}: {data.get('message', '')}")
        result = data.get("result") or {}
        records = result.get("records") or []
        if not result.get("done", True) or len(records) < result.get("totalSize", 0):
            raise QueryError(f"incomplete result: {len(records)} of {result.get('totalSize')}")
        return strip_attributes(records)

    def retrieve(self, specs: list[str]) -> tuple[Retrieved, list[str]]:
        """Retrieve metadata in one call. Returns components per spec and retrieve warnings."""
        with tempfile.TemporaryDirectory(prefix="sflic-") as tmp:
            target = Path(tmp) / "out"
            cmd = ["sf", "project", "retrieve", "start", "--target-org", self.alias]
            for spec in specs:
                cmd += ["--metadata", spec]
            cmd += ["--target-metadata-dir", str(target), "--unzip", "--single-package"]
            # cwd=tmp keeps sf from touching the working directory
            data = run_sf_json(cmd, lambda c, **kw: self.runner(c, cwd=tmp, **kw))
            if data.get("status") != 0:
                raise QueryError(f"{data.get('name', 'Error')}: {data.get('message', '')}")
            result = data.get("result") or {}
            if not result.get("success"):
                raise QueryError(f"retrieve did not succeed: {result.get('status')}")
            warnings = [m.get("problem", str(m)) for m in result.get("messages") or []]
            return self._parse(target, specs, result.get("fileProperties") or []), warnings

    @staticmethod
    def _parse(target: Path, specs: list[str], props: list[dict[str, Any]]) -> Retrieved:
        out: Retrieved = {spec: {} for spec in specs}
        for prop in props:
            comp_type, full_name = prop.get("type", ""), unquote(prop.get("fullName", ""))
            if comp_type == "Package":
                continue
            file = next(
                (p for p in (target / prop["fileName"], target / "unpackaged" / prop["fileName"])
                 if p.is_file()),
                None,
            )  # fmt: skip
            if file is None:
                raise QueryError(f"retrieved file missing: {prop['fileName']}")
            content = xml_to_dict(ET.parse(file).getroot())
            for spec in specs:
                if spec_matches(spec, comp_type, full_name):
                    out[spec][full_name] = content
        return out


def collect_data(
    client: OrgClient, decl: DataDecl
) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[str]]:
    """Run every declared query and one metadata retrieve. Failures are recorded, never fatal."""
    data: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    warnings: list[str] = []
    for kind, run in (("soql", client.soql), ("tooling", client.tooling)):
        for name, query in decl.get(kind, {}).items():
            try:
                data.setdefault(kind, {})[name] = run(query)
            except Exception as e:  # noqa: BLE001
                errors[relpath(kind, name)] = f"{type(e).__name__}: {e}"

    metadata = decl.get("metadata", {})
    if metadata:
        try:
            retrieved, warnings = client.retrieve(sorted(set(metadata.values())))
        except Exception as e:  # noqa: BLE001
            for name in metadata:
                errors[relpath("metadata", name)] = f"{type(e).__name__}: {e}"
        else:
            for name, spec in metadata.items():
                data.setdefault("metadata", {})[name] = retrieved.get(spec, {})
    return data, errors, warnings


def collect(
    session: OrgSession,
    client: OrgClient,
    decl: DataDecl,
    out_root: Path,
) -> Path:
    collected_at = utc_now()
    try:
        org_rows = client.soql(ORG_QUERY)
        org = org_rows[0] if org_rows else {}
    except Exception as e:
        # If Organization can't be read, nothing else will be; write no snapshot.
        raise ConnectError(f"cannot query org {session.alias!r}: {e}") from e

    data, errors, warnings = collect_data(client, decl)
    return write_snapshot(
        out_root / snapshot_dirname(session.org_id, collected_at),
        org_id=session.org_id,
        org=org,
        instance_url=session.instance_url,
        api_version=session.api_version,
        collecting_user=session.username,
        collected_at=collected_at,
        data=data,
        errors=errors,
        warnings=warnings,
        queries={relpath(k, n): q for k, items in decl.items() for n, q in items.items()},
    )
