import json
import subprocess

import pytest

from sflic.connect import ConnectError, get_session


def runner(payload: dict):
    def run(cmd, **kw):
        assert cmd == ["sf", "org", "display", "--target-org", "dev", "--json"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    return run


def test_parses_session_and_drops_token():
    payload = {
        "status": 0,
        "result": {
            "id": "00D1",
            "instanceUrl": "https://acme.my.salesforce.com",
            "accessToken": "SECRET",
            "apiVersion": "62.0",
            "username": "auditor@acme.com",
        },
    }
    s = get_session("dev", runner(payload))
    assert (s.org_id, s.api_version, s.username) == ("00D1", "62.0", "auditor@acme.com")
    assert "SECRET" not in repr(s)


def test_sf_error_status():
    with pytest.raises(ConnectError, match="No authorization"):
        get_session("dev", runner({"status": 1, "message": "No authorization found"}))


def test_missing_fields():
    with pytest.raises(ConnectError, match="instanceUrl"):
        get_session("dev", runner({"status": 0, "result": {"id": "1", "apiVersion": "62.0"}}))


def test_unparseable_output_does_not_leak():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="garbage SECRET", stderr="")

    with pytest.raises(ConnectError) as e:
        get_session("dev", run)
    assert "SECRET" not in str(e.value)
