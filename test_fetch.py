import os
import stat
import tempfile
import unittest
from pathlib import Path

import fetch


def ready_database(**extra):
    database = {
        "name": "app",
        "state": "ready",
        "ready": True,
        "html_url": "https://app.planetscale.com/devops-youklid/app",
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
            "devops-youklid",
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
        self.assertEqual(payload["url"], "https://app.planetscale.com/devops-youklid/app")

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

    def test_latency_insights_rank_by_p99(self):
        long_sql = "select " + ("slow_column_" * 8) + " from events where id = ?"
        payload = fetch.compose(
            ready_database(),
            deploys([]),
            empty_metrics(),
            empty_insights(),
            "acme",
            "main",
            insights([
                {
                    "p99_latency": 40.2,
                    "p50_latency": 12.4,
                    "max_latency": 80,
                    "query_count": 100,
                    "rows_read_per_query": 1.5,
                    "normalized_sql": "select 1",
                },
                {
                    "p99_latency": 2500,
                    "p50_latency": 800,
                    "max_latency": 4000,
                    "query_count": 1,
                    "rows_read_per_query": 20,
                    "normalized_sql": long_sql,
                },
            ]),
        )
        rows = payload["insightRows"]
        self.assertEqual(rows[0]["p99"], "2.5s")
        self.assertEqual(rows[0]["p50"], "800ms")
        self.assertIn("1 run", rows[0]["detail"])
        self.assertIn("20 rows/query", rows[0]["detail"])
        self.assertIn("max 4.0s", rows[0]["detail"])
        self.assertTrue(rows[0]["sql"].endswith("…"))
        self.assertEqual(rows[1]["p99"], "40.2ms")
        self.assertEqual(rows[1]["p50"], "12.4ms")
        self.assertIn("p99 2.5s · p50 800ms", payload["tooltip"])
        self.assertLess(rows[1]["p99"].find("select"), 0)

    def test_env_text_quotes_password(self):
        text = fetch.env_text("app", {
            "host": "aws.connect.psdb.cloud",
            "username": "user",
            "password": "p@ss word",
            "database": "app",
        })
        self.assertIn("DATABASE_PASSWORD=p@ss word\n", text)
        self.assertIn("mysql://user:p%40ss%20word@aws.connect.psdb.cloud/app?", text)
        self.assertNotIn("p@ss word@", text)

    def test_copy_reuses_cached_password_without_creating_another(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "creds.json"
            os.environ["PS_PLUGIN_CREDENTIAL_FILE"] = str(path)
            try:
                copied = []

                def fail_request(*_args, **_kwargs):
                    raise AssertionError("should reuse the cache")

                original = fetch.request
                fetch.request = fail_request
                fetch.write_cache(path, {
                    "acme/app/main": {
                        "host": "h.psdb.cloud",
                        "username": "user",
                        "password": "secret",
                        "database": "app",
                    }
                })
                result = fetch.copy_credentials("token", "acme", "app", "main", copier=copied.append)
            finally:
                fetch.request = original
                os.environ.pop("PS_PLUGIN_CREDENTIAL_FILE", None)
        self.assertTrue(result["ok"])
        self.assertIn("DATABASE_PASSWORD=secret", copied[0])
        self.assertNotIn("secret", result["message"])

    def test_copy_reports_missing_password_permission(self):
        def forbidden(*_args, **_kwargs):
            raise fetch.ApiError(403, "no", "/passwords")

        original = fetch.request
        fetch.request = forbidden
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["PS_PLUGIN_CREDENTIAL_FILE"] = str(Path(directory) / "creds.json")
                result = fetch.copy_credentials("token", "acme", "app", "main", copier=lambda _text: None)
        finally:
            fetch.request = original
            os.environ.pop("PS_PLUGIN_CREDENTIAL_FILE", None)
        self.assertFalse(result["ok"])
        self.assertIn("connect_production_branch", result["message"])

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
