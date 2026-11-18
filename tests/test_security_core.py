import hashlib
import hmac
import json
import sqlite3
import sys
import tempfile
import unittest
import asyncio
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram.utils.web_app import check_webapp_signature

import bot
from webapp_api import build_api_app, verify_init_data


def telegram_init_data(token: str, now: int) -> str:
    fields = {
        "auth_date": str(now),
        "query_id": "AAE-test-vector",
        "signature": "included-in-token-HMAC-data",
        "user": json.dumps({"id": 123456789, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class SecurityCoreTests(unittest.TestCase):
    def test_telegram_hmac_matches_aiogram_and_rejects_tampering(self):
        token, now = "123456:independent-vector-token", 1_800_000_000
        raw = telegram_init_data(token, now)
        self.assertTrue(check_webapp_signature(token, raw))
        verified = verify_init_data(raw, token, now_epoch=now)
        self.assertEqual(verified["user"]["id"], 123456789)
        self.assertIsNone(verify_init_data(raw.replace("Test", "Evil"), token, now_epoch=now))
        self.assertIsNone(verify_init_data(raw, token + "x", now_epoch=now))

    def test_webapp_events_require_order_and_reject_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            original_db = bot.DB_PATH
            bot.DB_PATH = Path(directory) / "attendance.sqlite3"
            try:
                bot.init_db()
                uid = 991234
                with self.assertRaises(ValueError):
                    bot.add_webapp_event(uid, "lunch_out", 1.0, 2.0, 3.0)
                bot.add_webapp_event(uid, "checkin", 1.0, 2.0, 3.0)
                with self.assertRaises(sqlite3.IntegrityError):
                    bot.add_webapp_event(uid, "checkin", 1.0, 2.0, 3.0)
                bot.add_webapp_event(uid, "lunch_out", 1.0, 2.0, 3.0)
                bot.add_webapp_event(uid, "lunch_in", 1.0, 2.0, 3.0)
                bot.add_webapp_event(uid, "checkout", 1.0, 2.0, 3.0)
                for action in ("checkin", "lunch_out", "lunch_in", "checkout"):
                    event = bot.get_event(uid, action)
                    self.assertTrue(event)
                    self.assertEqual(event["face_ok"], 1)
                    self.assertEqual(event["liveness_ok"], 1)
            finally:
                bot.DB_PATH = original_db

    def test_action_mismatch_burns_challenge_and_prevents_replay(self):
        async def scenario():
            token = "98765:api-challenge-token"
            now = int(time.time())
            raw = telegram_init_data(token, now)
            app = build_api_app(
                token=token, allowed_origin="https://web.example",
                office_lat=1.0, office_lon=2.0, office_radius_m=300,
                temp_dir=tempfile.mkdtemp(), get_user=lambda uid: {"face_path": "portrait.jpg"},
                get_event=lambda uid, kind: None, get_explanation=lambda uid: True,
                add_event=lambda *args: None, compare_face=lambda *args: True,
                notify_admins=lambda text: None, format_name=lambda user: "Test",
                late_minutes=lambda value: 0, hhmm=lambda value: "08:00",
                now_local=lambda: datetime(2026, 1, 1, 8, 0),
                distance_from_office=lambda lat, lon: 0, clock=lambda: now,
                liveness_check=lambda paths: True,
            )
            async with TestClient(TestServer(app)) as client:
                headers = {"Origin": "https://web.example", "X-Telegram-Init-Data": raw}
                result = await client.get("/api/challenge?action=checkin", headers=headers)
                payload = await result.json()
                self.assertEqual(result.status, 200)
                cid = payload["challenge_id"]

                async def submit(action):
                    form = FormData()
                    form.add_field("challenge_id", cid)
                    form.add_field("action", action)
                    form.add_field("latitude", "1.0")
                    form.add_field("longitude", "2.0")
                    return await client.post("/api/attendance", data=form, headers=headers)

                mismatch = await submit("lunch_out")
                self.assertEqual(mismatch.status, 400)
                replay = await submit("checkin")
                self.assertEqual(replay.status, 400)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
