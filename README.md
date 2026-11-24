# sflic — Salesforce license waste finder

A local Python CLI that reads a Salesforce org's license data, read-only, and reports
where license money is wasted:

- **Unassigned seats**: bought but given to nobody.
- **Dormant seats**: held by an active user who has not logged in for a while, or
  never. Frozen users count, because freezing does not release a license.

With the client's contract prices in `settings.yaml`, the report shows the amount per
year. Without prices it shows seat counts.

It covers user licenses, permission set licenses and managed-package seats.

## Safe by design

- **Read-only.** sflic only runs SOQL queries. It never creates, updates or deletes
  anything in the org, and it reports dormant users without deactivating them.
- **Data stays on your machine.** Snapshots and reports are written to local folders.
  Nothing is sent anywhere except the queries to the Salesforce org itself. No
  telemetry.
- **No stored credentials.** Login is handled by the Salesforce CLI; sflic never sees
  or saves the access token.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli) (`sf`),
  logged in to the org. So far tested with a System Administrator user.

## Use

```bash
git clone https://github.com/porwiszek/sfdc_license_usage.git
cd sfdc_license_usage
uv sync                                    # install
sf org login web --alias client-org        # authenticate (once)
cp settings.example.yaml settings.yaml     # then add the client's prices
uv run sflic run --org client-org --settings settings.yaml
```

Output, under `reports/<snapshot>/`:

- `report.html`: summary, licenses, dormant users, method, evidence appendix
- `dormant_users.csv`: the list for the admin who will act on it
- `analysis.json`: the same data for other tools

The steps can also be run separately. The analysis is offline, so thresholds and
prices can be changed and the analysis rerun without touching the org again:

```bash
uv run sflic collect --org client-org
uv run sflic analyze snapshots/<dir> --settings settings.yaml
```

## How it works

```
 sf CLI ──▶ connect ──▶ collect ──▶ analyze ──▶ report
                        writes       reads       HTML, CSV, JSON
                        snapshot     snapshot
```

The sf CLI owns the session; sflic never reads or stores the token. Each run is kept
as a timestamped snapshot with a SHA-256 hash for every data file.

## Settings

`settings.example.yaml` documents every option. Copy it to `settings.yaml`, which is
gitignored because it holds client prices.

| Setting | Meaning |
|---|---|
| `inactive_days` | Days without a login before a user counts as dormant (default 90). |
| `never_logged_in_grace_days` | A user who never logged in is dormant once created this many days ago (default 30). |
| `excluded_user_types` | User types that are not people, such as integration, automated process, guest and Chatter-only system users. |
| `excluded_user_licenses` | User licenses whose users are not people, such as Einstein agent and Analytics integration users. Their licenses are still reported. |
| `excluded_users` | Usernames to leave out, for example integration users that rarely log in by design. |
| `ignore_licenses` | Free or bundled licenses to leave out of the report entirely. |
| `prices` | Monthly price per seat from the contract, per user license, permission set license and package namespace. There are no built-in prices. |

## Status

Early version (0.1.0). All queries in `sflic/queries.py` have been run successfully on
Developer Edition orgs, but not yet on a production org. If a query fails on your org,
it is recorded in the snapshot manifest and shown as "not collected" in the report,
and the rest of the run continues. Please open an issue with the error message.

## Limitations

- Dormancy uses the last login date. A user who logs in but never uses a product they
  hold a seat for is not detected.
- Feature licenses on the user record (Marketing User, Service Cloud User and similar)
  are not assessed.
- The sf CLI stops a query at its record limit (`org-max-query-limit`). sflic treats an
  incomplete result as a failed query, so a very large org reports "not collected" for
  users rather than a wrong number. Raise the limit in the sf CLI for such orgs.
- Prices are whatever is in `settings.yaml`. Contract minimums and bundles are not
  known to the tool.

## Development

```bash
uv run pytest                              # tests, no org needed
uv run ruff check . && uv run ruff format .
```

`sflic/connect.py`, `sflic/snapshot.py` and the client in `sflic/collect.py` are copied
from sfgrc 0.1.0, a separate Salesforce security assessment tool by the same author,
so that sflic stands alone. They are deliberately left unchanged
apart from the package name: a fix in one project can be carried to the other with a
diff. The one real difference is `collect()`, which takes a query declaration instead
of a list of checks.

## License

MIT. See [LICENSE](LICENSE).
