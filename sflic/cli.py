"""sflic command line: collect, analyze, run."""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from sflic.analyze import analyze
from sflic.connect import ConnectError, get_session
from sflic.models import Analysis, Settings
from sflic.queries import DATA
from sflic.report.render import fmt_money, headline, write_outputs
from sflic.snapshot import SnapshotError

app = typer.Typer(help="Read-only Salesforce license waste finder.")

OrgOpt = Annotated[str, typer.Option("--org", help="Salesforce CLI org alias or username.")]
SettingsOpt = Annotated[
    Path | None,
    typer.Option("--settings", exists=True, dir_okay=False, help="settings.yaml for the client."),
]
SnapshotArg = Annotated[
    Path, typer.Argument(exists=True, file_okay=False, help="Snapshot directory.")
]
SnapshotsOpt = Annotated[Path, typer.Option("--snapshots", help="Where snapshots are written.")]
ReportsOpt = Annotated[Path, typer.Option("--reports", help="Where reports go.")]


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def load_settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    try:
        return Settings(**(yaml.safe_load(path.read_text("utf-8")) or {}))
    except (ValidationError, TypeError, yaml.YAMLError) as e:
        raise ValueError(f"{path}: {e}") from e


def _do_collect(org: str, snapshots: Path) -> Path:
    from sflic.collect import SfCliClient, collect

    session = get_session(org)
    typer.echo(f"Collecting from {session.username} ({session.org_id}) …")
    path = collect(session, SfCliClient(org), DATA, snapshots)
    typer.echo(f"Snapshot: {path}")
    return path


def _do_analyze(snapshot: Path, settings: Settings, reports: Path) -> Analysis:
    result = analyze(snapshot, settings)
    paths = write_outputs(result, reports / snapshot.resolve().name)
    typer.echo(headline(result))
    for pool in result.pools[:5]:
        if pool.reclaimable:
            amount = fmt_money(pool.annual(pool.reclaimable), settings.currency)
            typer.echo(
                f"  {pool.name}: {pool.unassigned} unassigned, {pool.dormant} dormant"
                + (f" ({amount} a year)" if amount else "")
            )
    for file, reason in result.not_collected.items():
        typer.secho(f"warning: {file} not collected: {reason}", fg=typer.colors.YELLOW, err=True)
    for warning in result.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)
    if result.integrity_issues:
        typer.secho("warning: snapshot integrity check failed", fg=typer.colors.YELLOW, err=True)
    typer.echo(f"Report: {paths['report']}")
    typer.echo(f"Dormant users: {paths['dormant_users']}")
    return result


@app.command("collect")
def collect_cmd(org: OrgOpt, snapshots: SnapshotsOpt = Path("snapshots")) -> None:
    """Query the org (read-only) and write a hashed snapshot."""
    try:
        _do_collect(org, snapshots)
    except ConnectError as e:
        _fail(str(e))


@app.command("analyze")
def analyze_cmd(
    snapshot: SnapshotArg,
    settings: SettingsOpt = None,
    reports: ReportsOpt = Path("reports"),
) -> None:
    """Analyze a snapshot (offline) and write analysis.json, report.html and dormant_users.csv."""
    try:
        _do_analyze(snapshot, load_settings(settings), reports)
    except (SnapshotError, ValueError) as e:
        _fail(str(e))


@app.command("run")
def run_cmd(
    org: OrgOpt,
    settings: SettingsOpt = None,
    snapshots: SnapshotsOpt = Path("snapshots"),
    reports: ReportsOpt = Path("reports"),
) -> None:
    """collect + analyze in one go."""
    try:
        loaded = load_settings(settings)  # fail before collecting
        _do_analyze(_do_collect(org, snapshots), loaded, reports)
    except (ConnectError, SnapshotError, ValueError) as e:
        _fail(str(e))


if __name__ == "__main__":
    app()
