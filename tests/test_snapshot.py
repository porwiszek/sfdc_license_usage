from datetime import UTC, datetime

import pytest

from sflic.snapshot import (
    MissingDataError,
    Snapshot,
    SnapshotError,
    snapshot_dirname,
    write_snapshot,
)

TS = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def make(tmp_path, **kw):
    args = dict(
        org_id="00D1",
        org={"Name": "Acme"},
        instance_url="https://acme.my.salesforce.com",
        api_version="62.0",
        collecting_user="auditor@acme.com",
        collected_at=TS,
        data={"soql": {"users": [{"Id": "005"}]}},
        errors={},
    )
    args.update(kw)
    return write_snapshot(tmp_path / "snap", **args)


def test_dirname_is_utc():
    assert snapshot_dirname("00D1", TS) == "00D1_20260304T050607Z"


def test_roundtrip_and_manifest(tmp_path):
    snap = Snapshot(make(tmp_path))
    assert snap.soql("users") == [{"Id": "005"}]
    assert snap.manifest["collected_at"] == "2026-03-04T05:06:07Z"
    assert set(snap.manifest["files"]) == {"soql/users.json"}
    assert snap.verify() == []


def test_refuses_overwrite(tmp_path):
    make(tmp_path)
    with pytest.raises(FileExistsError):
        make(tmp_path)


def test_verify_detects_tampering(tmp_path):
    path = make(tmp_path)
    (path / "soql/users.json").write_text("[]")
    (path / "soql/extra.json").write_text("[]")
    issues = Snapshot(path).verify()
    assert any("users.json: SHA-256 does not match" in i for i in issues)
    assert any("extra.json: present but not listed" in i for i in issues)


def test_verify_detects_missing_file(tmp_path):
    path = make(tmp_path)
    (path / "soql/users.json").unlink()
    assert Snapshot(path).verify() == ["soql/users.json: listed in manifest but missing"]


def test_missing_and_failed_data_raise_missing_data(tmp_path):
    snap = Snapshot(make(tmp_path, errors={"tooling/health.json": "boom"}))
    with pytest.raises(MissingDataError, match="boom"):
        snap.tooling("health")
    with pytest.raises(MissingDataError, match="not in the snapshot"):
        snap.soql("nope")


def test_invalid_names_rejected(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path, data={"soql": {"../escape": []}})


def test_no_manifest(tmp_path):
    with pytest.raises(SnapshotError):
        Snapshot(tmp_path)
