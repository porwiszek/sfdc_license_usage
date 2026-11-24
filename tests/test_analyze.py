from datetime import timedelta

import pytest
from conftest import COLLECTED_AT, OLD, ORG_DATA, make_snapshot, user

from sflic.analyze import analyze, dormancy
from sflic.models import PoolKind, Prices, Settings


def pool(result, name):
    return next(p for p in result.pools if p.name == name)


def test_pools_count_unassigned_and_dormant_seats(snapshot_dir, settings):
    result = analyze(snapshot_dir, settings)
    sf = pool(result, "Salesforce")
    assert (sf.total, sf.used, sf.unassigned, sf.dormant) == (10, 6, 4, 2)
    console = pool(result, "Sales Console User")
    assert (console.kind, console.unassigned, console.dormant) == (PoolKind.PERMISSION_SET, 3, 1)
    assert console.expires == "2027-03-31"
    sbqq = pool(result, "sbqq")
    assert (sbqq.unassigned, sbqq.dormant, sbqq.monthly_price) == (0, 1, None)


def test_pools_leave_out_ignored_and_uncounted_licenses(snapshot_dir, settings):
    names = {p.name for p in analyze(snapshot_dir, settings).pools}
    # Chatter Free is ignored, Identity has no seats, sitewide is a site license (-1)
    assert names == {"Salesforce", "Sales Console User", "sbqq"}


def test_dormant_users(snapshot_dir, settings):
    result = analyze(snapshot_dir, settings)
    assert [u.username for u in result.dormant_users] == ["bob@acme.com", "carol@acme.com"]
    bob, carol = result.dormant_users
    assert bob.reason == "no login for 165 days" and bob.days_inactive == 165
    assert bob.frozen is True
    assert bob.other_seats == ["Sales Console User", "sbqq"]
    assert bob.annual_cost == 100 * 12 + 50 * 12 and bob.unpriced_seats == 1
    assert carol.reason == "never logged in, created 73 days ago"
    assert (carol.last_login, carol.days_inactive, carol.frozen) == ("never", None, False)
    assert carol.annual_cost == 1200


def test_amounts(snapshot_dir, settings):
    result = analyze(snapshot_dir, settings)
    t = result.totals
    assert (t.unassigned_seats, t.dormant_users, t.dormant_seats) == (7, 2, 4)
    assert t.annual_unassigned == 4 * 1200 + 3 * 600
    assert t.annual_dormant == 2 * 1200 + 1 * 600
    assert t.annual_total == 9600
    assert (t.priced_pools, t.unpriced_pools_with_waste) == (2, 1)
    # largest amount first, unpriced last
    assert [p.name for p in result.pools] == ["Salesforce", "Sales Console User", "sbqq"]
    assert pool(result, "Salesforce").annual(6) == 7200


def test_without_prices_counts_only(snapshot_dir):
    result = analyze(snapshot_dir, Settings())
    assert result.totals.annual_total == 0 and result.totals.priced_pools == 0
    assert all(u.annual_cost is None for u in result.dormant_users)
    # nothing ignored or excluded by username: eve (Chatter Free) and svc are reported too
    assert {u.username for u in result.dormant_users} == {
        "bob@acme.com", "carol@acme.com", "eve@acme.com", "svc@acme.com"
    }  # fmt: skip


def test_thresholds_are_settings(snapshot_dir, settings):
    relaxed = settings.model_copy(update={"inactive_days": 200, "never_logged_in_grace_days": 80})
    assert analyze(snapshot_dir, relaxed).dormant_users == []
    strict = settings.model_copy(update={"never_logged_in_grace_days": 10})
    assert "dave@acme.com" in {u.username for u in analyze(snapshot_dir, strict).dormant_users}


def _with_extra_users(tmp_path, *extra):
    data = {
        **ORG_DATA,
        "active_users": [*ORG_DATA["active_users"], *(u for u, _ in extra)],
        "psl_assignments": [*ORG_DATA["psl_assignments"], *(a for _, a in extra if a)],
    }
    return make_snapshot(tmp_path / "s", data)


def test_chatter_only_users_are_not_people_by_default(tmp_path, settings):
    # Seen on a real org: the "Chatter Expert" system user has UserType CsnOnly.
    chatty = user("U8", "chatty@acme.com", "L1", "Salesforce", None, OLD, user_type="CsnOnly")
    result = analyze(_with_extra_users(tmp_path, (chatty, None)), settings)
    assert "chatty@acme.com" not in {u.username for u in result.dormant_users}


def test_users_on_excluded_user_licenses_are_not_dormant(tmp_path):
    # Agent users hold an Einstein Agent user license plus permission set licenses,
    # never log in, and are not people. Their pools are still reported.
    agent = user("U9", "agent@acme.com", "L1", "Einstein Agent", None, OLD)
    snap = _with_extra_users(
        tmp_path, (agent, {"AssigneeId": "U9", "PermissionSetLicenseId": "P1"})
    )
    result = analyze(snap, Settings(excluded_user_licenses=["Einstein Agent"]))
    assert "agent@acme.com" not in {u.username for u in result.dormant_users}
    assert pool(result, "Sales Console User").dormant == 1  # bob only
    assert "agent@acme.com" in {
        u.username for u in analyze(snap, Settings(excluded_user_licenses=[])).dormant_users
    }


def test_default_excluded_user_licenses_are_agent_and_integration_licenses():
    assert Settings().excluded_user_licenses == [
        "Einstein Agent",
        "Analytics Cloud Integration User",
    ]


@pytest.mark.parametrize(("days", "dormant"), [(90, False), (91, True)])
def test_inactive_boundary(days, dormant):
    last = (COLLECTED_AT - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    result = dormancy({"LastLoginDate": last}, COLLECTED_AT, Settings())
    assert (result is not None) is dormant


def test_settings_that_match_nothing_are_reported(snapshot_dir):
    settings = Settings(
        ignore_licenses=["Chater Free"],
        prices=Prices(user={"Salesforce": 100, "Salesforce Platfrom": 25}, package={"cpq": 10}),
    )
    warnings = analyze(snapshot_dir, settings).warnings
    assert any("'Salesforce Platfrom'" in w and "prices.user" in w for w in warnings)
    assert any("'cpq'" in w and "prices.package" in w for w in warnings)
    assert any("'Chater Free'" in w and "ignore_licenses" in w for w in warnings)
    assert len(warnings) == 3


def test_failed_query_is_reported_not_raised(tmp_path, settings):
    data = {k: v for k, v in ORG_DATA.items() if k != "permission_set_licenses"}
    errors = {"soql/permission_set_licenses.json": "QueryError: INVALID_TYPE: sObject type"}
    result = analyze(make_snapshot(tmp_path / "s", data, errors), settings)
    assert "INVALID_TYPE" in result.not_collected["soql/permission_set_licenses.json"]
    assert {p.name for p in result.pools} == {"Salesforce", "sbqq"}
    # same amount now, so the user who never logged in comes first
    assert [u.username for u in result.dormant_users] == ["carol@acme.com", "bob@acme.com"]
    # a missing list must not produce "unknown license" warnings about other settings
    assert result.warnings == []


def test_missing_users_gives_pools_without_dormancy(tmp_path, settings):
    data = {k: v for k, v in ORG_DATA.items() if k != "active_users"}
    result = analyze(make_snapshot(tmp_path / "s", data), settings)
    assert "soql/active_users.json" in result.not_collected
    assert result.dormant_users == [] and result.totals.dormant_seats == 0
    assert result.totals.unassigned_seats == 7


def test_unreadable_date_is_a_warning(tmp_path, settings):
    users = [dict(ORG_DATA["active_users"][1], LastLoginDate="yesterday")]
    result = analyze(make_snapshot(tmp_path / "s", {**ORG_DATA, "active_users": users}), settings)
    assert result.dormant_users == []
    assert result.warnings == ["bob@acme.com: unreadable login or creation date; not assessed"]


def test_analysis_is_reproducible_and_verifies_snapshot(snapshot_dir, settings):
    first, second = analyze(snapshot_dir, settings), analyze(snapshot_dir, settings)
    assert first.pools == second.pools and first.dormant_users == second.dormant_users
    assert first.integrity_issues == []
    (snapshot_dir / "soql" / "user_licenses.json").write_text("[]")
    assert analyze(snapshot_dir, settings).integrity_issues == [
        "soql/user_licenses.json: SHA-256 does not match manifest"
    ]
