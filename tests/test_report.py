import csv
import io
import json

from sflic.analyze import analyze
from sflic.models import Settings
from sflic.report.render import dormant_csv, headline, render_report, write_outputs


def test_headline_with_and_without_prices(snapshot_dir, settings):
    assert headline(analyze(snapshot_dir, settings)) == (
        "Up to EUR 9,600 a year: 7 unassigned seat(s) and 4 seat(s) held by 2 dormant user(s). "
        "1 license(s) with waste have no price yet."
    )
    assert "Add contract prices" in headline(analyze(snapshot_dir, Settings()))


def test_report_contents(snapshot_dir, settings):
    html = render_report(analyze(snapshot_dir, settings))
    assert "<h1>Acme Ltd</h1>" in html
    assert "EUR 9,600" in html and "EUR 7,200" in html
    assert "bob@acme.com" in html and "no login for 165 days" in html
    assert "2026-06-01 00:00 UTC" in html  # timestamps formatted for people
    assert "alice@acme.com" not in html  # active users are not listed
    assert "All data files matched their SHA-256 hashes" in html
    assert "Not collected" not in html


def test_report_escapes_org_data(tmp_path, settings):
    from conftest import ORG_DATA, make_snapshot

    users = [dict(ORG_DATA["active_users"][1], Name="<script>alert(1)</script>")]
    snap = make_snapshot(tmp_path / "s", {**ORG_DATA, "active_users": users})
    html = render_report(analyze(snap, settings))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_report_shows_missing_data(tmp_path, settings):
    from conftest import ORG_DATA, make_snapshot

    data = {k: v for k, v in ORG_DATA.items() if k != "package_licenses"}
    snap = make_snapshot(tmp_path / "s", data, {"soql/package_licenses.json": "QueryError: boom"})
    html = render_report(analyze(snap, settings))
    assert "Not collected" in html and "QueryError: boom" in html


def test_csv_and_files(snapshot_dir, settings, tmp_path):
    result = analyze(snapshot_dir, settings)
    rows = list(csv.DictReader(io.StringIO(dormant_csv(result))))
    assert [r["username"] for r in rows] == ["bob@acme.com", "carol@acme.com"]
    assert rows[0]["annual_cost_EUR"] == "1800.00" and rows[0]["frozen"] == "yes"
    assert rows[1]["last_login"] == "never" and rows[1]["days_inactive"] == ""
    paths = write_outputs(result, tmp_path / "out")
    assert json.loads(paths["analysis"].read_text())["totals"]["unassigned_seats"] == 7
    assert paths["report"].read_text().startswith("<!doctype html>")
