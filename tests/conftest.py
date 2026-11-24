"""A small fake org shared by the tests. All dates are relative to COLLECTED_AT."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sflic.models import Prices, Settings
from sflic.snapshot import write_snapshot

COLLECTED_AT = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)


def user(uid, username, license_id, license_name, last_login, created, user_type="Standard"):
    return {
        "Id": uid,
        "Username": username,
        "Name": username.split("@")[0].title(),
        "UserType": user_type,
        "LastLoginDate": last_login,
        "CreatedDate": created,
        "Profile": {
            "Name": "Standard User",
            "UserLicenseId": license_id,
            "UserLicense": {"Name": license_name},
        },
    }


OLD = "2024-01-01T00:00:00.000+0000"

ORG_DATA = {
    "user_licenses": [
        {"Id": "L1", "Name": "Salesforce", "Status": "Active", "TotalLicenses": 10,
         "UsedLicenses": 6},
        {"Id": "L2", "Name": "Chatter Free", "Status": "Active", "TotalLicenses": 5000,
         "UsedLicenses": 1},
        {"Id": "L3", "Name": "Identity", "Status": "Disabled", "TotalLicenses": 0,
         "UsedLicenses": 0},
    ],
    "permission_set_licenses": [
        {"Id": "P1", "MasterLabel": "Sales Console User", "Status": "Active",
         "TotalLicenses": 5, "UsedLicenses": 2, "ExpirationDate": "2027-03-31"},
    ],
    "package_licenses": [
        {"Id": "K1", "NamespacePrefix": "sbqq", "Status": "Active", "AllowedLicenses": 3,
         "UsedLicenses": 3, "ExpirationDate": None},
        {"Id": "K2", "NamespacePrefix": "sitewide", "Status": "Active", "AllowedLicenses": -1,
         "UsedLicenses": 0, "ExpirationDate": None},
    ],
    "active_users": [
        user("U1", "alice@acme.com", "L1", "Salesforce", "2026-11-12T08:00:00.000+0000", OLD),
        # 165 days without a login; also holds a permission set license and a package seat
        user("U2", "bob@acme.com", "L1", "Salesforce", "2026-06-01T00:00:00.000+0000", OLD),
        # never logged in, created 73 days ago
        user("U3", "carol@acme.com", "L1", "Salesforce", None, "2026-09-01T00:00:00.000+0000"),
        # never logged in, but created 12 days ago: inside the grace period
        user("U4", "dave@acme.com", "L1", "Salesforce", None, "2026-11-01T00:00:00.000+0000"),
        # dormant, but holds only an ignored license
        user("U5", "eve@acme.com", "L2", "Chatter Free", "2025-01-01T00:00:00.000+0000", OLD),
        user("U6", "guest@acme.com", "L1", "Salesforce", None, OLD, user_type="Guest"),
        # rarely logs in by design; excluded by username
        user("U7", "svc@acme.com", "L1", "Salesforce", "2026-01-01T00:00:00.000+0000", OLD),
    ],
    "psl_assignments": [
        {"AssigneeId": "U1", "PermissionSetLicenseId": "P1"},
        {"AssigneeId": "U2", "PermissionSetLicenseId": "P1"},
    ],
    "package_license_assignments": [
        {"UserId": "U2", "PackageLicenseId": "K1"},
        {"UserId": "U1", "PackageLicenseId": "K1"},
    ],
    "frozen_users": [{"UserId": "U2"}],
}  # fmt: skip


def make_snapshot(path: Path, data: dict | None = None, errors: dict | None = None) -> Path:
    return write_snapshot(
        path,
        org_id="00D1",
        org={
            "Id": "00D1",
            "Name": "Acme",
            "OrganizationType": "Enterprise Edition",
            "IsSandbox": False,
        },  # fmt: skip
        instance_url="https://acme.my.salesforce.com",
        api_version="68.0",
        collecting_user="auditor@acme.com",
        collected_at=COLLECTED_AT,
        data={"soql": ORG_DATA if data is None else data},
        errors=errors or {},
    )


@pytest.fixture
def snapshot_dir(tmp_path) -> Path:
    return make_snapshot(tmp_path / "00D1_20261113T120000Z")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        client="Acme Ltd",
        ignore_licenses=["Chatter Free"],
        excluded_users=["SVC@acme.com"],
        prices=Prices(user={"Salesforce": 100}, permission_set={"Sales Console User": 50}),
    )
