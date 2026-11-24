"""Render an Analysis into one self-contained HTML file, plus a CSV of dormant users."""

import csv
import io
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from sflic.models import Analysis, PoolKind

SF_TIMESTAMP = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:\+0000|Z)")

# What each data file feeds, for the evidence appendix.
FILE_USE = {
    "soql/user_licenses.json": "User license seats",
    "soql/permission_set_licenses.json": "Permission set license seats",
    "soql/package_licenses.json": "Managed package seats",
    "soql/active_users.json": "Dormant users",
    "soql/psl_assignments.json": "Permission set licenses held by dormant users",
    "soql/package_license_assignments.json": "Package seats held by dormant users",
    "soql/frozen_users.json": "Frozen flag on dormant users",
}


def fmt_time(value: Any) -> str:
    """One UTC timestamp style throughout."""
    return SF_TIMESTAMP.sub(r"\1 \2 UTC", str(value or ""))


def fmt_money(value: float | None, currency: str) -> str:
    return "" if value is None else f"{currency} {value:,.0f}"


def headline(a: Analysis) -> str:
    t = a.totals
    seats = (
        f"{t.unassigned_seats} unassigned seat(s) and {t.dormant_seats} seat(s) held by "
        f"{t.dormant_users} dormant user(s)"
    )
    if t.unassigned_seats + t.dormant_seats == 0:
        return "No unassigned seats and no dormant license holders found."
    if t.priced_pools == 0:
        return f"{seats}. Add contract prices to settings.yaml to see amounts."
    line = f"Up to {fmt_money(t.annual_total, a.settings.currency)} a year: {seats}."
    if t.unpriced_pools_with_waste:
        line += f" {t.unpriced_pools_with_waste} license(s) with waste have no price yet."
    return line


def render_report(a: Analysis) -> str:
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(time=fmt_time)
    env.globals.update(money=lambda v: fmt_money(v, a.settings.currency))
    return env.get_template("template.html").render(
        a=a,
        headline=headline(a),
        kinds=[(k, [p for p in a.pools if p.kind == k]) for k in PoolKind],
        file_use=FILE_USE,
    )


def dormant_csv(a: Analysis) -> str:
    """Dormant users as CSV, for the admin who will deactivate or reassign them."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        ["username", "name", "user_license", "profile", "last_login", "days_inactive",
         "frozen", "reason", "other_seats", f"annual_cost_{a.settings.currency}"]
    )  # fmt: skip
    for u in a.dormant_users:
        writer.writerow(
            [u.username, u.name, u.user_license, u.profile, fmt_time(u.last_login),
             "" if u.days_inactive is None else u.days_inactive, "yes" if u.frozen else "no",
             u.reason, "; ".join(u.other_seats),
             "" if u.annual_cost is None else f"{u.annual_cost:.2f}"]
        )  # fmt: skip
    return out.getvalue()


def write_outputs(a: Analysis, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "analysis": out_dir / "analysis.json",
        "report": out_dir / "report.html",
        "dormant_users": out_dir / "dormant_users.csv",
    }
    paths["analysis"].write_text(a.model_dump_json(indent=2) + "\n", "utf-8")
    paths["report"].write_text(render_report(a), "utf-8")
    paths["dormant_users"].write_text(dormant_csv(a), "utf-8")
    return paths
