import json
import subprocess
from pathlib import Path

import pytest

from sflic.collect import (
    QueryError,
    SfCliClient,
    collect,
    collect_data,
    spec_matches,
    strip_attributes,
)
from sflic.connect import ConnectError, OrgSession
from sflic.queries import DATA
from sflic.snapshot import Snapshot


class FakeClient:
    """Stands in for the org. Records queries; no network."""

    def __init__(self, fail_on: str = ""):
        self.queries: list[str] = []
        self.fail_on = fail_on

    def soql(self, query):
        self.queries.append(query)
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("INVALID_FIELD")
        if "FROM Organization" in query:
            return [{"Id": "00D1", "Name": "Acme"}]
        return [{"Id": "1"}]

    def tooling(self, query):
        self.queries.append(query)
        return [{"Score": "90"}]

    def retrieve(self, specs):
        self.queries.append(f"RETRIEVE {specs}")
        if self.fail_on == "RETRIEVE":
            raise RuntimeError("retrieve failed")
        return {"Settings:Security": {"Security": {"passwordPolicies": {}}}}, ["a warning"]


def test_collect_data_records_errors_without_stopping():
    decl = {
        "soql": {"ok": "SELECT Id FROM User", "bad": "SELECT Nope FROM User"},
        "metadata": {"security": "SecuritySettings"},
    }
    data, errors, _ = collect_data(FakeClient(fail_on="Nope"), decl)
    assert data["soql"] == {"ok": [{"Id": "1"}]}
    assert errors["soql/bad.json"] == "RuntimeError: INVALID_FIELD"


def test_collect_data_metadata_in_one_retrieve():
    decl = {"metadata": {"security": "Settings:Security", "profiles": "Profile"}}
    client = FakeClient()
    data, errors, warnings = collect_data(client, decl)
    assert client.queries == ["RETRIEVE ['Profile', 'Settings:Security']"]
    assert data["metadata"] == {
        "security": {"Security": {"passwordPolicies": {}}},
        "profiles": {},  # nothing of that type in the org
    }
    assert errors == {} and warnings == ["a warning"]


def test_collect_data_metadata_failure_marks_every_name():
    decl = {"metadata": {"security": "Settings:Security", "profiles": "Profile"}}
    data, errors, _ = collect_data(FakeClient(fail_on="RETRIEVE"), decl)
    assert "metadata" not in data
    assert set(errors) == {"metadata/security.json", "metadata/profiles.json"}


def test_strip_attributes():
    profile = {"attributes": {}, "Name": "Admin"}
    rec = {"attributes": {"type": "User"}, "Id": "1", "Profile": profile}
    assert strip_attributes([rec]) == [{"Id": "1", "Profile": {"Name": "Admin"}}]


def test_collect_writes_snapshot(tmp_path):
    session = OrgSession("dev", "00D1", "https://acme", "auditor@acme.com", "62.0")
    client = FakeClient()
    path = collect(session, client, DATA, tmp_path)
    snap = Snapshot(path)
    assert path.name.startswith("00D1_")
    assert snap.manifest["org"] == {"Id": "00D1", "Name": "Acme"}
    assert snap.soql("user_licenses") == [{"Id": "1"}]
    assert set(snap.manifest["files"]) == {f"soql/{name}.json" for name in DATA["soql"]}
    assert snap.manifest["queries"]["soql/user_licenses.json"].endswith("FROM UserLicense")
    assert snap.verify() == []
    # read-only: only SELECT queries were issued
    assert all(q.lstrip().upper().startswith("SELECT") for q in client.queries)


def test_collect_records_a_failing_query(tmp_path):
    session = OrgSession("dev", "00D1", "https://acme", "auditor@acme.com", "62.0")
    path = collect(session, FakeClient(fail_on="FROM PackageLicense"), DATA, tmp_path)
    snap = Snapshot(path)
    assert snap.manifest["errors"] == {"soql/package_licenses.json": "RuntimeError: INVALID_FIELD"}
    assert "soql/package_licenses.json" not in snap.manifest["files"]


def test_collect_aborts_when_org_unreachable(tmp_path):
    class Down(FakeClient):
        def soql(self, query):
            raise QueryError("INVALID_SESSION_ID: Session expired or invalid")

    session = OrgSession("dev", "00D1", "https://acme", "auditor@acme.com", "62.0")
    with pytest.raises(ConnectError, match="INVALID_SESSION_ID"):
        collect(session, Down(), DATA, tmp_path)
    assert list(tmp_path.iterdir()) == []  # no snapshot written


def test_every_query_is_a_select():
    for kind, items in DATA.items():
        for name, query in items.items():
            assert query.startswith("SELECT "), f"{kind}/{name}"


def sf_runner(payload: dict, seen: list):
    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    return run


def test_sf_cli_client_soql_and_tooling():
    payload = {
        "status": 0,
        "result": {"done": True, "totalSize": 1, "records": [{"attributes": {}, "Id": "1"}]},
    }
    seen: list = []
    client = SfCliClient("dev", sf_runner(payload, seen))
    assert client.soql("SELECT Id FROM User") == [{"Id": "1"}]
    assert client.tooling("SELECT Score FROM SecurityHealthCheck") == [{"Id": "1"}]
    assert seen[0] == [
        "sf", "data", "query", "--query", "SELECT Id FROM User", "--target-org", "dev", "--json"
    ]  # fmt: skip
    assert "--use-tooling-api" in seen[1]


def test_sf_cli_client_errors():
    seen: list = []
    bad = {"status": 1, "name": "INVALID_FIELD", "message": "No such column"}
    with pytest.raises(QueryError, match="INVALID_FIELD: No such column"):
        SfCliClient("dev", sf_runner(bad, seen)).soql("SELECT Nope FROM User")
    partial = {"status": 0, "result": {"done": False, "totalSize": 5, "records": [{"Id": "1"}]}}
    with pytest.raises(QueryError, match="incomplete"):
        SfCliClient("dev", sf_runner(partial, seen)).soql("SELECT Id FROM User")


ADMIN_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <custom>false</custom>
    <loginIpRanges>
        <endAddress>255.255.255.255</endAddress>
        <startAddress>0.0.0.0</startAddress>
    </loginIpRanges>
    <userLicense>Salesforce</userLicense>
    <userPermissions><enabled>true</enabled><name>ApiEnabled</name></userPermissions>
    <userPermissions><enabled>true</enabled><name>ModifyAllData</name></userPermissions>
</Profile>
"""

SECURITY = """<?xml version="1.0" encoding="UTF-8"?>
<SecuritySettings xmlns="http://soap.sforce.com/2006/04/metadata">
    <networkAccess/>
    <passwordPolicies><minimumPasswordLength>8</minimumPasswordLength></passwordPolicies>
</SecuritySettings>
"""


def retrieve_runner(seen: list, files: dict[str, str], props: list[dict], messages=()):
    """Fake `sf project retrieve start`: writes files where sf would and returns its JSON."""

    def run(cmd, cwd=None, **kw):
        seen.append((cmd, cwd))
        target = Path(cmd[cmd.index("--target-metadata-dir") + 1]) / "unpackaged"
        for rel, text in files.items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_text(text)
        payload = {
            "status": 0,
            "result": {
                "success": True,
                "status": "Succeeded",
                "fileProperties": props,
                "messages": list(messages),
            },
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    return run


def test_sf_cli_client_retrieve_parses_metadata(tmp_path):
    seen: list = []
    files = {
        "profiles/Admin.profile": ADMIN_PROFILE,
        "profiles/Custom%3A Sales.profile": ADMIN_PROFILE,
        "settings/Security.settings": SECURITY,
    }
    props = [
        {"type": "Profile", "fullName": "Admin", "fileName": "profiles/Admin.profile"},
        {
            "type": "Profile",
            "fullName": "Custom%3A Sales",
            "fileName": "profiles/Custom%3A Sales.profile",
        },
        {
            "type": "SecuritySettings",
            "fullName": "Security",
            "fileName": "settings/Security.settings",
        },
        {"type": "Package", "fullName": "package.xml", "fileName": "package.xml"},
    ]
    missing = [
        {"fileName": "package.xml", "problem": "Entity of type 'Profile' named 'X' cannot be found"}
    ]
    client = SfCliClient("dev", retrieve_runner(seen, files, props, missing))
    retrieved, warnings = client.retrieve(["Profile", "Settings:Security"])

    admin = retrieved["Profile"]["Admin"]
    assert admin["loginIpRanges"] == {"startAddress": "0.0.0.0", "endAddress": "255.255.255.255"}
    assert admin["userPermissions"][1] == {"enabled": "true", "name": "ModifyAllData"}
    assert "Custom: Sales" in retrieved["Profile"]  # URL-decoded name
    assert retrieved["Settings:Security"]["Security"] == {
        "networkAccess": None,
        "passwordPolicies": {"minimumPasswordLength": "8"},
    }
    assert warnings == ["Entity of type 'Profile' named 'X' cannot be found"]

    cmd, cwd = seen[0]
    assert cmd[:4] == ["sf", "project", "retrieve", "start"]
    assert cmd.count("--metadata") == 2
    assert cwd is not None and not Path(cwd).exists()  # temp dir cleaned up


def test_sf_cli_client_retrieve_failure():
    seen: list = []
    bad = {"status": 1, "name": "RetrieveFailed", "message": "boom"}
    with pytest.raises(QueryError, match="RetrieveFailed: boom"):
        SfCliClient("dev", lambda c, **kw: sf_runner(bad, seen)(c)).retrieve(["Profile"])


@pytest.mark.parametrize(
    ("spec", "comp_type", "name", "expected"),
    [
        ("Settings:Security", "SecuritySettings", "Security", True),
        ("Settings:Security", "SessionSettings", "Session", False),
        ("Profile", "Profile", "Admin", True),
        ("Profile:*", "Profile", "Admin", True),
        ("Profile:Admin", "Profile", "Read Only", False),
        ("ConnectedApp", "Profile", "Admin", False),
    ],
)
def test_spec_matches(spec, comp_type, name, expected):
    assert spec_matches(spec, comp_type, name) is expected
