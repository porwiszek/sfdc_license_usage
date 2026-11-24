"""End to end through the real subprocess path, against a fake `sf` executable."""

import json
import os
import stat
import sys

import pytest
from conftest import ORG_DATA
from typer.testing import CliRunner

from sflic.cli import app

FAKE_SF = """#!{python}
import json, sys

args = sys.argv[1:]
data = json.load(open({data_path!r}))
if args[:2] == ["org", "display"]:
    out = {{"status": 0, "result": {{"id": "00D1", "instanceUrl": "https://acme.my.salesforce.com",
            "apiVersion": "68.0", "username": "auditor@acme.com", "accessToken": "SECRET"}}}}
elif args[:2] == ["data", "query"]:
    query = args[args.index("--query") + 1]
    assert query.lstrip().upper().startswith("SELECT"), query
    sobject = query.split(" FROM ")[1].split()[0]
    if sobject in data["fail"]:
        out = {{"status": 1, "name": "INVALID_TYPE", "message": "sObject type not supported"}}
    else:
        records = data["records"][sobject]
        out = {{"status": 0, "result": {{"done": True, "totalSize": len(records),
                "records": records}}}}
else:
    out = {{"status": 1, "name": "Unexpected", "message": " ".join(args)}}
print(json.dumps(out))
"""

BY_SOBJECT = {
    "Organization": [{"Id": "00D1", "Name": "Acme", "OrganizationType": "Enterprise Edition",
                      "IsSandbox": False, "InstanceName": "EU1"}],
    "UserLicense": ORG_DATA["user_licenses"],
    "PermissionSetLicense": ORG_DATA["permission_set_licenses"],
    "PackageLicense": ORG_DATA["package_licenses"],
    "User": ORG_DATA["active_users"],
    "PermissionSetLicenseAssign": ORG_DATA["psl_assignments"],
    "UserPackageLicense": ORG_DATA["package_license_assignments"],
    "UserLogin": ORG_DATA["frozen_users"],
}  # fmt: skip


@pytest.fixture
def fake_sf(tmp_path, monkeypatch):
    def install(fail: list[str] | None = None):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        data_path = tmp_path / "org.json"
        data_path.write_text(json.dumps({"records": BY_SOBJECT, "fail": fail or []}))
        script = bin_dir / "sf"
        script.write_text(FAKE_SF.format(python=sys.executable, data_path=str(data_path)))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    return install


def run(tmp_path, *args):
    return CliRunner().invoke(
        app,
        [*args, "--snapshots", str(tmp_path / "snapshots"), "--reports", str(tmp_path / "reports")],
    )


def test_run_end_to_end(tmp_path, fake_sf):
    fake_sf()
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "client: Acme Ltd\nignore_licenses: [Chatter Free]\nexcluded_users: [svc@acme.com]\n"
        "prices:\n  user: {Salesforce: 100}\n  permission_set: {Sales Console User: 50}\n"
    )
    result = run(tmp_path, "run", "--org", "dev", "--settings", str(settings))
    assert result.exit_code == 0, result.output
    assert "Up to EUR 9,600 a year" in result.output
    assert "SECRET" not in result.output
    (snapshot,) = (tmp_path / "snapshots").iterdir()
    out = tmp_path / "reports" / snapshot.name
    assert {p.name for p in out.iterdir()} == {"analysis.json", "report.html", "dormant_users.csv"}
    assert "SECRET" not in (snapshot / "manifest.json").read_text()
    analysis = json.loads((out / "analysis.json").read_text())
    assert analysis["not_collected"] == {} and analysis["integrity_issues"] == []
    assert [u["username"] for u in analysis["dormant_users"]] == ["bob@acme.com", "carol@acme.com"]


def test_run_survives_a_query_the_org_rejects(tmp_path, fake_sf):
    fake_sf(fail=["PackageLicense", "UserPackageLicense"])
    result = run(tmp_path, "run", "--org", "dev")
    assert result.exit_code == 0, result.output
    assert "soql/package_licenses.json not collected" in result.output
    (snapshot,) = (tmp_path / "snapshots").iterdir()
    html = (tmp_path / "reports" / snapshot.name / "report.html").read_text()
    assert "Not collected" in html and "INVALID_TYPE" in html


def test_bad_settings_fail_before_collecting(tmp_path, fake_sf):
    fake_sf()
    settings = tmp_path / "settings.yaml"
    settings.write_text("inactive_day: 60\n")  # typo
    result = run(tmp_path, "run", "--org", "dev", "--settings", str(settings))
    assert result.exit_code == 1 and "inactive_day" in result.output
    assert not (tmp_path / "snapshots").exists()


def test_missing_sf_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    args = ["collect", "--org", "dev", "--snapshots", str(tmp_path / "snapshots")]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1 and "`sf` not found" in result.output
    assert not (tmp_path / "snapshots").exists()
