"""Turn a snapshot into license pools, dormant holders and amounts.

Reads only from the snapshot, never from a live connection, so an analysis can be
rerun with different settings (thresholds, prices) without touching the org. Missing
or failed data is reported under `not_collected`; nothing here raises on bad data.

Two kinds of waste are reported:

- Unassigned seats: bought but given to nobody (TotalLicenses - UsedLicenses).
- Dormant seats: held by an active user who has not logged in for a while, or never.
  Frozen users are included, because freezing a user does not release its licenses.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from sflic import __version__
from sflic.models import Analysis, DormantUser, Pool, PoolKind, Settings, Totals
from sflic.snapshot import MANIFEST, MissingDataError, Snapshot, iso_utc, sha256_file, utc_now


def parse_sf_datetime(value: str | None) -> datetime | None:
    """Parse '2026-10-08T18:06:53.000+0000'. None stays None; bad input raises ValueError."""
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class _Reader:
    """Reads snapshot files, recording the ones that are missing instead of raising."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.not_collected: dict[str, str] = {}

    def rows(self, name: str) -> list[dict[str, Any]] | None:
        try:
            data = self.snapshot.soql(name)
        except MissingDataError as e:
            self.not_collected[f"soql/{name}.json"] = str(e)
            return None
        if not isinstance(data, list):
            self.not_collected[f"soql/{name}.json"] = "collected data is not a list of records"
            return None
        return [r for r in data if isinstance(r, dict)]


# (kind, data file, name field, total field)
POOL_SOURCES = [
    (PoolKind.USER, "user_licenses", "Name", "TotalLicenses"),
    (PoolKind.PERMISSION_SET, "permission_set_licenses", "MasterLabel", "TotalLicenses"),
    (PoolKind.PACKAGE, "package_licenses", "NamespacePrefix", "AllowedLicenses"),
]


def build_pools(reader: _Reader, settings: Settings) -> tuple[list[Pool], list[str]]:
    """Pools worth reporting, and warnings about settings that match no license.

    Left out: licenses in `ignore_licenses`, and pools with no purchasable seat count
    (total 0 or less; a managed package reports -1 for a site-wide license).
    """
    ignored = set(settings.ignore_licenses)
    pools: list[Pool] = []
    seen: dict[PoolKind, set[str]] = {}
    for kind, file, name_field, total_field in POOL_SOURCES:
        rows = reader.rows(file)
        if rows is None:
            continue
        seen[kind] = set()
        for row in rows:
            name = str(row.get(name_field) or row.get("Id") or "")
            seen[kind].add(name)
            total, used = _int(row.get(total_field)), _int(row.get("UsedLicenses"))
            if name in ignored or total <= 0:
                continue
            pools.append(
                Pool(
                    kind=kind,
                    id=str(row.get("Id") or ""),
                    name=name,
                    status=str(row.get("Status") or ""),
                    expires=str(row.get("ExpirationDate") or ""),
                    total=total,
                    used=used,
                    unassigned=max(0, total - used),
                    monthly_price=settings.prices.for_kind(kind).get(name),
                )
            )

    warnings = []
    for kind, names in seen.items():
        for priced in sorted(set(settings.prices.for_kind(kind)) - names):
            warnings.append(
                f"prices.{kind.value} has a price for {priced!r}, "
                f"but the org has no {kind.label.lower()} with that name"
            )
    if len(seen) == len(POOL_SOURCES):  # only meaningful when every list was collected
        everything = set().union(*seen.values())
        for name in sorted(ignored - everything):
            warnings.append(f"ignore_licenses names {name!r}, but the org has no such license")
    return pools, warnings


def dormancy(
    user: dict[str, Any], now: datetime, settings: Settings
) -> tuple[str, int | None] | None:
    """(reason, days since last login) when the user is dormant, else None.

    Raises ValueError on an unreadable date; the caller turns that into a warning.
    """
    last = parse_sf_datetime(user.get("LastLoginDate"))
    if last is not None:
        days = (now - last).days
        if days > settings.inactive_days:
            return f"no login for {days} days", days
        return None
    created = parse_sf_datetime(user.get("CreatedDate"))
    if created is None:
        return None
    age = (now - created).days
    if age > settings.never_logged_in_grace_days:
        return f"never logged in, created {age} days ago", None
    return None


def _seats_by_user(
    rows: list[dict[str, Any]] | None, user_field: str, pool_field: str
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in rows or []:
        if row.get(user_field) and row.get(pool_field):
            out.setdefault(row[user_field], []).append(row[pool_field])
    return out


def find_dormant(
    reader: _Reader, pools: list[Pool], settings: Settings, now: datetime
) -> tuple[list[DormantUser], list[str]]:
    """Dormant users holding at least one reported seat. Updates each pool's dormant count."""
    users = reader.rows("active_users")
    if users is None:
        return [], []
    frozen = {r.get("UserId") for r in reader.rows("frozen_users") or []}
    psl = _seats_by_user(reader.rows("psl_assignments"), "AssigneeId", "PermissionSetLicenseId")
    pkg = _seats_by_user(reader.rows("package_license_assignments"), "UserId", "PackageLicenseId")
    by_id = {p.id: p for p in pools}
    excluded_types = set(settings.excluded_user_types)
    excluded_users = {u.lower() for u in settings.excluded_users}
    excluded_licenses = set(settings.excluded_user_licenses)

    dormant: list[DormantUser] = []
    warnings: list[str] = []
    for user in users:
        username = str(user.get("Username") or "")
        profile = user.get("Profile") or {}
        user_license = str((profile.get("UserLicense") or {}).get("Name") or "")
        if (
            user.get("UserType") in excluded_types
            or username.lower() in excluded_users
            or user_license in excluded_licenses
        ):
            continue
        try:
            result = dormancy(user, now, settings)
        except ValueError:
            warnings.append(f"{username}: unreadable login or creation date; not assessed")
            continue
        if result is None:
            continue
        reason, days = result
        seat_ids = [profile.get("UserLicenseId"), *psl.get(user.get("Id"), [])]
        seat_ids += pkg.get(user.get("Id"), [])
        seats = [by_id[i] for i in seat_ids if i in by_id]
        if not seats:
            continue  # holds nothing that is reported (for example only an ignored license)
        for pool in seats:
            pool.dormant += 1
        priced = [p.monthly_price * 12 for p in seats if p.monthly_price is not None]
        dormant.append(
            DormantUser(
                username=username,
                name=str(user.get("Name") or ""),
                user_license=user_license,
                profile=str(profile.get("Name") or ""),
                last_login=user.get("LastLoginDate") or "never",
                days_inactive=days,
                created=str(user.get("CreatedDate") or ""),
                frozen=user.get("Id") in frozen,
                reason=reason,
                other_seats=sorted(p.name for p in seats if p.kind != PoolKind.USER),
                annual_cost=sum(priced) if priced else None,
                unpriced_seats=len(seats) - len(priced),
            )
        )
    dormant.sort(
        key=lambda u: (
            -(u.annual_cost or 0),
            -(u.days_inactive if u.days_inactive is not None else 10**6),
            u.username,
        )
    )
    return dormant, warnings


def totals(pools: list[Pool], dormant: list[DormantUser]) -> Totals:
    priced = [p for p in pools if p.monthly_price is not None]
    return Totals(
        unassigned_seats=sum(p.unassigned for p in pools),
        dormant_users=len(dormant),
        dormant_seats=sum(p.dormant for p in pools),
        annual_unassigned=sum(p.annual(p.unassigned) or 0 for p in priced),
        annual_dormant=sum(p.annual(p.dormant) or 0 for p in priced),
        priced_pools=len(priced),
        unpriced_pools_with_waste=sum(
            1 for p in pools if p.monthly_price is None and p.reclaimable > 0
        ),
    )


def sort_pools(pools: list[Pool]) -> list[Pool]:
    """Largest amount first; pools without a price follow, largest seat count first."""
    return sorted(
        pools,
        key=lambda p: (
            p.monthly_price is None,
            -(p.annual(p.reclaimable) or 0),
            -p.reclaimable,
            p.kind.value,
            p.name,
        ),
    )


def analyze(snapshot_dir: Path, settings: Settings) -> Analysis:
    snapshot = Snapshot(snapshot_dir)
    reader = _Reader(snapshot)
    pools, warnings = build_pools(reader, settings)
    try:
        now = snapshot.collected_at()
    except MissingDataError as e:
        reader.not_collected[MANIFEST] = str(e)
        dormant: list[DormantUser] = []
    else:
        dormant, date_warnings = find_dormant(reader, pools, settings, now)
        warnings += date_warnings
    return Analysis(
        tool_version=__version__,
        analyzed_at=iso_utc(utc_now()),
        snapshot_dir=snapshot.path.name,
        manifest=snapshot.manifest,
        manifest_sha256=sha256_file(snapshot.path / MANIFEST),
        integrity_issues=snapshot.verify(),
        settings=settings,
        pools=sort_pools(pools),
        dormant_users=dormant,
        totals=totals(pools, dormant),
        not_collected=dict(sorted(reader.not_collected.items())),
        warnings=warnings,
    )
