"""Settings and analysis result models."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PoolKind(StrEnum):
    USER = "user"
    PERMISSION_SET = "permission_set"
    PACKAGE = "package"

    @property
    def label(self) -> str:
        return {
            "user": "User license",
            "permission_set": "Permission set license",
            "package": "Managed package",
        }[self.value]


class Prices(BaseModel):
    """Monthly price per seat, keyed by the license name shown in the report.

    Prices come from the client's contract. There are no defaults: list prices change
    and most clients pay a negotiated price. A license without a price is still
    counted; it just has no amount.
    """

    model_config = ConfigDict(extra="forbid")

    user: dict[str, float] = Field(default_factory=dict)
    permission_set: dict[str, float] = Field(default_factory=dict)
    package: dict[str, float] = Field(default_factory=dict)

    def for_kind(self, kind: PoolKind) -> dict[str, float]:
        return getattr(self, kind.value)


class Settings(BaseModel):
    """Per-client configuration from settings.yaml."""

    model_config = ConfigDict(extra="forbid")

    client: str = ""
    currency: str = "EUR"
    # A user is dormant after this many days without a login ...
    inactive_days: int = Field(default=90, ge=1)
    # ... or when it never logged in and was created more than this many days ago.
    never_logged_in_grace_days: int = Field(default=30, ge=0)
    # User types that are not people and are never reported as dormant.
    excluded_user_types: list[str] = Field(
        default_factory=lambda: ["CloudIntegrationUser", "AutomatedProcess", "Guest", "CsnOnly"]
    )
    # User license names whose users are not people (agents, integration users) and are
    # never reported as dormant. Their license pools are still reported.
    excluded_user_licenses: list[str] = Field(
        default_factory=lambda: ["Einstein Agent", "Analytics Cloud Integration User"]
    )
    # Usernames to leave out (for example integration users that log in rarely by design).
    excluded_users: list[str] = Field(default_factory=list)
    # License names to leave out of the report entirely (free or bundled licenses).
    ignore_licenses: list[str] = Field(default_factory=list)
    prices: Prices = Field(default_factory=Prices)


class Pool(BaseModel):
    """One purchasable pool of seats."""

    kind: PoolKind
    id: str
    name: str
    status: str = ""
    expires: str = ""
    total: int
    used: int
    unassigned: int
    dormant: int = 0  # seats held by dormant users
    monthly_price: float | None = None

    @property
    def reclaimable(self) -> int:
        return self.unassigned + self.dormant

    def annual(self, seats: int) -> float | None:
        return None if self.monthly_price is None else seats * self.monthly_price * 12


class DormantUser(BaseModel):
    username: str
    name: str = ""
    user_license: str = ""
    profile: str = ""
    last_login: str  # ISO timestamp or "never"
    days_inactive: int | None  # None when the user never logged in
    created: str = ""
    frozen: bool = False
    reason: str
    other_seats: list[str] = Field(default_factory=list)  # permission set / package licenses
    annual_cost: float | None = None  # priced seats only; None when none has a price
    unpriced_seats: int = 0


class Totals(BaseModel):
    unassigned_seats: int = 0
    dormant_users: int = 0
    dormant_seats: int = 0
    annual_unassigned: float = 0.0
    annual_dormant: float = 0.0
    priced_pools: int = 0
    unpriced_pools_with_waste: int = 0

    @property
    def annual_total(self) -> float:
        return self.annual_unassigned + self.annual_dormant


class Analysis(BaseModel):
    """Output of `sflic analyze`, input of the report."""

    tool_version: str
    analyzed_at: str
    snapshot_dir: str
    manifest: dict[str, Any]
    manifest_sha256: str = ""
    integrity_issues: list[str] = Field(default_factory=list)
    settings: Settings
    pools: list[Pool] = Field(default_factory=list)
    dormant_users: list[DormantUser] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    # data files that could not be read, with the reason
    not_collected: dict[str, str] = Field(default_factory=dict)
    # problems with settings.yaml worth showing, e.g. a price for an unknown license
    warnings: list[str] = Field(default_factory=list)
