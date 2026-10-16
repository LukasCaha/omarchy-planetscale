#!/usr/bin/env python3
"""Print one JSON line describing a PlanetScale database for the bar plugin."""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.planetscale.com/v1"
TOKEN_PATH = Path.home() / ".config" / "planetscale" / "bar-token"
SQL_LIMIT = 80
DEPLOY_LIMIT = 5
TOKEN_LIMIT = 400

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


def error_payload(message, name, needs_token=False):
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
        "numbers": [],
        "charts": [],
        "needsToken": bool(needs_token),
    }


def load_token(path):
    if not path.is_file():
        raise ConfigError("No service token yet.")
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
        return "Unauthorized. Paste a service token that can read this database."
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


def fetch_metrics(token, org, database, branch, period):
    period = normalize_period(period)
    instant = section(
        token,
        _branch_metrics(org, database, branch, "instant"),
        {"metrics[]": POSTGRES_METRICS},
    )
    tablets_now = section(
        token,
        _branch_metrics(org, database, branch, "tablets-instant"),
        {"metrics[]": VITESS_TABLET_METRICS},
    )
    tablets = section(
        token,
        _branch_metrics(org, database, branch, "tablets"),
        {"metrics[]": list(TABLET_SERIES), "period": period},
    )
    merged = []
    ok = False
    status = None
    if tablets["ok"]:
        ok = True
        merged.extend(window_gauges(tablets["data"]))
    for part in (instant, tablets_now):
        if part["ok"]:
            ok = True
            merged.extend(part["data"].get("metrics") or [])
        elif status is None and not part.get("unsupported"):
            status = part["status"]
    data = {"metrics": merged}
    if tablets["ok"]:
        ok = True
        data["disk_used_bytes"] = _latest_sum(tablets["data"], "shard_storage_usage")
        data["disk_free_bytes"] = _latest_sum(tablets["data"], "shard_storage_available")
    if not ok:
        return {"ok": False, "status": status or 0, "body": "", "data": {}}
    result = {"ok": True, "status": 200, "data": data}
    if tablets["ok"]:
        result["tablets"] = tablets["data"]
    return result


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


def _number(metric_id, group, label, value, pin=""):
    return {
        "id": metric_id,
        "group": group,
        "label": label,
        "value": value,
        "pin": pin,
    }


def _measured(prefix, value):
    if not value or "unavailable" in str(value):
        return ""
    return f"{prefix} {value}"


def branch_metric_rows(metrics_section):
    if not metrics_section["ok"]:
        reason = "unavailable"
        if metrics_section["status"]:
            reason = f"unavailable (HTTP {metrics_section['status']})"
        return [
            _number("connections", "branch", "Connections", reason),
            _number("cpu", "branch", "CPU", reason),
            _number("memory", "branch", "Memory", reason),
            _number("disk", "branch", "Disk", reason),
            _number("lag", "branch", "Replica lag", reason),
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
    disk_pin = ""
    if used is not None or free is not None:
        parts = []
        pin_parts = []
        if used is not None:
            used_text = _format_bytes(used)
            parts.append(f"{used_text} used")
            pin_parts.append(used_text)
        if free is not None:
            free_text = _format_bytes(free)
            parts.append(f"{free_text} free")
            pin_parts.append(free_text)
        disk_line = ", ".join(parts)
        disk_pin = "disk " + "/".join(pin_parts)
    elif disk_name:
        disk_line = _format_number(disk_percent, "%")
        disk_pin = _measured("disk", disk_line)
    else:
        disk_line = "unavailable"
    connection_text = _format_number(connections, "") if connections is not None else "unavailable"
    cpu_text = _format_number(cpu, "%") if cpu_name else "unavailable"
    memory_text = _format_number(memory, "%") if memory_name else "unavailable"
    lag_text = _format_number(lag, "s") if lag_name else "unavailable"
    return [
        _number("connections", "branch", "Connections", connection_text, _measured("conn", connection_text)),
        _number("cpu", "branch", "CPU", cpu_text, _measured("cpu", cpu_text)),
        _number("memory", "branch", "Memory", memory_text, _measured("mem", memory_text)),
        _number("disk", "branch", "Disk", disk_line, disk_pin),
        _number("lag", "branch", "Replica lag", lag_text, _measured("lag", lag_text)),
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


COLLECTIVE_LATENCY = (
    ("p50", "latency_p50", "p50"),
    ("p95", "latency_p95", "p95"),
    ("p99", "latency_p99", "p99"),
    ("p999", "latency_p999", "p99.9"),
    ("max", "latency_max", "max"),
)
QUERY_METRIC_NAMES = [name for _key, name, _label in COLLECTIVE_LATENCY] + ["queries"]
PERIODS = ("15m", "1h", "6h", "1d", "7d")
DEFAULT_PERIOD = "1h"
TABLET_SERIES = (
    "primary_cpu_usage",
    "primary_memory_usage",
    "replication_lag",
    "shard_storage_usage",
    "shard_storage_available",
)


def normalize_period(value):
    text = str(value or "").strip()
    if text in PERIODS:
        return text
    return DEFAULT_PERIOD


def _finished_points(points, interval, now=None):
    usable = []
    for point in points or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2 or point[1] is None:
            continue
        usable.append((point[0], float(point[1])))
    if not usable:
        return []
    current = time.time() if now is None else now
    step = interval or 60
    if len(usable) >= 2 and usable[-1][0] + step > current + 1:
        return usable[:-1]
    return usable


def _combined_metric(payload, name, interval, aggregate, now=None):
    if aggregate == "rate":
        buckets = {}
        for series in (payload or {}).get("series") or []:
            if series.get("metric") != name:
                continue
            for stamp, value in _finished_points(series.get("points"), interval, now):
                buckets[stamp] = buckets.get(stamp, 0) + value
        if not buckets or interval <= 0:
            return None
        return sum(buckets.values()) / (len(buckets) * interval)
    means = []
    for series in (payload or {}).get("series") or []:
        if series.get("metric") != name:
            continue
        points = _finished_points(series.get("points"), interval, now)
        if points:
            means.append(sum(value for _stamp, value in points) / len(points))
    if not means:
        return None
    return max(means)


def window_gauges(payload, now=None):
    interval = (payload or {}).get("interval") or 60
    gauges = []
    for name, role in (
        ("primary_cpu_usage", "primary"),
        ("primary_memory_usage", "primary"),
        ("replication_lag", "replica"),
    ):
        value = _combined_metric(payload, name, interval, "mean", now)
        if value is None:
            continue
        gauges.append({"metric": name, "values": [{"value": value, "role": role}]})
    return gauges


def _rate_text(value):
    if value >= 10:
        return str(int(round(value)))
    if value >= 1:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def collective_rows(section):
    if not section:
        return []
    if not section.get("ok"):
        status = section.get("status") or "error"
        return [_number("p99", "insights", "p99", f"unavailable (HTTP {status})")]
    payload = section.get("data") or {}
    interval = payload.get("interval") or 60
    rows = []
    for key, name, label in COLLECTIVE_LATENCY:
        value = _combined_metric(payload, name, interval, "mean")
        text = format_latency(value) if value is not None else "unavailable"
        rows.append({
            "id": key,
            "group": "insights",
            "label": label,
            "value": text,
            "pin": f"{label} {text}" if value is not None else "",
        })
    per_second = _combined_metric(payload, "queries", interval, "rate")
    if per_second is None:
        rows.append(_number("qps", "insights", "QPS", "unavailable"))
        rows.append(_number("qpm", "insights", "QPM", "unavailable"))
        return rows
    per_minute = per_second * 60
    qps = _rate_text(per_second)
    qpm = format_count(per_minute)
    rows.append(_number("qps", "insights", "QPS", f"{qps}/s", f"qps {qps}/s"))
    rows.append(_number("qpm", "insights", "QPM", f"{qpm}/min", f"qpm {qpm}/min"))
    return rows


def query_report(insights):
    if not insights["ok"]:
        status = insights["status"] or "error"
        summary = f"unavailable (HTTP {status})"
        return {"summary": summary, "detail": "", "row": _number("errors", "queries", "Errors", summary)}
    rows = [
        row for row in ((insights["data"] or {}).get("data") or [])
        if (row.get("error_count") or 0) > 0
    ]
    if not rows:
        return {
            "summary": "none",
            "detail": "",
            "row": _number("errors", "queries", "Errors", "none", "errors 0"),
        }
    worst = max(rows, key=lambda row: row.get("error_count") or 0)
    noun = "fingerprint" if len(rows) == 1 else "fingerprints"
    sql = _one_line(worst.get("normalized_sql") or worst.get("fingerprint") or "")
    summary = f"{len(rows)} {noun}"
    detail = ""
    if sql:
        detail = f"{int(worst.get('error_count') or 0)} errors · {sql}"
    return {
        "summary": summary,
        "detail": detail,
        "row": _number("errors", "queries", "Errors", summary, f"errors {len(rows)}"),
    }


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


CHART_POINTS = 48


def _series_points(payload, name, interval, combine, now=None):
    buckets = {}
    for series in (payload or {}).get("series") or []:
        if series.get("metric") != name:
            continue
        for stamp, value in _finished_points(series.get("points"), interval, now):
            if stamp not in buckets:
                buckets[stamp] = value
            elif combine == "sum":
                buckets[stamp] += value
            else:
                buckets[stamp] = max(buckets[stamp], value)
    return [(stamp, buckets[stamp]) for stamp in sorted(buckets)]


def _chart_number(value):
    number = float(value)
    if abs(number) >= 100:
        return int(round(number))
    if abs(number) >= 10:
        return round(number, 1)
    return round(number, 2)


def _downsample(values, limit):
    count = len(values)
    if count <= limit:
        return values
    reduced = []
    for index in range(limit):
        start = int(index * count / limit)
        end = int((index + 1) * count / limit)
        chunk = [value for value in values[start:end] if value is not None]
        if not chunk:
            reduced.append(None)
        else:
            reduced.append(sum(chunk) / len(chunk))
    return reduced


def _chart(chart_id, label, unit, scale, payload, specs, transform=None, now=None):
    if not payload:
        return None
    interval = payload.get("interval") or 60
    raw = []
    for series_id, metric, series_label, combine in specs:
        points = _series_points(payload, metric, interval, combine, now)
        if transform == "rate" and interval > 0:
            points = [(stamp, value / interval) for stamp, value in points]
        if points:
            raw.append((series_id, series_label, points))
    if not raw:
        return None
    stamps = sorted({stamp for _series_id, _label, points in raw for stamp, _value in points})
    series = []
    for series_id, series_label, points in raw:
        lookup = dict(points)
        values = _downsample([lookup.get(stamp) for stamp in stamps], CHART_POINTS)
        numbers = []
        kept = False
        for value in values:
            if value is None:
                numbers.append(None)
            else:
                numbers.append(_chart_number(value))
                kept = True
        if kept:
            series.append({"id": series_id, "label": series_label, "points": numbers})
    if not series:
        return None
    return {"id": chart_id, "label": label, "unit": unit, "scale": scale, "series": series}


def build_charts(query_payload, tablets_payload, now=None):
    charts = []
    latency = _chart(
        "latency",
        "Latency",
        "ms",
        "auto",
        query_payload,
        (
            ("p50", "latency_p50", "p50", "max"),
            ("p95", "latency_p95", "p95", "max"),
            ("p99", "latency_p99", "p99", "max"),
        ),
        now=now,
    )
    queries = _chart(
        "queries",
        "Queries",
        "/s",
        "auto",
        query_payload,
        (("qps", "queries", "QPS", "sum"),),
        transform="rate",
        now=now,
    )
    host = _chart(
        "host",
        "Host",
        "%",
        "percent",
        tablets_payload,
        (
            ("memory", "primary_memory_usage", "Memory", "max"),
            ("cpu", "primary_cpu_usage", "CPU", "max"),
        ),
        now=now,
    )
    for chart in (latency, queries, host):
        if chart:
            charts.append(chart)
    return charts


def dashboard_url(database, org, name):
    html = str(database.get("html_url") or "")
    if html.startswith("https://"):
        return html
    return f"https://app.planetscale.com/{urllib.parse.quote(org)}/{urllib.parse.quote(name)}"


def compose(database, deploys, metrics, insights, org, branch, query_metrics=None, period=DEFAULT_PERIOD):
    name = str(database.get("name") or "")
    state = state_lines(database)
    deploy = deploy_lines(deploys)
    branch_rows = branch_metric_rows(metrics)
    metric = [f"Branch {branch}"] + [f"{row['label']}: {row['value']}" for row in branch_rows]
    query = query_report(insights)
    collective = collective_rows(query_metrics)
    query_payload = query_metrics.get("data") if query_metrics and query_metrics.get("ok") else None
    tablets_payload = metrics.get("tablets") if isinstance(metrics, dict) else None
    charts = build_charts(query_payload, tablets_payload)
    schema = None
    for line in state:
        if line.startswith("Schema recommendations: "):
            schema = line.split(": ", 1)[1]
    numbers = []
    if schema is not None:
        numbers.append(_number("schema", "state", "Schema", schema, f"schema {schema}"))
    numbers.extend(collective)
    numbers.extend(branch_rows)
    numbers.append(query["row"])
    insight_lines_out = []
    if collective:
        insight_lines_out = ["Insights"] + [
            f"{row['label']}: {row['value']}" for row in collective
        ]
    query_lines = [f"Query errors: {query['summary']}"]
    if query["detail"]:
        query_lines.append(query["detail"])
    blocks = [state, deploy, metric, query_lines]
    if insight_lines_out:
        blocks.append(insight_lines_out)
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
        "metrics": [{"label": row["label"], "value": row["value"]} for row in branch_rows],
        "querySummary": query["summary"],
        "queryDetail": query["detail"],
        "insightRows": collective,
        "numbers": numbers,
        "charts": charts,
        "needsToken": False,
        "period": normalize_period(period),
    }


def collect(token, org, database, branch, period=DEFAULT_PERIOD):
    period = normalize_period(period)
    path = (
        f"/organizations/{urllib.parse.quote(org)}"
        f"/databases/{urllib.parse.quote(database)}"
    )
    try:
        payload = request(token, path)
    except ApiError as exc:
        return error_payload(
            database_error(exc.status),
            database,
            needs_token=exc.status in (401, 403),
        )
    branch_path = path + f"/branches/{urllib.parse.quote(branch)}"
    deploys = section(token, path + "/deploy-requests", {"state": "open", "per_page": 25})
    metrics = fetch_metrics(token, org, database, branch, period)
    insights = section(
        token,
        branch_path + "/insights",
        {"sort": "errorCount", "dir": "desc", "per_page": 25, "period": period},
    )
    query_metrics = section(
        token,
        branch_path + "/metrics",
        {"metrics[]": QUERY_METRIC_NAMES, "period": period},
    )
    return compose(payload, deploys, metrics, insights, org, branch, query_metrics, period)


def save_token(path, raw):
    line = str(raw or "").strip()
    if not line:
        raise ConfigError("Paste a service token first.")
    if any(character.isspace() for character in line) or len(line) > TOKEN_LIMIT:
        raise ConfigError("Paste one line, TOKEN_ID:TOKEN, with no spaces.")
    token_id, separator, secret = line.partition(":")
    if not separator or not token_id or not secret:
        raise ConfigError("Paste one line, TOKEN_ID:TOKEN, with no spaces.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(line + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def main(argv):
    saving = len(argv) == 2 and argv[1] == "--save-token"
    if saving:
        path = Path(os.environ.get("PS_PLUGIN_TOKEN_FILE", TOKEN_PATH))
        raw = os.environ.pop("PS_PLUGIN_TOKEN_VALUE", "")
        try:
            save_token(path, raw)
        except ConfigError as exc:
            emit({"ok": False, "message": str(exc)})
            return 0
        emit({"ok": True, "message": "Saved the service token."})
        return 0
    if len(argv) not in (4, 5):
        emit(error_payload("Usage: fetch.py ORG DATABASE BRANCH [PERIOD]", "PlanetScale"))
        return 0
    org, database, branch = argv[1], argv[2], argv[3]
    period = normalize_period(argv[4] if len(argv) == 5 else DEFAULT_PERIOD)
    label = database or "PlanetScale"
    if not org or not database:
        message = "Set organization and database on the plugin entry in ~/.config/omarchy/shell.json."
        emit(error_payload(message, label))
        return 0
    if not branch:
        branch = "main"
    try:
        token = load_token(Path(os.environ.get("PS_PLUGIN_TOKEN_FILE", TOKEN_PATH)))
    except ConfigError as exc:
        emit(error_payload(str(exc), label, needs_token=True))
        return 0
    emit(collect(token, org, database, branch, period))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
