#!/usr/bin/env python3
"""Print one JSON line describing a PlanetScale database for the bar plugin."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.planetscale.com/v1"
TOKEN_PATH = Path.home() / ".config" / "planetscale" / "bar-token"
CREDENTIAL_PATH = Path.home() / ".config" / "planetscale" / "bar-credentials.json"
PASSWORD_NAME = "omarchy-bar"
PASSWORD_ROLE = "readwriter"
SQL_LIMIT = 80
DEPLOY_LIMIT = 5

POSTGRES_METRICS = [
    "planetscale_pods_cpu_util_percentages",
    "planetscale_pods_mem_util_percentages",
    "planetscale_volume_usage_percentage",
    "planetscale_postgres_connection_state",
    "planetscale_edge_postgres_active_connections",
    "planetscale_edge_active_connections",
    "planetscale_postgres_replica_lag_seconds",
    "planetscale_replica_lag_seconds",
    "connections",
]
VITESS_TABLET_METRICS = [
    "primary_cpu_usage",
    "primary_memory_usage",
    "replication_lag",
]
CPU_METRICS = ("planetscale_pods_cpu_util_percentages", "primary_cpu_usage")
MEMORY_METRICS = ("planetscale_pods_mem_util_percentages", "primary_memory_usage")
LAG_METRICS = (
    "planetscale_postgres_replica_lag_seconds",
    "planetscale_replica_lag_seconds",
    "replication_lag",
)
DISK_PERCENT_METRICS = ("planetscale_volume_usage_percentage",)

IN_PROGRESS = {
    "queued",
    "submitting",
    "in_progress",
    "pending_cutover",
    "in_progress_vschema",
    "in_progress_cancel",
    "in_progress_cutover",
    "in_progress_revert",
    "in_progress_revert_vschema",
    "complete_pending_revert",
}
DEPLOY_ERRORS = {"error", "complete_error", "complete_revert_error"}
WARN_STATES = {
    "sleeping",
    "awakening",
    "importing",
    "pending",
    "import_ready",
    "sleep_in_progress",
}


class ConfigError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, body, path):
        super().__init__(f"HTTP {status} for {path}")
        self.status = status
        self.body = body
        self.path = path


def emit(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def error_payload(message, name):
    return {
        "name": name or "PlanetScale",
        "tone": "bad",
        "tooltip": message,
        "url": "",
        "error": message,
        "state": "",
        "flags": "",
        "schemaRecommendations": None,
        "branch": "",
        "deployLines": [],
        "metrics": [],
        "querySummary": "",
        "queryDetail": "",
        "insightRows": [],
    }


def load_token(path):
    if not path.is_file():
        raise ConfigError(
            "No service token. Put TOKEN_ID:TOKEN in "
            f"{path} and run chmod 600 on that file. The token needs "
            "read_database, read_branch, and read_deploy_request."
        )
    mode = path.stat().st_mode
    if mode & 0o077:
        raise ConfigError(
            f"{path} is readable by other users. Run: chmod 600 {path}"
        )
    line = path.read_text(encoding="utf-8").strip()
    token_id, separator, secret = line.partition(":")
    if not separator or not token_id or not secret:
        raise ConfigError(f"{path} must be one line: TOKEN_ID:TOKEN")
    return line


def request(token, path, query=None, body=None):
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "ps-plugin",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise ApiError(exc.code, body, path) from None
    except urllib.error.URLError as exc:
        raise ApiError(0, str(exc.reason), path) from None


def database_error(status):
    if status in (401, 403):
        return (
            "Unauthorized. The service token in "
            f"{TOKEN_PATH} needs read_database on this database."
        )
    if status == 404:
        return "Database not found."
    if status == 0:
        return "PlanetScale API is unreachable."
    return f"PlanetScale API returned HTTP {status}."


def _unsupported(status, body):
    if status in (404, 405, 422):
        return True
    text = (body or "").lower()
    return "not supported" in text or "not available" in text


def section(token, path, query=None):
    try:
        return {"ok": True, "status": 200, "data": request(token, path, query)}
    except ApiError as exc:
        return {
            "ok": False,
            "status": exc.status,
            "body": exc.body,
            "unsupported": _unsupported(exc.status, exc.body),
        }


def _branch_metrics(org, database, branch, suffix):
    return (
        f"/organizations/{urllib.parse.quote(org)}"
        f"/databases/{urllib.parse.quote(database)}"
        f"/branches/{urllib.parse.quote(branch)}/metrics/{suffix}"
    )


def _latest_sum(payload, name):
    total = None
    for series in (payload or {}).get("series") or []:
        if series.get("metric") != name:
            continue
        points = series.get("points") or []
        if not points or len(points[-1]) < 2 or points[-1][1] is None:
            continue
        total = (total or 0) + points[-1][1]
    return total


def fetch_metrics(token, org, database, branch):
    instant = section(
        token,
        _branch_metrics(org, database, branch, "instant"),
        {"metrics[]": POSTGRES_METRICS},
    )
    tablets = section(
        token,
        _branch_metrics(org, database, branch, "tablets-instant"),
        {"metrics[]": VITESS_TABLET_METRICS},
    )
    storage = section(
        token,
        _branch_metrics(org, database, branch, "tablets"),
        {"metrics[]": ["shard_storage_usage", "shard_storage_available"], "period": "15m"},
    )
    merged = []
    ok = False
    status = None
    for part in (instant, tablets):
        if part["ok"]:
            ok = True
            merged.extend(part["data"].get("metrics") or [])
        elif status is None and not part.get("unsupported"):
            status = part["status"]
    data = {"metrics": merged}
    if storage["ok"]:
        ok = True
        data["disk_used_bytes"] = _latest_sum(storage["data"], "shard_storage_usage")
        data["disk_free_bytes"] = _latest_sum(storage["data"], "shard_storage_available")
    if not ok:
        return {"ok": False, "status": status or 0, "body": "", "data": {}}
    return {"ok": True, "status": 200, "data": data}


def _values(metric):
    return [item for item in (metric or {}).get("values") or [] if "value" in item]


def _preferred(values):
    primary = [item["value"] for item in values if item.get("role") == "primary"]
    if primary:
        return primary
    return [item["value"] for item in values]


def _metric_map(payload):
    found = {}
    for metric in (payload or {}).get("metrics") or []:
        name = metric.get("metric")
        if name and name not in found:
            found[name] = metric
    return found


def _average(numbers):
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _format_number(value, suffix):
    if value is None:
        return "unavailable"
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 0.05:
        text = str(int(round(rounded)))
    else:
        text = f"{rounded:.1f}"
    return text + suffix


def _connection_count(found):
    for name in (
        "planetscale_edge_active_connections",
        "planetscale_edge_postgres_active_connections",
        "connections",
    ):
        metric = found.get(name)
        numbers = _preferred(_values(metric)) if metric else []
        if numbers:
            return sum(numbers)
    state = found.get("planetscale_postgres_connection_state")
    numbers = _preferred(_values(state)) if state else []
    if numbers:
        return sum(numbers)
    return None


def _format_bytes(value):
    number = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if abs(number) < 1024 or unit == units[-1]:
            if unit == "B" or abs(number) >= 10:
                return f"{int(round(number))} {unit}"
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{int(round(number))} B"


def _first_metric(found, names):
    for name in names:
        if name in found:
            return name, found[name]
    return None, None


def _lag_seconds(found):
    for name in LAG_METRICS:
        metric = found.get(name)
        if not metric:
            continue
        values = _values(metric)
        replicas = [
            item["value"] for item in values
            if item.get("role") not in (None, "", "primary")
        ]
        numbers = replicas or [item["value"] for item in values]
        if numbers:
            return max(numbers)
    return None


def metric_lines(branch, metrics_section):
    header = f"Branch {branch}"
    if not metrics_section["ok"]:
        reason = "unavailable"
        if metrics_section["status"]:
            reason = f"unavailable (HTTP {metrics_section['status']})"
        return [
            header,
            f"Connections: {reason}",
            f"CPU: {reason}",
            f"Memory: {reason}",
            f"Disk: {reason}",
            f"Replica lag: {reason}",
        ]
    payload = metrics_section["data"] or {}
    found = _metric_map(payload)
    cpu_name, cpu_metric = _first_metric(found, CPU_METRICS)
    memory_name, memory_metric = _first_metric(found, MEMORY_METRICS)
    disk_name, disk_metric = _first_metric(found, DISK_PERCENT_METRICS)
    lag_name, _lag_metric = _first_metric(found, LAG_METRICS)
    cpu = _average(_preferred(_values(cpu_metric))) if cpu_name else None
    memory = _average(_preferred(_values(memory_metric))) if memory_name else None
    disk_percent = _average(_preferred(_values(disk_metric))) if disk_name else None
    connections = _connection_count(found)
    lag = _lag_seconds(found) if lag_name else None
    used = payload.get("disk_used_bytes")
    free = payload.get("disk_free_bytes")
    if used is not None or free is not None:
        parts = []
        if used is not None:
            parts.append(f"{_format_bytes(used)} used")
        if free is not None:
            parts.append(f"{_format_bytes(free)} free")
        disk_line = ", ".join(parts)
    elif disk_name:
        disk_line = _format_number(disk_percent, "%")
    else:
        disk_line = "unavailable"
    return [
        header,
        "Connections: " + (_format_number(connections, "") if connections is not None else "unavailable"),
        "CPU: " + (_format_number(cpu, "%") if cpu_name else "unavailable"),
        "Memory: " + (_format_number(memory, "%") if memory_name else "unavailable"),
        "Disk: " + disk_line,
        "Replica lag: " + (_format_number(lag, "s") if lag_name else "unavailable"),
    ]


def _one_line(text, limit=SQL_LIMIT):
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def deploy_lines(deploys):
    if not deploys["ok"] and deploys.get("unsupported"):
        return ["Deploys", "Schema deploys are not available for this database"]
    if not deploys["ok"]:
        status = deploys["status"] or "error"
        return ["Deploys", f"Unavailable (HTTP {status})"]
    items = list((deploys["data"] or {}).get("data") or [])
    if not items:
        return ["Deploys", "None open"]
    lines = ["Deploys"]
    shown = items[:DEPLOY_LIMIT]
    for item in shown:
        number = item.get("number")
        label = f"#{number}" if number is not None else "request"
        source = item.get("branch") or "?"
        target = item.get("into_branch") or "?"
        state = str(item.get("deployment_state") or item.get("state") or "open").replace("_", " ")
        lines.append(f"{label} {source} → {target} · {state}")
    extra = len(items) - len(shown)
    total = (deploys["data"] or {}).get("total_count")
    if isinstance(total, int) and total > len(items):
        extra = total - len(shown)
    if extra > 0:
        lines.append(f"and {extra} more")
    return lines


def format_latency(value):
    if value is None or value == "":
        return ""
    ms = float(value)
    if ms >= 1000:
        seconds = ms / 1000
        if seconds >= 10:
            return f"{int(round(seconds))}s"
        return f"{seconds:.1f}s"
    if ms >= 100:
        return f"{int(round(ms))}ms"
    if ms >= 1:
        text = f"{ms:.1f}".rstrip("0").rstrip(".")
        return text + "ms"
    if ms <= 0:
        return "0ms"
    return f"{ms:.2f}ms"


def format_count(value):
    number = float(value)
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"


def latency_rows(latency):
    if not latency:
        return []
    if not latency.get("ok"):
        status = latency.get("status") or "error"
        return [{
            "p99": "unavailable",
            "p50": "",
            "detail": f"HTTP {status}",
            "sql": "",
        }]
    rows = list((latency.get("data") or {}).get("data") or [])
    ranked = sorted(rows, key=lambda row: float(row.get("p99_latency") or 0), reverse=True)
    if not ranked:
        return [{
            "p99": "none",
            "p50": "",
            "detail": "No queries in the last 24 hours",
            "sql": "",
        }]
    shown = []
    for row in ranked[:3]:
        count = row.get("query_count")
        rows_per_query = row.get("rows_read_per_query")
        parts = []
        if count is not None:
            runs = int(count)
            parts.append(f"{runs} run" if runs == 1 else f"{runs} runs")
        if rows_per_query is not None:
            parts.append(f"{format_count(rows_per_query)} rows/query")
        maximum = row.get("max_latency")
        if maximum is not None:
            parts.append(f"max {format_latency(maximum)}")
        shown.append({
            "p99": format_latency(row.get("p99_latency")),
            "p50": format_latency(row.get("p50_latency")),
            "detail": " · ".join(parts),
            "sql": _one_line(row.get("normalized_sql") or row.get("fingerprint") or "", 72),
        })
    return shown


def insight_lines(insights):
    if not insights["ok"]:
        status = insights["status"] or "error"
        return [f"Query errors: unavailable (HTTP {status})"]
    rows = [
        row for row in ((insights["data"] or {}).get("data") or [])
        if (row.get("error_count") or 0) > 0
    ]
    if not rows:
        return ["Query errors: none"]
    worst = max(rows, key=lambda row: row.get("error_count") or 0)
    noun = "fingerprint" if len(rows) == 1 else "fingerprints"
    sql = _one_line(worst.get("normalized_sql") or worst.get("fingerprint") or "")
    lines = [f"Query errors: {len(rows)} {noun}"]
    if sql:
        lines.append(f"{int(worst.get('error_count') or 0)} errors · {sql}")
    return lines


def state_lines(database):
    state = database.get("state") or "unknown"
    lines = [f"State: {state}"]
    flags = []
    if database.get("resizing"):
        flags.append("Resizing")
    if database.get("resize_queued"):
        flags.append("Resize queued")
    if database.get("config_changing"):
        flags.append("Config change in progress")
    if database.get("config_change_queued"):
        flags.append("Config change queued")
    lines.append(", ".join(flags) if flags else "No resize or config change")
    if "open_schema_recommendations_count" in database:
        lines.append(
            f"Schema recommendations: {int(database.get('open_schema_recommendations_count') or 0)}"
        )
    return lines


def tone_for(database, deploys):
    state = str(database.get("state") or "")
    busy = state in WARN_STATES or any(
        database.get(key)
        for key in ("resizing", "resize_queued", "config_changing", "config_change_queued")
    )
    deploy_error = False
    deploy_busy = False
    if deploys.get("ok"):
        for item in (deploys["data"] or {}).get("data") or []:
            deployment = str(item.get("deployment_state") or "")
            if deployment in DEPLOY_ERRORS:
                deploy_error = True
            if deployment in IN_PROGRESS:
                deploy_busy = True
    ready = database.get("ready")
    if deploy_error or (ready is False and state not in WARN_STATES):
        return "bad"
    if busy or deploy_busy:
        return "warn"
    if state == "ready" or ready is True:
        return "ok"
    return "warn" if state in WARN_STATES else "bad"


def dashboard_url(database, org, name):
    html = str(database.get("html_url") or "")
    if html.startswith("https://"):
        return html
    return f"https://app.planetscale.com/{urllib.parse.quote(org)}/{urllib.parse.quote(name)}"


def _labeled_rows(lines):
    rows = []
    for line in lines:
        label, separator, value = line.partition(": ")
        if separator:
            rows.append({"label": label, "value": value})
    return rows


def compose(database, deploys, metrics, insights, org, branch, latency=None):
    name = str(database.get("name") or "")
    state = state_lines(database)
    deploy = deploy_lines(deploys)
    metric = metric_lines(branch, metrics)
    query = insight_lines(insights)
    slow = latency_rows(latency)
    schema = None
    for line in state:
        if line.startswith("Schema recommendations: "):
            schema = line.split(": ", 1)[1]
    latency_lines = []
    if slow:
        latency_lines = ["Insights"]
        for row in slow:
            label = f"p99 {row['p99']}"
            if row["p50"]:
                label += f" · p50 {row['p50']}"
            latency_lines.append(label)
            if row["detail"]:
                latency_lines.append(row["detail"])
            if row["sql"]:
                latency_lines.append(row["sql"])
    blocks = [state, deploy, metric, query]
    if latency_lines:
        blocks.append(latency_lines)
    return {
        "name": name,
        "tone": tone_for(database, deploys),
        "tooltip": "\n\n".join("\n".join(block) for block in blocks),
        "url": dashboard_url(database, org, name),
        "error": None,
        "state": state[0].split(": ", 1)[1] if state else "",
        "flags": state[1] if len(state) > 1 else "",
        "schemaRecommendations": schema,
        "branch": branch,
        "deployLines": deploy[1:],
        "metrics": _labeled_rows(metric[1:]),
        "querySummary": query[0].split(": ", 1)[1] if query else "",
        "queryDetail": query[1] if len(query) > 1 else "",
        "insightRows": slow,
    }


def collect(token, org, database, branch):
    path = (
        f"/organizations/{urllib.parse.quote(org)}"
        f"/databases/{urllib.parse.quote(database)}"
    )
    try:
        payload = request(token, path)
    except ApiError as exc:
        return error_payload(database_error(exc.status), database)
    branch_path = path + f"/branches/{urllib.parse.quote(branch)}"
    deploys = section(token, path + "/deploy-requests", {"state": "open", "per_page": 25})
    metrics = fetch_metrics(token, org, database, branch)
    insights = section(
        token,
        branch_path + "/insights",
        {"sort": "errorCount", "dir": "desc", "per_page": 25, "period": "1d"},
    )
    latency = section(
        token,
        branch_path + "/insights",
        {"sort": "p99Latency", "dir": "desc", "per_page": 8, "period": "1d"},
    )
    return compose(payload, deploys, metrics, insights, org, branch, latency)


def credential_cache_path():
    return Path(os.environ.get("PS_PLUGIN_CREDENTIAL_FILE", CREDENTIAL_PATH))


def cache_key(org, database, branch):
    return f"{org}/{database}/{branch}"


def read_cache(path):
    if not path.is_file():
        return {}
    if path.stat().st_mode & 0o077:
        raise ConfigError(f"{path} is readable by other users. Run: chmod 600 {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def env_text(database, record):
    host = str(record.get("host") or "")
    user = str(record.get("username") or "")
    password = str(record.get("password") or "")
    name = str(record.get("database") or database)
    if not host or not user or not password:
        raise ConfigError("PlanetScale did not return a host, username, and password.")
    database_url = (
        "mysql://"
        f"{urllib.parse.quote(user, safe='')}:"
        f"{urllib.parse.quote(password, safe='')}@"
        f"{host}/{urllib.parse.quote(name, safe='')}"
        "?ssl=" + urllib.parse.quote('{"rejectUnauthorized":true}', safe="")
    )
    return "\n".join([
        f"DATABASE_HOST={host}",
        f"DATABASE_NAME={name}",
        f"DATABASE_USERNAME={user}",
        f"DATABASE_PASSWORD={password}",
        f"DATABASE_URL={database_url}",
    ]) + "\n"


def record_from_api(database, payload):
    return {
        "host": payload.get("access_host_url") or "",
        "username": payload.get("username") or "",
        "password": payload.get("plain_text") or "",
        "database": database,
        "role": payload.get("role") or PASSWORD_ROLE,
    }


def copy_to_clipboard(text):
    try:
        subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
    except (OSError, subprocess.CalledProcessError):
        raise ConfigError("Could not copy to the clipboard.") from None


def copy_credentials(token, org, database, branch, copier=None):
    copier = copier or copy_to_clipboard
    path = credential_cache_path()
    cache = read_cache(path)
    key = cache_key(org, database, branch)
    stored = cache.get(key)
    record = stored if isinstance(stored, dict) and stored.get("password") else None
    created = False
    if record is None:
        branch_path = (
            f"/organizations/{urllib.parse.quote(org)}"
            f"/databases/{urllib.parse.quote(database)}"
            f"/branches/{urllib.parse.quote(branch)}/passwords"
        )
        try:
            created_payload = request(token, branch_path, body={
                "name": PASSWORD_NAME,
                "role": PASSWORD_ROLE,
            })
        except ApiError as exc:
            if exc.status in (401, 403):
                return {
                    "ok": False,
                    "message": (
                        "Token cannot create a password. Add connect_production_branch "
                        "(or connect_branch) and try again."
                    ),
                }
            return {"ok": False, "message": f"Could not create a password (HTTP {exc.status})."}
        record = record_from_api(database, created_payload)
        if not record["password"]:
            return {
                "ok": False,
                "message": "PlanetScale did not return a password. Create one in the dashboard instead.",
            }
        cache[key] = record
        write_cache(path, cache)
        created = True
    copier(env_text(database, record))
    if created:
        return {
            "ok": True,
            "message": "Copied a new password. Paste it into your .env. It will not be shown again.",
        }
    return {"ok": True, "message": "Copied credentials. Paste them into your .env."}


def main(argv):
    copying = len(argv) == 5 and argv[4] == "--copy-credentials"
    if len(argv) != 4 and not copying:
        emit(error_payload("Usage: fetch.py ORG DATABASE BRANCH", "PlanetScale"))
        return 0
    org, database, branch = argv[1], argv[2], argv[3]
    label = database or "PlanetScale"
    if not org or not database:
        message = "Set organization and database on the plugin entry in ~/.config/omarchy/shell.json."
        emit({"ok": False, "message": message} if copying else error_payload(message, label))
        return 0
    if not branch:
        branch = "main"
    try:
        token = load_token(Path(os.environ.get("PS_PLUGIN_TOKEN_FILE", TOKEN_PATH)))
    except ConfigError as exc:
        emit({"ok": False, "message": str(exc)} if copying else error_payload(str(exc), label))
        return 0
    if copying:
        try:
            emit(copy_credentials(token, org, database, branch))
        except ConfigError as exc:
            emit({"ok": False, "message": str(exc)})
        return 0
    emit(collect(token, org, database, branch))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
