import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import fetch


def ready_database(**extra):
    database = {
        "name": "app",
        "state": "ready",
        "ready": True,
        "html_url": "https://app.planetscale.com/acme/app",
        "resizing": False,
        "resize_queued": False,
        "config_changing": False,
        "config_change_queued": False,
        "open_schema_recommendations_count": 0,
    }
    database.update(extra)
    return database


def deploys(items, **extra):
    data = {"data": items}
    data.update(extra)
    return {"ok": True, "status": 200, "data": data}


def metrics(series):
    return {
        "ok": True,
        "status": 200,
        "data": {"metrics": [{"metric": name, "values": values} for name, values in series]},
    }


def insights(rows):
    return {"ok": True, "status": 200, "data": {"data": rows}}


def empty_metrics():
    return metrics([])


def empty_insights():
    return insights([])


class PresenceTests(unittest.TestCase):
    def test_ready_database(self):
        payload = fetch.compose(
            ready_database(),
            deploys([]),
            metrics([
                ("planetscale_pods_cpu_util_percentages", [{"role": "primary", "value": 4}, {"role": "replica", "value": 80}]),
                ("planetscale_pods_mem_util_percentages", [{"role": "primary", "value": 31}]),
                ("planetscale_volume_usage_percentage", [{"role": "primary", "value": 22}]),
                ("connections", [{"value": 12}]),
                ("planetscale_replica_lag_seconds", [{"role": "replica", "value": 0.4}]),
            ]),
            empty_insights(),
            "acme",
            "main",
        )
        self.assertEqual(payload["tone"], "ok")
        self.assertEqual(payload["error"], None)
        self.assertIn("State: ready", payload["tooltip"])
        self.assertIn("No resize or config change", payload["tooltip"])
        self.assertIn("Schema recommendations: 0", payload["tooltip"])
        self.assertIn("None open", payload["tooltip"])
        self.assertIn("Connections: 12", payload["tooltip"])
        self.assertIn("CPU: 4%", payload["tooltip"])
        self.assertNotIn("80%", payload["tooltip"])
        self.assertIn("Replica lag: 0.4s", payload["tooltip"])
        self.assertIn("Query errors: none", payload["tooltip"])
        self.assertEqual(payload["url"], "https://app.planetscale.com/acme/app")

    def test_deploy_in_progress(self):
        payload = fetch.compose(
            ready_database(),
            deploys([{
                "number": 12,
                "branch": "feature",
                "into_branch": "main",
                "state": "open",
                "deployment_state": "in_progress",
            }]),
            empty_metrics(),
            empty_insights(),
            "acme",
            "main",
        )
        self.assertEqual(payload["tone"], "warn")
        self.assertIn("#12 feature → main · in progress", payload["tooltip"])

    def test_deploy_requests_missing_on_postgres(self):
        payload = fetch.compose(
            ready_database(),
            {"ok": False, "status": 404, "unsupported": True, "body": "not found"},
            empty_metrics(),
            empty_insights(),
            "acme",
            "main",
        )
        self.assertEqual(payload["tone"], "ok")
        self.assertIn("Schema deploys are not available for this database", payload["tooltip"])
        self.assertIn("Query errors: none", payload["tooltip"])

    def test_vitess_tablet_metrics(self):
        payload = fetch.compose(
            ready_database(),
            deploys([]),
            {
                "ok": True,
                "status": 200,
                "data": {
                    "metrics": [
                        {"metric": "primary_cpu_usage", "values": [{"value": 36}]},
                        {"metric": "primary_memory_usage", "values": [{"value": 67}]},
                        {"metric": "replication_lag", "values": [{"value": 0}, {"value": 1.2}]},
                    ],
                    "disk_used_bytes": 16,
                    "disk_free_bytes": 54757998592,
                },
            },
            empty_insights(),
            "acme",
            "main",
        )
        self.assertIn("CPU: 36%", payload["tooltip"])
        self.assertIn("Memory: 67%", payload["tooltip"])
        self.assertIn("Replica lag: 1.2s", payload["tooltip"])
        self.assertIn("Disk: 16 B used, 51 GiB free", payload["tooltip"])
        pins = {row["id"]: row["pin"] for row in payload["numbers"]}
        self.assertEqual(pins["cpu"], "cpu 36%")
        self.assertEqual(pins["lag"], "lag 1.2s")
        self.assertEqual(pins["disk"], "disk 16 B/51 GiB")

    def test_partial_metrics(self):
        payload = fetch.compose(
            ready_database(),
            deploys([]),
            metrics([("connections", [{"value": 3}])]),
            empty_insights(),
            "acme",
            "main",
        )
        self.assertIn("Connections: 3", payload["tooltip"])
        self.assertIn("CPU: unavailable", payload["tooltip"])
        self.assertIn("Memory: unavailable", payload["tooltip"])
        self.assertIn("Disk: unavailable", payload["tooltip"])
        self.assertIn("Replica lag: unavailable", payload["tooltip"])

    def test_query_error_fingerprint(self):
        sql = "select " + ("very_long_column_" * 8) + " from users where id = ?"
        payload = fetch.compose(
            ready_database(),
            deploys([]),
            empty_metrics(),
            insights([
                {"error_count": 0, "normalized_sql": "select 1"},
                {"error_count": 4, "normalized_sql": sql},
                {"error_count": 1, "normalized_sql": "select 2"},
            ]),
            "acme",
            "main",
        )
        self.assertEqual(payload["insightRows"], [])
        self.assertIn("Query errors: 2 fingerprints", payload["tooltip"])
        self.assertIn("4 errors · ", payload["tooltip"])
        self.assertIn("…", payload["tooltip"])
        self.assertLess(len(payload["tooltip"].split("4 errors · ", 1)[1]), 90)

    def test_collective_latency_and_query_rate(self):
        now = 1_000_000
        original = fetch.time.time
        fetch.time.time = lambda: now
        try:
            payload = fetch.compose(
                ready_database(),
                deploys([]),
                empty_metrics(),
                empty_insights(),
                "acme",
                "main",
                {
                    "ok": True,
                    "status": 200,
                    "data": {
                        "interval": 60,
                        "series": [
                            {"metric": "latency_p50", "points": [[now - 120, 1.06], [now - 10, 9]]},
                            {"metric": "latency_p95", "points": [[now - 120, 1.69], [now - 10, 9]]},
                            {"metric": "latency_p99", "points": [[now - 120, 2.78], [now - 10, 50]]},
                            {"metric": "latency_p999", "points": [[now - 120, 3.98], [now - 10, 80]]},
                            {"metric": "latency_max", "points": [[now - 120, 3.98], [now - 10, 80]]},
                            {"metric": "queries", "points": [[now - 120, 120], [now - 10, 9999]]},
                            {"metric": "queries", "points": [[now - 120, 135], [now - 10, 1]]},
                        ],
                    },
                },
            )
        finally:
            fetch.time.time = original
        by_id = {row["id"]: row for row in payload["insightRows"]}
        self.assertEqual(by_id["p99"]["value"], "2.8ms")
        self.assertEqual(by_id["p99"]["pin"], "p99 2.8ms")
        self.assertEqual(by_id["p50"]["value"], "1.1ms")
        self.assertEqual(by_id["qps"]["value"], "4.2/s")
        self.assertEqual(by_id["qps"]["pin"], "qps 4.2/s")
        self.assertEqual(by_id["qpm"]["value"], "255/min")
        self.assertEqual(by_id["qpm"]["pin"], "qpm 255/min")
        self.assertIn("p99: 2.8ms", payload["tooltip"])
        self.assertNotIn("9999", payload["tooltip"])
        pins = {row["id"]: row["pin"] for row in payload["numbers"]}
        self.assertEqual(pins["schema"], "schema 0")
        self.assertEqual(pins["p99"], "p99 2.8ms")
        self.assertEqual(pins["cpu"], "")
        self.assertEqual(pins["errors"], "errors 0")

    def test_range_averages_finished_buckets(self):
        now = 1_000_000
        original = fetch.time.time
        fetch.time.time = lambda: now
        try:
            payload = fetch.compose(
                ready_database(),
                deploys([]),
                empty_metrics(),
                empty_insights(),
                "acme",
                "main",
                {
                    "ok": True,
                    "status": 200,
                    "data": {
                        "interval": 60,
                        "series": [
                            {"metric": "latency_p99", "points": [[now - 180, 10], [now - 120, 30], [now - 10, 90]]},
                            {"metric": "queries", "points": [[now - 180, 60], [now - 120, 180], [now - 10, 9999]]},
                        ],
                    },
                },
                "6h",
            )
            gauges = fetch.window_gauges({
                "interval": 60,
                "series": [
                    {"metric": "primary_cpu_usage", "points": [[now - 180, 20], [now - 120, 40], [now - 10, 99]]},
                    {"metric": "replication_lag", "points": [[now - 180, 0], [now - 120, 2], [now - 10, 9]]},
                ],
            })
        finally:
            fetch.time.time = original
        by_id = {row["id"]: row for row in payload["insightRows"]}
        self.assertEqual(by_id["p99"]["value"], "20ms")
        self.assertEqual(by_id["qps"]["value"], "2/s")
        self.assertEqual(payload["period"], "6h")
        self.assertEqual(fetch.normalize_period("nope"), "1h")
        cpu = gauges[0]["values"][0]["value"]
        lag = gauges[1]["values"][0]["value"]
        self.assertEqual(cpu, 30)
        self.assertEqual(lag, 1)

    def test_charts_use_finished_buckets(self):
        now = 1_000_000
        original = fetch.time.time
        fetch.time.time = lambda: now
        try:
            metrics_payload = empty_metrics()
            metrics_payload["tablets"] = {
                "interval": 60,
                "series": [
                    {"metric": "primary_cpu_usage", "points": [[now - 180, 20], [now - 120, 40], [now - 10, 99]]},
                    {"metric": "primary_memory_usage", "points": [[now - 180, 50], [now - 120, 70], [now - 10, 99]]},
                ],
            }
            payload = fetch.compose(
                ready_database(),
                deploys([]),
                metrics_payload,
                empty_insights(),
                "acme",
                "main",
                {
                    "ok": True,
                    "status": 200,
                    "data": {
                        "interval": 60,
                        "series": [
                            {"metric": "latency_p50", "points": [[now - 180, 4], [now - 120, 6], [now - 10, 80]]},
                            {"metric": "latency_p99", "points": [[now - 180, 10], [now - 120, 30], [now - 10, 90]]},
                            {"metric": "queries", "points": [[now - 180, 60], [now - 120, 180], [now - 10, 9999]]},
                            {"metric": "queries", "points": [[now - 180, 60], [now - 120, 60], [now - 10, 1]]},
                        ],
                    },
                },
            )
            long_points = [[now - (96 - index) * 60, float(index)] for index in range(96)]
            long_chart = fetch.build_charts(
                {"interval": 60, "series": [{"metric": "latency_p99", "points": long_points}]},
                None,
            )
        finally:
            fetch.time.time = original
        charts = {chart["id"]: chart for chart in payload["charts"]}
        self.assertEqual([series["id"] for series in charts["latency"]["series"]], ["p50", "p99"])
        self.assertEqual(charts["latency"]["series"][1]["points"], [10, 30])
        self.assertEqual(charts["latency"]["unit"], "ms")
        self.assertEqual(charts["queries"]["series"][0]["points"], [2, 4])
        self.assertEqual(charts["queries"]["unit"], "/s")
        self.assertEqual(charts["host"]["scale"], "percent")
        self.assertEqual(charts["host"]["series"][0]["points"], [50, 70])
        self.assertEqual(charts["host"]["series"][1]["points"], [20, 40])
        self.assertEqual(fetch.compose(ready_database(), deploys([]), empty_metrics(), empty_insights(), "acme", "main")["charts"], [])
        reduced = long_chart[0]["series"][0]["points"]
        self.assertEqual(len(reduced), 48)
        self.assertEqual(reduced[0], 0.5)
        self.assertEqual(reduced[-1], 94.5)

    def test_branch_numbers_can_be_pinned(self):
        payload = fetch.compose(
            ready_database(open_schema_recommendations_count=28),
            deploys([]),
            {
                "ok": True,
                "status": 200,
                "data": {
                    "metrics": [
                        {"metric": "primary_cpu_usage", "values": [{"value": 48}]},
                        {"metric": "primary_memory_usage", "values": [{"value": 67}]},
                        {"metric": "replication_lag", "values": [{"value": 0}]},
                    ],
                    "disk_used_bytes": 16,
                    "disk_free_bytes": 54757998592,
                },
            },
            insights([
                {"error_count": 1, "normalized_sql": "select 1"},
                {"error_count": 1, "normalized_sql": "select 2"},
                {"error_count": 1, "normalized_sql": "select 3"},
                {"error_count": 1, "normalized_sql": "select 4"},
            ]),
            "acme",
            "main",
        )
        pins = {row["id"]: row for row in payload["numbers"]}
        self.assertEqual(pins["schema"]["pin"], "schema 28")
        self.assertEqual(pins["cpu"]["pin"], "cpu 48%")
        self.assertEqual(pins["memory"]["pin"], "mem 67%")
        self.assertEqual(pins["lag"]["pin"], "lag 0s")
        self.assertEqual(pins["disk"]["pin"], "disk 16 B/51 GiB")
        self.assertEqual(pins["connections"]["pin"], "")
        self.assertEqual(pins["errors"]["pin"], "errors 4")
        self.assertFalse(payload["needsToken"])

    def test_save_token_writes_a_private_file_and_hides_the_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bar-token"
            secret = "id123:super-secret-value"
            fetch.save_token(path, "  " + secret + "\n")
            self.assertEqual(path.read_text(encoding="utf-8"), secret + "\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(fetch.load_token(path), secret)
            with self.assertRaises(fetch.ConfigError):
                fetch.save_token(path, "not a token")
            self.assertEqual(path.read_text(encoding="utf-8"), secret + "\n")
            os.environ["PS_PLUGIN_TOKEN_FILE"] = str(path)
            os.environ["PS_PLUGIN_TOKEN_VALUE"] = secret
            try:
                raw = self._capture_main(["fetch.py", "--save-token"])
            finally:
                os.environ.pop("PS_PLUGIN_TOKEN_FILE", None)
                os.environ.pop("PS_PLUGIN_TOKEN_VALUE", None)
        self.assertNotIn("super-secret-value", raw)
        self.assertTrue(json.loads(raw)["ok"])

    def test_missing_token_asks_for_one(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-token"
            os.environ["PS_PLUGIN_TOKEN_FILE"] = str(missing)
            try:
                payload = json.loads(self._capture_main(["fetch.py", "acme", "app", "main"]))
            finally:
                os.environ.pop("PS_PLUGIN_TOKEN_FILE", None)
        self.assertTrue(payload["needsToken"])
        self.assertIn("No service token", payload["error"])

    def _capture_main(self, argv):
        from io import StringIO
        original = sys.stdout
        buffer = StringIO()
        sys.stdout = buffer
        try:
            fetch.main(argv)
        finally:
            sys.stdout = original
        return buffer.getvalue()

    def test_unauthorized(self):
        self.assertIn("Unauthorized", fetch.database_error(401))

    def test_token_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bar-token"
            path.write_text("id:secret\n", encoding="utf-8")
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
            with self.assertRaises(fetch.ConfigError):
                fetch.load_token(path)
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self.assertEqual(fetch.load_token(path), "id:secret")

    def test_open_deploy_without_cutover_stays_ok(self):
        payload = fetch.compose(
            ready_database(),
            deploys([{
                "number": 3,
                "branch": "add-index",
                "into_branch": "main",
                "state": "open",
                "deployment_state": "ready",
            }]),
            empty_metrics(),
            empty_insights(),
            "acme",
            "main",
        )
        self.assertEqual(payload["tone"], "ok")
        self.assertIn("#3 add-index → main · ready", payload["tooltip"])

    def test_sleeping_is_warn_and_failed_deploy_is_bad(self):
        sleeping = fetch.compose(
            ready_database(state="sleeping", ready=False),
            deploys([]),
            empty_metrics(),
            empty_insights(),
            "acme",
            "main",
        )
        self.assertEqual(sleeping["tone"], "warn")
        failed = fetch.compose(
            ready_database(),
            deploys([{
                "number": 9,
                "branch": "bad",
                "into_branch": "main",
                "deployment_state": "error",
            }]),
            empty_metrics(),
            empty_insights(),
            "acme",
            "main",
        )
        self.assertEqual(failed["tone"], "bad")


if __name__ == "__main__":
    unittest.main()
