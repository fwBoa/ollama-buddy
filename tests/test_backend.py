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
from datetime import datetime, timezone, timedelta
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


def balance(monthly=60, remaining=47.66):
    now = datetime.now(timezone.utc)
    return {"included": {"allowance_usd": monthly, "balance_usd": remaining,
                         "period": {"from": (now - timedelta(days=11)).isoformat(),
                                    "until": (now + timedelta(days=20)).isoformat()}},
            "purchased": {"balance_usd": 7.5}}


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
    def test_balance_endpoint_and_validation(self):
        data = balance()
        with patch.object(buddy.urllib.request, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps(data).encode()
            self.assertEqual(buddy.cloud_balance("test-key"), data)
            request = opener.return_value.open.call_args[0][0]
            self.assertEqual(request.full_url, "https://ollama.com/api/balance")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
            for field in ("balance_usd", "allowance_usd"):
                for invalid in (-1, True, float("nan"), float("inf"), {}, None):
                    changed = copy.deepcopy(data)
                    changed["included"][field] = invalid
                    response.read.return_value = json.dumps(changed).encode()
                    self.assertEqual(buddy.cloud_balance("test-key"), {"error": "unexpected response"})
            changed = balance(60, 61)
            self.assertIsNone(buddy.balance_values(changed))
            for invalid in ("2026-10-01", "nonsense", "9999-12-31T00:00:00Z"):
                changed = copy.deepcopy(data)
                changed["included"]["period"]["from"] = invalid
                self.assertIsNone(buddy.balance_values(changed))
        self.assertEqual(buddy.cloud_balance(""), {"error": "no key"})

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
        self.balance = patch.object(buddy, "cloud_balance", return_value={"error": "HTTP 503"})
        self.balance.start()

    def tearDown(self):
        self.account.stop()
        self.balance.stop()
        self.state.conn.close()
        self.paths.stop()
        self.temp.cleanup()


class StateTests(StateFixture):
    def test_activity_starts_at_official_renewal_including_partial_day(self):
        with patch.object(buddy,'datetime',wraps=datetime) as clock:
            clock.now.return_value = utc(2026,10,13,10)
            self.state.cfg.update(ollama_api_key='test-key',quota_reset_at=epoch(utc(2026,9,1)))
            buddy.cloud_balance.return_value = {'included':{'allowance_usd':60,'balance_usd':59.7,
                'period':{'from':'2026-10-12T12:00:00Z','until':'2026-11-12T12:00:00Z'}},
                'purchased':{'balance_usd':0}}
            data = usage([('2026-10-11',99),('2026-10-12',.4),('2026-10-13',.2)])
            for day in data['buckets']: day['cached_input_tokens'] = 1
            with patch.object(buddy,'cloud_usage',return_value=data):
                payload = self.state.payload()
            self.assertEqual([day['day'] for day in payload['daily_days']],['2026-10-12','2026-10-13'])
            self.assertEqual(payload['activity_start'],epoch(utc(2026,10,12,12)))
            self.assertEqual(payload['activity_start_source'],'balance')
            self.assertEqual(payload['activity_partial_day'],'2026-10-12')
            self.assertAlmostEqual(payload['quota']['current'],.3)
            self.assertEqual(self.state.conn.execute('SELECT count(*) FROM daily_usage').fetchone()[0],3)
            # A real renewal changes the visible window without clearing saved history.
            clock.now.return_value = utc(2026,11,12,15)
            self.state.invalidate_cloud()
            buddy.cloud_balance.return_value = {'included':{'allowance_usd':60,'balance_usd':60,
                'period':{'from':'2026-11-12T00:00:00Z','until':'2026-12-12T00:00:00Z'}},
                'purchased':{'balance_usd':0}}
            with patch.object(buddy,'cloud_usage',return_value=usage([('2026-11-11',5),('2026-11-12',0)])):
                payload = self.state.payload()
            self.assertEqual([day['day'] for day in payload['daily_days']],['2026-11-12'])
            self.assertIsNone(payload['activity_partial_day'])

    def test_rate_limit_backoff_survives_refresh_but_not_account_change(self):
        self.state.cfg['ollama_api_key'] = 'test-key'
        buddy.cloud_balance.return_value = {'error':'HTTP 429','retry_after':120}
        self.state.balance()
        self.state.balance_ts -= 61
        self.state.invalidate_cloud()
        self.state.balance()
        self.assertEqual(buddy.cloud_balance.call_count,1)
        self.state.balance_ts -= 61
        self.state.balance()
        self.assertEqual(buddy.cloud_balance.call_count,2)
        self.state.update_config({'ollama_api_key':'another-key'})
        self.assertIsNone(self.state.balance_data)

    def test_official_reset_day_and_expired_period_refresh(self):
        with patch.object(buddy, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = utc(2026,10,12,15)
            self.state.cfg['ollama_api_key'] = 'test-key'
            buddy.cloud_balance.return_value = {'included':{'allowance_usd':60,'balance_usd':59.5,
                'period':{'from':'2026-10-12T12:00:00Z','until':'2026-11-12T12:00:00Z'}},
                'purchased':{'balance_usd':0}}
            with patch.object(buddy, 'cloud_usage', return_value=usage([('2026-10-12',.8)])):
                q = self.state.payload()['quota']
            self.assertEqual(q['current'],.5)
            self.assertIsNone(q['error'])
            self.assertEqual(q['cycle_start'],'2026-10-12T12:00:00+00:00')
            clock.now.return_value = utc(2026,11,12,15)
            buddy.cloud_balance.return_value = {'included':{'allowance_usd':60,'balance_usd':60,
                'period':{'from':'2026-11-12T12:00:00Z','until':'2026-12-12T12:00:00Z'}},
                'purchased':{'balance_usd':0}}
            self.state.balance()
            self.assertEqual(buddy.cloud_balance.call_count,2)

    def test_official_balance_ignores_manual_settings_and_usage_failure(self):
        self.state.cfg.update(ollama_api_key="test-key", monthly_quota=999,
                              quota_reset_at=epoch(utc(2020, 1, 31)))
        data = balance()
        buddy.cloud_balance.return_value = data
        with patch.object(buddy, "cloud_usage", return_value={"error": "HTTP 500"}):
            first = self.state.payload()
            self.assertFalse(first["usage_available"])
            self.assertEqual(first["quota"]["measurement"], "balance")
            self.assertAlmostEqual(first["quota"]["current"], 12.34)
            self.assertEqual(first["quota"]["monthly"], 60)
            self.assertEqual(first["quota"]["remaining"], 47.66)
            self.assertEqual(first["quota"]["purchased"], 7.5)
            self.assertEqual(first["quota"]["resets_at"], buddy.utc_timestamp(data["included"]["period"]["until"]))
            self.assertEqual(first["quota"]["resets_source"], "balance")
            self.assertEqual(first["quota"]["monthly_set"], 999)
            self.assertIsNone(first["quota"]["key_error"])
            self.state.payload()
            self.assertEqual(buddy.cloud_balance.call_count, 1)
        self.state.invalidate_cloud()
        buddy.cloud_balance.return_value = balance(60, 46)
        with patch.object(buddy, "cloud_usage", return_value=usage([])):
            self.assertEqual(self.state.payload()["quota"]["current"], 14)
        self.state.update_config({"ollama_api_key": "other-key"})
        self.assertIsNone(self.state.balance_data)
        self.assertEqual(self.state.conn.execute("SELECT * FROM balance_samples").fetchall(), [])

    def test_legacy_and_zero_allowance_do_not_use_manual_dollar_quota(self):
        self.state.cfg.update(ollama_api_key="test-key", monthly_quota=999)
        reset = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        buddy.cloud_balance.return_value = {"included": {
            "session": {"remaining_percent": 75, "resets_at": reset},
            "weekly": {"remaining_percent": 40, "resets_at": reset}},
            "purchased": {"balance_usd": 5}}
        with patch.object(buddy, "cloud_usage", return_value={"error": "unexpected response"}):
            q = self.state.payload()["quota"]
        self.assertEqual(q["error"], "legacy plan")
        self.assertIsNone(q["ratio"])
        self.assertEqual(q["monthly"], 0)
        self.assertEqual(q["legacy_limits"]["weekly"]["remaining_percent"], 40)
        self.state.invalidate_cloud()
        buddy.cloud_balance.return_value = balance(0, 0)
        with patch.object(buddy, "cloud_usage", return_value=usage([])):
            q = self.state.payload()["quota"]
        self.assertEqual(q["measurement"], "balance")
        self.assertEqual(q["remaining"], 0)
        self.assertIsNone(q["ratio"])

    def test_token_counters_persist_separately_and_missing_cache_is_unknown(self):
        today = datetime.now(timezone.utc).date().isoformat()
        data = usage([(today, .2)])
        data["buckets"][0].update(input_tokens=100, cached_input_tokens=40, output_tokens=20)
        buddy.record_days(self.state.conn, data)
        history = buddy.history_cloud(self.state.conn, self.state.cfg, data)
        day = buddy.cloud_days(history)[-1]
        self.assertEqual((day["input_tokens"], day["cached_input_tokens"], day["output_tokens"], day["tokens"]),
                         (100, 40, 20, 120))
        restarted = buddy.State(dict(self.state.cfg))
        try:
            self.assertEqual(buddy.cloud_days(buddy.history_cloud(restarted.conn, restarted.cfg, data))[-1], day)
        finally:
            restarted.conn.close()
        del data["buckets"][0]["cached_input_tokens"]
        buddy.record_days(self.state.conn, data)
        day = buddy.cloud_days(buddy.history_cloud(self.state.conn, self.state.cfg, data))[-1]
        self.assertIsNone(day["cached_input_tokens"])
        data["buckets"][0]["cached_input_tokens"] = 101
        self.assertIsNone(buddy.cloud_days(data))

    def test_migrate_old_token_total_without_inventing_split(self):
        self.state.conn.close()
        buddy.DB_PATH.unlink()
        old = sqlite3.connect(buddy.DB_PATH)
        old.execute("CREATE TABLE daily_usage (day TEXT PRIMARY KEY,usd REAL NOT NULL,requests INTEGER NOT NULL,tokens INTEGER NOT NULL,read_at INTEGER NOT NULL)")
        old.execute("INSERT INTO daily_usage VALUES ('2026-10-01',1,2,123,1)")
        old.commit()
        old.close()
        self.state.conn = buddy.connect()
        row = self.state.conn.execute("SELECT tokens,input_tokens,cached_input_tokens,output_tokens FROM daily_usage").fetchone()
        self.assertEqual(row, (123, None, None, None))

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

    def test_spending_without_reset_or_cap_and_persistent_31_day_cycle(self):
        now = utc(2026, 10, 31, 12)
        with patch.object(buddy, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            self.state.cfg["ollama_api_key"] = "test-key"
            first = usage([((utc(2026, 10, 1) + timedelta(days=n)).date().isoformat(), 1) for n in range(30)])
            with patch.object(buddy, "cloud_usage", return_value=first):
                payload = self.state.payload()
            self.assertTrue(payload["usage_available"])
            self.assertIsNone(payload["quota"]["ratio"])
            self.assertEqual(payload["period"], "recent")
            self.state.update_config({"quota_reset_at": epoch(utc(2026, 10, 1))})
            self.state.invalidate_cloud()
            second = usage([((utc(2026, 10, 2) + timedelta(days=n)).date().isoformat(), 1) for n in range(30)])
            with patch.object(buddy, "cloud_usage", return_value=second):
                payload = self.state.payload()
            self.assertEqual(payload["usd"], 31)
            self.assertEqual(payload["days_total"], 31)
            self.assertEqual(payload["quota"]["current"], 31)
            self.assertEqual(payload["period"], "cycle")
            self.state.account_data = {"plan": "unknown"}
            missing_cap = self.state.payload()
            self.assertTrue(missing_cap["usage_available"])
            self.assertIsNone(missing_cap["quota"]["ratio"])
            self.assertEqual(missing_cap["usd"], 31)
            self.state.update_config({"monthly_quota": 100})
            self.assertEqual(self.state.payload()["quota"]["ratio"], .31)
            # Le fichier SQLite survit au redemarrage, contrairement au cache HTTP.
            restarted = buddy.State(dict(self.state.cfg))
            try:
                with patch.object(buddy, "cloud_usage", return_value=second):
                    self.assertEqual(restarted.payload()["quota"]["current"], 31)
            finally:
                restarted.conn.close()
            self.state.update_config({"ollama_api_key": "another-account"})
            self.assertEqual(self.state.conn.execute("SELECT * FROM daily_usage").fetchall(), [])

    def test_daily_usage_updates_on_partial_reset_day(self):
        with patch.object(buddy, "datetime", wraps=datetime) as clock:
            clock.now.return_value = utc(2026, 10, 12, 15)
            self.state.cfg.update(ollama_api_key="test-key", quota_reset_at=epoch(utc(2026, 10, 12, 12)))
            with patch.object(buddy, "cloud_usage", return_value=usage([("2026-10-12", .2)])):
                first = self.state.payload()
            self.assertEqual(first["days"], [])
            self.assertEqual(first["daily_days"][-1]["usd"], .2)
            self.assertEqual(first["quota"]["error"], "partial reset day")
            self.assertIsNone(first["quota"]["ratio"])
            self.state.invalidate_cloud()
            with patch.object(buddy, "cloud_usage", return_value=usage([("2026-10-12", .4)])):
                second = self.state.payload()
            self.assertEqual(second["daily_days"][-1]["usd"], .4)
            clock.now.return_value = utc(2026, 10, 13)
            self.state.invalidate_cloud()
            with patch.object(buddy, "cloud_usage", return_value=usage([("2026-10-12", .4), ("2026-10-13", .1)])):
                third = self.state.payload()
            self.assertEqual(third["quota"]["current"], .1)
            self.assertEqual(len(third["daily_days"]), 2)

    def test_history_gap_and_partial_bucket_do_not_become_complete_usage(self):
        with patch.object(buddy, "datetime", wraps=datetime) as clock:
            clock.now.return_value = utc(2026, 10, 31, 12)
            self.state.cfg.update(ollama_api_key="test-key", quota_reset_at=epoch(utc(2026, 10, 1)))
            data = usage([((utc(2026, 10, 1) + timedelta(days=n)).date().isoformat(), 1)
                          for n in range(31) if n != 15])
            with patch.object(buddy, "cloud_usage", return_value=data):
                payload = self.state.payload()
            self.assertTrue(payload["usage_available"])
            self.assertIsNone(payload["quota"]["ratio"])
            self.assertEqual(payload["quota"]["error"], "incomplete history")
            original = usage([("2026-10-01", 10)])
            buddy.record_days(self.state.conn, original)
            partial = usage([("2026-10-01", 2)])
            partial["buckets"][0]["from"] = "2026-10-01T12:00:00Z"
            buddy.record_days(self.state.conn, partial)
            self.assertEqual(self.state.conn.execute("SELECT usd FROM daily_usage WHERE day='2026-10-01'").fetchone()[0], 10)


class KeychainTests(StateFixture):
    def setUp(self):
        super().setUp()
        self.environment = patch.dict(os.environ, {"OLLAMA_BUDDY_KEYCHAIN_HELPER": "/synthetic/helper"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.secret = ""
        def store(operation, value=""):
            if operation == "set": self.secret = value
            if operation == "delete": self.secret = ""
            return self.secret if operation == "get" else ""
        self.storage = patch.object(buddy, "keychain", side_effect=store)
        self.storage.start()
        self.addCleanup(self.storage.stop)

    def test_migrate_legacy_key_and_remove_it(self):
        buddy.CONFIG_PATH.write_text(json.dumps({**buddy.DEFAULT_CONFIG, "ollama_api_key": "legacy-key"}))
        cfg = buddy.load_config()
        self.assertEqual(cfg["ollama_api_key"], "legacy-key")
        self.assertEqual(self.secret, "legacy-key")
        self.assertNotIn("legacy-key", buddy.CONFIG_PATH.read_text())
        self.state.cfg = cfg
        self.state.update_config({"ollama_api_key": "replacement-key"})
        self.assertEqual(self.secret, "replacement-key")
        self.assertNotIn("replacement-key", buddy.CONFIG_PATH.read_text())
        self.state.update_config({"ollama_api_key": ""})
        self.assertEqual(self.secret, "")

    def test_locked_migration_preserves_legacy_and_retries_before_preferences(self):
        buddy.CONFIG_PATH.write_text(json.dumps({**buddy.DEFAULT_CONFIG, "ollama_api_key": "legacy-key"}))
        with patch.object(buddy, "keychain", side_effect=OSError("locked")):
            self.state.cfg = buddy.load_config()
            self.assertEqual(self.state.cfg["_key_storage_error"], "keychain unavailable")
            with self.assertRaises(OSError):
                self.state.update_config({"theme": "dark"})
        self.assertIn("legacy-key", buddy.CONFIG_PATH.read_text())
        self.state.update_config({"theme": "dark"})
        self.assertEqual(self.secret, "legacy-key")
        self.assertNotIn("legacy-key", buddy.CONFIG_PATH.read_text())

    def test_failed_config_write_rolls_back_keychain(self):
        self.state.update_config({"ollama_api_key": "original-key"})
        with patch.object(buddy, "save_config", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.state.update_config({"ollama_api_key": "new-key"})
        self.assertEqual(self.secret, "original-key")
        self.assertEqual(self.state.cfg["ollama_api_key"], "original-key")

    def test_same_key_reentry_retries_failed_migration(self):
        buddy.CONFIG_PATH.write_text(json.dumps({**buddy.DEFAULT_CONFIG, "ollama_api_key": "legacy-key"}))
        with patch.object(buddy, "keychain", side_effect=OSError("locked")):
            self.state.cfg = buddy.load_config()
            with self.assertRaises(OSError):
                self.state.update_config({"ollama_api_key": "legacy-key"})
            self.assertIn("legacy-key", buddy.CONFIG_PATH.read_text())
        self.state.update_config({"ollama_api_key": "legacy-key"})
        self.assertEqual(self.secret, "legacy-key")
        self.assertNotIn("legacy-key", buddy.CONFIG_PATH.read_text())
        self.assertNotIn("_key_storage_error", self.state.cfg)

    def test_helper_never_puts_secret_in_arguments(self):
        self.storage.stop()
        with patch.object(buddy.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b""
            buddy.keychain("set", "test-secret")
            self.assertNotIn("test-secret", " ".join(run.call_args[0][0]))
            self.assertEqual(run.call_args.kwargs["input"], b"test-secret")


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
        status, _, body = self.request('GET','/health')
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['cycle_activity'])
        status, _, _ = self.post({"monthly_quota": 60}, {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(self.state.cfg["monthly_quota"], 60)
        self.assertEqual(self.request("POST", "/api/refresh")[0], 200)
        self.assertEqual(self.request("GET", "/api/refresh")[0], 405)
        self.assertEqual(self.post({"notifications": True}, path="/api/notifications")[0], 400)
        with patch.dict(os.environ, {"OLLAMA_BUDDY_KEYCHAIN_HELPER": "/synthetic/helper"}):
            self.assertEqual(self.post({"notifications": True}, path="/api/notifications")[0], 400)
        with patch.dict(os.environ, {"OLLAMA_BUDDY_NATIVE": "1"}):
            self.assertEqual(self.post({"notifications": True}, path="/api/notifications")[0], 200)
        self.assertTrue(self.state.cfg["notifications"])
        self.assertEqual(self.post({"notifications": "true"}, path="/api/notifications")[0], 400)

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
