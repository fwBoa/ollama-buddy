import concurrent.futures
import copy
import http.client
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

import ollama_buddy as buddy


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def epoch(moment):
    return int(moment.timestamp() * 1000)


def usage(days):
    buckets = [{"from": day + "T00:00:00Z", "request_count": 2,
                "usage_usd": amount, "input_tokens": 3, "output_tokens": 4}
               for day, amount in days]
    return {"buckets": buckets, "totals": {
        "request_count": len(days) * 2, "usage_usd": sum(amount for _, amount in days)}}


class CycleTests(unittest.TestCase):
    def window(self, anchor, now):
        return buddy.reset_window(None, {"quota_reset_at": epoch(anchor)}, now)

    def test_subscription_31_keeps_both_bounds(self):
        end, start, source = self.window(utc(2026, 1, 31, 12), utc(2026, 2, 5))
        self.assertEqual((start, end, source),
                         (epoch(utc(2026, 1, 31, 12)), epoch(utc(2026, 2, 28, 12)), "settings"))
        end, start, _ = self.window(utc(2026, 1, 31, 12), utc(2026, 3, 5))
        self.assertEqual((start, end), (epoch(utc(2026, 2, 28, 12)), epoch(utc(2026, 3, 31, 12))))

    def test_leap_year_and_old_anchor(self):
        end, start, _ = self.window(utc(2020, 1, 31), utc(2024, 2, 10))
        self.assertEqual((start, end), (epoch(utc(2024, 1, 31)), epoch(utc(2024, 2, 29))))

    def test_exact_reset_and_future_anchor(self):
        end, start, _ = self.window(utc(2026, 12, 12, 10), utc(2026, 10, 12, 10))
        self.assertEqual((start, end), (epoch(utc(2026, 10, 12, 10)), epoch(utc(2026, 11, 12, 10))))

    def test_missing_cycle(self):
        self.assertEqual(buddy.reset_window(None, {}, utc(2026, 10, 12)), (0, None, None))

    def test_partial_day_and_cap(self):
        data = usage([("2026-09-11", 20), ("2026-09-12", 12), ("2026-09-13", 3)])
        self.assertEqual(buddy.cycle_ratio(60, epoch(utc(2026, 9, 12)), data), .25)
        self.assertEqual(buddy.cloud_cycle_usage(data, epoch(utc(2026, 9, 12, 12))), 3)
        self.assertIsNone(buddy.cycle_ratio(0, epoch(utc(2026, 9, 12)), data))

    def test_incomplete_history_is_not_a_quota(self):
        data = usage([("2026-09-13", 3)])
        self.assertIsNone(buddy.cloud_cycle_usage(data, epoch(utc(2026, 9, 12))))
        self.assertIsNone(buddy.cloud_cycle_usage({"buckets": [], "totals": {
            "usage_usd": 5, "request_count": 1}}, epoch(utc(2026, 9, 12))))
        self.assertEqual(buddy.cloud_cycle_usage(usage([]), epoch(utc(2026, 9, 12))), 0)

    def test_api_schema_changes_and_bad_numbers(self):
        for data in ([], "oops", {"buckets": [None]}, {"buckets": [{}]}):
            self.assertIsNone(buddy.cloud_days(data))
        for value in (-1, float("nan"), float("inf"), None, {}, True):
            data = usage([("2026-09-12", 1)])
            data["buckets"][0]["usage_usd"] = value
            self.assertIsNone(buddy.cloud_days(data))
        data = usage([("2026-09-12", 1)])
        data["buckets"][0]["request_count"] = 1.5
        self.assertIsNone(buddy.cloud_days(data))
        self.assertIsNone(buddy.cloud_totals({"totals": {}}))


class CloudTests(unittest.TestCase):
    def test_valid_and_malformed_cloud_responses(self):
        for data, expected in ((usage([]), usage([])), ([], {"error": "unexpected response"}),
                               ({"totals": {}}, {"error": "unexpected response"})):
            with patch.object(buddy.urllib.request, "build_opener") as make_opener:
                response = make_opener.return_value.open.return_value.__enter__.return_value
                response.read.return_value = json.dumps(data).encode()
                self.assertEqual(buddy.cloud_usage("test-key"), expected)
                request = make_opener.return_value.open.call_args[0][0]
                self.assertEqual(request.full_url, "https://ollama.com/api/usage?range=30d")
                self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_cloud_errors_and_redirects_do_not_disclose_key(self):
        with patch.object(buddy.urllib.request, "build_opener") as make_opener:
            for code, message in ((401, "key rejected"), (302, "HTTP 302"), (500, "HTTP 500")):
                make_opener.return_value.open.side_effect = urllib.error.HTTPError(
                    "https://ollama.com/api/usage", code, "failure", {}, None)
                self.assertEqual(buddy.cloud_usage("test-key"), {"error": message})
        self.assertEqual(buddy.cloud_usage(""), {"error": "no key"})
        # A redirect must be refused before urllib can forward Authorization.
        handler = buddy.NoCloudRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://other.test"))


class StateFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = patch.multiple(buddy, DATA_DIR=root, CONFIG_PATH=root / "config.json", DB_PATH=root / "usage.db")
        self.paths.start()
        self.state = buddy.State(dict(buddy.DEFAULT_CONFIG))
        self.account = patch.object(buddy, "ollama_account", return_value={"plan": "pro", "email": "private@example.test"})
        self.account.start()

    def tearDown(self):
        self.account.stop()
        self.state.conn.close()
        self.paths.stop()
        self.temp.cleanup()


class StateTests(StateFixture):
    def test_private_atomic_config_and_failed_write(self):
        self.state.update_config({"ollama_api_key": "test-key"})
        self.assertEqual(os.stat(buddy.CONFIG_PATH).st_mode & 0o777, 0o600)
        self.assertEqual(buddy.load_config()["ollama_api_key"], "test-key")
        previous = buddy.CONFIG_PATH.read_bytes()
        with patch.object(buddy.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.state.update_config({"lang": "fr"})
        self.assertEqual(self.state.cfg["lang"], "auto")
        self.assertEqual(buddy.CONFIG_PATH.read_bytes(), previous)
        self.assertEqual(list(buddy.DATA_DIR.glob(".config-*")), [])

    def test_invalid_update_is_all_or_nothing(self):
        previous = copy.deepcopy(self.state.cfg)
        for value in (float("inf"), float("nan"), -1, True, {}):
            with self.assertRaises(ValueError):
                self.state.update_config({"lang": "fr", "monthly_quota": value})
            self.assertEqual(self.state.cfg, previous)
        with self.assertRaises(ValueError):
            self.state.update_config({"ollama_api_key": "test\nheader"})
        with self.assertRaises(ValueError):
            self.state.update_config({"quota_reset_at": 1e30})

    def test_cache_serializes_clients_and_account_is_cached(self):
        self.state.update_config({"ollama_api_key": "test-key"})
        def fetch(key):
            time.sleep(.03)
            return usage([])
        with patch.object(buddy, "cloud_usage", side_effect=fetch) as cloud:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                payloads = list(pool.map(lambda _: self.state.payload(), range(6)))
            self.assertEqual(cloud.call_count, 1)
        self.assertEqual(buddy.ollama_account.call_count, 1)
        self.assertNotIn("cloud", payloads[0])
        self.assertEqual(payloads[0]["account"], {"plan": "pro"})
        self.assertNotIn("test-key", json.dumps(payloads))

    def test_changed_cap_and_key_discard_incompatible_samples(self):
        self.state.conn.executemany("INSERT INTO cloud_samples VALUES (?,?,?,?)",
                                    [(1, "", 1, .5), (1, "model", 1, .5)])
        self.state.conn.execute("INSERT INTO model_color VALUES ('model', 0)")
        self.state.conn.commit()
        self.state.update_config({"monthly_quota": 300})
        self.assertEqual(self.state.conn.execute("SELECT model FROM cloud_samples").fetchall(), [("model",)])
        self.state.update_config({"ollama_api_key": "new-account"})
        self.assertEqual(self.state.conn.execute("SELECT * FROM cloud_samples").fetchall(), [])
        self.assertEqual(self.state.conn.execute("SELECT * FROM model_color").fetchall(), [])


class HTTPTests(StateFixture):
    def setUp(self):
        super().setUp()
        handler = type("TestHandler", (buddy.Handler,), {"state": self.state})
        self.server = buddy.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def post(self, payload, headers=None, path="/api/quota"):
        return self.request("POST", path, json.dumps(payload),
                            {"Content-Type": "application/json", **(headers or {})})

    def test_external_origin_and_rebinding_are_blocked(self):
        status, _, _ = self.post({"monthly_quota": 999}, {"Origin": "https://attacker.test"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/usage", headers={"Host": f"attacker.test:{self.port}"})
        self.assertEqual(status, 403)
        status, _, _ = self.post({"monthly_quota": 999}, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(self.state.cfg["monthly_quota"], 0)

    def test_local_json_and_native_refresh_work(self):
        status, _, _ = self.post({"monthly_quota": 60}, {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(self.state.cfg["monthly_quota"], 60)
        self.assertEqual(self.request("POST", "/api/refresh")[0], 200)
        self.assertEqual(self.request("GET", "/api/refresh")[0], 405)

    def test_bad_bodies_and_unknown_routes_return_errors(self):
        for body, expected in (("[]", 400), ("null", 400), ('{"monthly_quota":NaN}', 400), ("x" * 17000, 413)):
            self.assertEqual(self.request("POST", "/api/quota", body, {"Content-Type": "application/json"})[0], expected)
        self.assertEqual(self.request("POST", "/api/quota", '{"monthly_quota":999}', {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post({"theme": "dark"}, path="/api/lang")[0], 400)
        self.assertEqual(self.post({"quota_reset_at": -5})[0], 400)
        self.assertEqual(self.post({}, path="/missing")[0], 404)

    def test_nonce_csp_on_both_pages(self):
        import re
        for route in ("/", "/mini", "/mini.html"):
            status, headers, body = self.request("GET", route)
            self.assertEqual(status, 200)
            html = body.decode()
            nonce = re.search(r'<script nonce="([^"]+)"', html).group(1)
            self.assertIn("'nonce-" + nonce + "'", headers["Content-Security-Policy"])
            self.assertNotIn("<script>", html)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_initial_sse_payload(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/api/stream")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        line = response.readline().decode()
        self.assertTrue(line.startswith("data: "))
        self.assertIsNone(json.loads(line[6:])["quota"]["current"])
        connection.close()


if __name__ == "__main__":
    unittest.main()
