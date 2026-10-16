# PlanetScale bar plugin

PlanetScale logo for the [Omarchy](https://omarchy.org) bar. Click the logo to open a panel with:

- database state, deploy requests, and branch metrics
- branch-wide latency (p50, p95, p99, p99.9, max) and query rate (QPS and QPM) for the last full minute
- query errors
- a **Copy host, user, and password** action that puts a `.env` block on the clipboard

Right-click opens the database in the browser. Middle-click refreshes. In the panel, `r` refreshes, `c` copies credentials, `o` opens the browser, and `Esc` closes it.

## Install

```bash
omarchy plugin add https://github.com/LukasCaha/omarchy-planetscale --enable
```

`barMetric` is the number after the logo: `p50`, `p95`, `p99`, `p999`, `max`, `qps`, or `qpm`.

The plugin id is `lukascaha.planetscale`. Set your database on that entry in `~/.config/omarchy/shell.json`:

```json
{
  "id": "lukascaha.planetscale",
  "organization": "your-org",
  "database": "your-database",
  "branch": "main",
  "refreshIntervalSec": 60,
  "barMetric": "p99"
}
```

## Service token

The token stays out of this repo. Create `~/.config/planetscale/bar-token` with one line, `TOKEN_ID:TOKEN`, then run `chmod 600` on that file.

The token needs `read_database`, `read_branch`, and `read_deploy_request`. Copying credentials also needs `connect_production_branch` or `connect_branch`, because PlanetScale only returns a password when it is created.

The first copy creates a `readwriter` password named `omarchy-bar` and stores it in `~/.config/planetscale/bar-credentials.json` (mode 600). Later copies reuse that file. PlanetScale will not show the password again. Delete the file to create a new one.

## Development

```bash
python3 test_fetch.py
omarchy plugin validate .
```
