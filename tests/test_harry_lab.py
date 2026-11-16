"""単体テスト: 5指標記録・4時刻スロット(09/14/20/23)・増減計算・セキュリティ"""

import csv
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import collector
import config
import export_secret
import import_cookie
import main
import parser
import ui_server


class TestHarryNoteLab(unittest.TestCase):
    def test_normalize_schedule_times_and_infer_slot(self):
        times = config.normalize_schedule_times(["23:00", "9:00", "14:00", "20:00", "9:00", "invalid"])
        self.assertEqual(times, ["09:00", "14:00", "20:00", "23:00"])

        # cron遅延で 09:14 や 23:10 に実行されても正規スロットにマッピングされること
        self.assertEqual(
            parser.infer_time_slot(datetime(2026, 11, 16, 9, 14, 0), times, "scheduled"),
            "09:00",
        )
        self.assertEqual(
            parser.infer_time_slot(datetime(2026, 11, 16, 23, 10, 0), times, "scheduled"),
            "23:00",
        )
        self.assertEqual(
            parser.infer_time_slot(datetime(2026, 11, 16, 16, 42, 0), times, "manual"),
            "16:42",
        )

    def test_evaluate_four_daily_slots_including_20_to_23(self):
        settings = {
            "auto_backup_enabled": True,
            "schedule_mode": "times",
            "schedule_times": ["09:00", "14:00", "20:00", "23:00"],
            "min_safe_interval_hours": 2.0,
        }

        # 20:01 に実行済みでも、23:00（約3時間後）は2時間安全弁を通過して実行対象になること
        due, _, slot = ui_server.evaluate_times_schedule(
            settings,
            last_captured_at="2026-11-16 20:01:00",
            last_time_slot="20:00",
            now=datetime(2026, 11, 16, 23, 0, 10),
        )
        self.assertTrue(due)
        self.assertEqual(slot, "23:00")

        # すでに 23:02 実行済みなら翌日の 09:00 が次回予定になること
        due2, next_dt2, _ = ui_server.evaluate_times_schedule(
            settings,
            last_captured_at="2026-11-16 23:02:00",
            last_time_slot="23:00",
            now=datetime(2026, 11, 16, 23, 15, 0),
        )
        self.assertFalse(due2)
        self.assertEqual(next_dt2, datetime(2026, 11, 17, 9, 0, 0))

    def test_hourly_five_metrics_and_deltas_csv_and_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            exports_dir = tmp_path / "exports"
            db_file = tmp_path / "note_stats.db"
            raw_dir.mkdir()
            exports_dir.mkdir()

            raw_09 = raw_dir / "stats_raw_0900.json"
            raw_09.write_text(
                json.dumps(
                    {
                        "captured_at": "2026-11-16T09:05:00",
                        "account": {"urlname": "hurry_room", "nickname": "ハリー", "follower_count": 2300, "note_count": 83},
                        "summary": {
                            "period": "過去28日間",
                            "impressions": "10000",
                            "page_views": "500",
                            "likes": "120",
                            "comments": "15",
                            "follower_growth": "20",
                            "aggregation_at": "2026/11/16 08:50",
                        },
                        "articles": [
                            {
                                "note_id": "nh1",
                                "title": "実験記録1",
                                "url": "https://note.com/hurry_room/n/nh1",
                                "published_at": "2026年11月10日",
                                "status": "公開中",
                                "raw_numbers": ["1000", "50", "10", "1", "-"],
                                "full_text": "",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            raw_14 = raw_dir / "stats_raw_1400.json"
            raw_14.write_text(
                json.dumps(
                    {
                        "captured_at": "2026-11-16T14:02:00",
                        "account": {"urlname": "hurry_room", "nickname": "ハリー", "follower_count": 2307, "note_count": 83},
                        "summary": {
                            "period": "過去28日間",
                            "impressions": "10450",
                            "page_views": "532",
                            "likes": "128",
                            "comments": "18",
                            "follower_growth": "27",
                            "aggregation_at": "2026/11/16 13:50",
                        },
                        "articles": [
                            {
                                "note_id": "nh1",
                                "title": "実験記録1",
                                "url": "https://note.com/hurry_room/n/nh1",
                                "published_at": "2026年11月10日",
                                "status": "公開中",
                                "raw_numbers": ["1120", "64", "13", "2", "-"],
                                "full_text": "",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(parser, "RAW_DIR", raw_dir),
                mock.patch.object(parser, "EXPORTS_DIR", exports_dir),
                mock.patch.object(parser, "DB_FILE", db_file),
                mock.patch.object(ui_server, "DB_FILE", db_file),
            ):
                parser.process_latest_raw(capture_type="scheduled", raw_file=raw_09)
                parser.process_latest_raw(capture_type="scheduled", raw_file=raw_14)

                hourly_csv = exports_dir / "note_hourly_history.csv"
                self.assertTrue(hourly_csv.exists())
                with open(hourly_csv, "r", encoding="utf-8-sig", newline="") as f:
                    hrows = list(csv.DictReader(f))
                self.assertEqual(len(hrows), 2)
                self.assertEqual(hrows[0]["time_slot"], "09:00")
                self.assertEqual(hrows[1]["time_slot"], "14:00")
                self.assertEqual(hrows[1]["diff_impressions"], "+450")
                self.assertEqual(hrows[1]["diff_page_views"], "+32")
                self.assertEqual(hrows[1]["diff_likes"], "+8")
                self.assertEqual(hrows[1]["diff_comments"], "+3")
                self.assertEqual(hrows[1]["follower_count"], "2307")
                self.assertEqual(hrows[1]["diff_follower_count"], "+7")

                conn = sqlite3.connect(db_file)
                try:
                    tl = ui_server._build_hourly_timeline(conn)
                    self.assertEqual(len(tl["records"]), 2)
                    self.assertEqual(tl["latest"]["time_slot"], "14:00")
                    self.assertEqual(tl["latest"]["diff_follower_count"], 7)
                finally:
                    conn.close()

    def test_cookie_summary_secret_and_security(self):
        payload = import_cookie._build_session_payload("foo=1; _note_session_v5=tok_h; bar=2")
        self.assertEqual(payload["cookies"][0]["name"], "_note_session_v5")
        self.assertEqual(payload["cookies"][0]["value"], "tok_h")

        summary = collector._parse_summary(
            "インプレッション\n\n5,000\n\nページビュー\n\n200\n\nスキ\n\n30\n\nコメント\n\n4\n\n"
        )
        self.assertEqual(summary["impressions"], "5000")
        self.assertEqual(summary["comments"], "4")

        server = ThreadingHTTPServer(("127.0.0.1", 0), ui_server.BackupUIHandler)
        server.access_key = "harry_key"  # type: ignore[attr-defined]
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx1:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/job", timeout=3)
            self.assertEqual(ctx1.exception.code, 403)

            with self.assertRaises(urllib.error.HTTPError) as ctx2:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/export/file?name=../secret.json&key=harry_key", timeout=3)
            self.assertEqual(ctx2.exception.code, 400)

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/job?key=harry_key", timeout=3) as r:
                self.assertEqual(r.status, 200)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
