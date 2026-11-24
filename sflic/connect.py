"""Org identity from an existing Salesforce CLI session.

MVP auth: `sf org login web --alias <alias>` once. The sf CLI owns the session
and its token; sflic never reads or stores the token. Queries also run through
sf (see collect.SfCliClient), so sf refreshes the session as needed.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ConnectError(Exception):
    pass


@dataclass(frozen=True)
class OrgSession:
    alias: str
    org_id: str
    instance_url: str
    username: str
    api_version: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_sf_json(cmd: list[str], runner: Runner = subprocess.run) -> dict[str, Any]:
    """Run an sf command with --json and return the parsed output. Never echoes stdout."""
    try:
        proc = runner([*cmd, "--json"], capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise ConnectError("Salesforce CLI `sf` not found on PATH") from e
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ConnectError(f"could not parse output of `{' '.join(cmd[:3])}`") from e


def parse_org_display(alias: str, data: dict[str, Any]) -> OrgSession:
    if data.get("status") != 0:
        raise ConnectError(
            f"`sf org display` failed for {alias!r}: {data.get('message', '')}; "
            f"try `sf org login web --alias {alias}`"
        )
    result = data.get("result") or {}
    missing = [k for k in ("id", "instanceUrl", "apiVersion") if not result.get(k)]
    if missing:
        raise ConnectError(f"`sf org display` for {alias!r} is missing {', '.join(missing)}")
    return OrgSession(
        alias=alias,
        org_id=result["id"],
        instance_url=result["instanceUrl"],
        username=result.get("username", ""),
        api_version=result["apiVersion"],
    )


def get_session(alias: str, runner: Runner = subprocess.run) -> OrgSession:
    data = run_sf_json(["sf", "org", "display", "--target-org", alias], runner)
    data.get("result", {}).pop("accessToken", None)  # not needed, don't keep it around
    return parse_org_display(alias, data)
