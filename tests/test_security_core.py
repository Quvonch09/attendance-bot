import gzip
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
from webapp_api import build_api_app, classify_location, verify_init_data


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

    def test_regular_employee_keyboard_has_exactly_four_attendance_buttons(self):
        keyboard = bot.main_keyboard(uid=123456789).keyboard
        texts = [button.text for row in keyboard for button in row]
        self.assertEqual(texts, ["✅ Ishga keldim", "🍽 Tushlikka", "🔙 Tushlikdan qaytdim", "🏁 Ishdan chiqdim"])

    def test_owner_and_second_admin_keep_employee_and_admin_controls(self):
        attendance = ["✅ Ishga keldim", "🍽 Tushlikka", "🔙 Tushlikdan qaytdim", "🏁 Ishdan chiqdim"]
        for uid in (8048583227, 1922170479):
            texts = [button.text for row in bot.main_keyboard(uid=uid).keyboard for button in row]
            self.assertEqual(texts[:4], attendance)
            self.assertIn("👥 Xodimlar", texts)
            self.assertIn("🧑‍💼 Yuz tasdiqlari", texts)

    def test_employee_profiles_are_reviewer_only_and_private_chat_only(self):
        self.assertIsNone(bot.employee_profile_access_error(8048583227, "private"))
        self.assertIsNone(bot.employee_profile_access_error(1922170479, "private"))
        self.assertIsNotNone(bot.employee_profile_access_error(123456789, "private"))
        self.assertIsNotNone(bot.employee_profile_access_error(8048583227, "group"))

    def test_admin_employee_directory_counts_and_lists_all_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            original_db = bot.DB_PATH
            bot.DB_PATH = Path(directory) / "attendance.sqlite3"
            try:
                bot.init_db()
                test_rows = [
                    (10001, "Approved Employee", "approved.jpg", "2026-01-01", "2026-01-01", 1, 1),
                    (10002, "Pending Employee", "pending.jpg", "2026-01-02", "2026-01-02", 1, 0),
                    (10003, "Inactive Employee", "inactive.jpg", "2026-01-03", "2026-01-03", 0, 0),
                ]
                with bot.db_connect() as con:
                    con.executemany(
                        "INSERT INTO users(telegram_id,full_name,face_path,consent_at,created_at,active,approved) VALUES(?,?,?,?,?,?,?)",
                        test_rows,
                    )
                self.assertEqual(
                    bot.admin_employee_counts(),
                    {"total": 3, "active": 2, "approved": 1, "pending": 1, "inactive": 1},
                )
                page, total, rows = bot.admin_employee_page()
                self.assertEqual((page, total, len(rows)), (0, 3, 3))
                text, markup = bot.employee_directory_view()
                self.assertIn("Jami ro‘yxatda: 3 xodim", text)
                self.assertIn("Faol: 2", text)
                self.assertIn("tasdiqlangan: 1", text)
                self.assertIn("kutmoqda: 1", text)
                self.assertIn("Faol emas: 1", text)
                callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
                self.assertIn("emp:profile:10001:0", callbacks)
                self.assertIn("emp:profile:10002:0", callbacks)
                self.assertIn("emp:profile:10003:0", callbacks)
            finally:
                bot.DB_PATH = original_db

    def test_webapp_action_url_sets_action_and_keeps_existing_query(self):
        original_url = bot.WEBAPP_URL
        bot.WEBAPP_URL = "https://app.example.test/attendance?source=telegram"
        try:
            self.assertEqual(
                bot.webapp_action_url("lunch_in"),
                "https://app.example.test/attendance?source=telegram&action=lunch_in",
            )
        finally:
            bot.WEBAPP_URL = original_url

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
                    form.add_field("accuracy_m", "10")
                    return await client.post("/api/attendance", data=form, headers=headers)

                mismatch = await submit("lunch_out")
                self.assertEqual(mismatch.status, 400)
                replay = await submit("checkin")
                self.assertEqual(replay.status, 400)

        asyncio.run(scenario())

    def test_motion_check_and_face_match_failures_are_distinct_and_fail_closed(self):
        async def run_case(liveness_ok, face_ok):
            token, now = "67890:face-failure-token", int(time.time())
            raw = telegram_init_data(token, now)
            compare_calls, recorded = [], []
            def compare(reference, candidate):
                compare_calls.append(candidate)
                return face_ok
            app = build_api_app(
                token=token, allowed_origin="https://web.example",
                office_lat=1.0, office_lon=2.0, office_radius_m=300,
                temp_dir=tempfile.mkdtemp(), get_user=lambda uid: {"face_path": "portrait.jpg"},
                get_event=lambda uid, kind: None, get_explanation=lambda uid: True,
                add_event=lambda *args: recorded.append(args), compare_face=compare,
                notify_admins=lambda text: None, format_name=lambda user: "Test",
                late_minutes=lambda value: 0, hhmm=lambda value: "08:00",
                now_local=lambda: datetime(2026, 1, 1, 8, 0),
                distance_from_office=lambda lat, lon: 100, clock=lambda: now,
                liveness_check=lambda paths: liveness_ok,
            )
            async with TestClient(TestServer(app)) as client:
                headers = {"Origin": "https://web.example", "X-Telegram-Init-Data": raw}
                challenge = await client.get("/api/challenge?action=checkin", headers=headers)
                challenge_id = (await challenge.json())["challenge_id"]
                form = FormData()
                for name, value in (("challenge_id", challenge_id), ("action", "checkin"),
                                    ("latitude", "1.0"), ("longitude", "2.0"),
                                    ("accuracy_m", "20")):
                    form.add_field(name, value)
                for index in range(5):
                    form.add_field("frames", b"test-frame", filename=f"frame-{index}.jpg",
                                   content_type="image/jpeg")
                response = await client.post("/api/attendance", data=form, headers=headers)
                return response.status, (await response.json())["error"], len(compare_calls), recorded

        async def scenario():
            motion_failure = await run_case(False, True)
            self.assertEqual(motion_failure[:3], (422, "motion_check_failed", 0))
            self.assertEqual(motion_failure[3], [])

            face_failure = await run_case(True, False)
            self.assertEqual(face_failure[:3], (422, "face_match_failed", 3))
            self.assertEqual(face_failure[3], [])

        asyncio.run(scenario())

    def test_location_accuracy_classification_is_conservative(self):
        self.assertIsNone(classify_location(250, 50, 300))
        self.assertEqual(classify_location(280, 50, 300), "location_accuracy_insufficient")
        self.assertEqual(classify_location(351, 50, 300), "outside_office_radius")
        self.assertEqual(classify_location(100, 0, 300), "location_accuracy_insufficient")
        self.assertEqual(classify_location(100, 100_001, 300), "location_accuracy_insufficient")

    def test_haversine_uses_latitude_longitude_order_and_returns_meters(self):
        self.assertAlmostEqual(bot.distance_from_office(bot.OFFICE_LAT, bot.OFFICE_LON), 0, places=6)
        north_offset_m = bot.distance_from_office(bot.OFFICE_LAT + 0.001, bot.OFFICE_LON)
        self.assertGreater(north_offset_m, 100)
        self.assertLess(north_offset_m, 120)
        swapped = bot.distance_from_office(bot.OFFICE_LON, bot.OFFICE_LAT)
        self.assertGreater(swapped, 1_000_000)

    def test_location_preflight_distinguishes_uncertain_from_clearly_outside(self):
        async def scenario():
            token, now = "45678:location-accuracy-token", int(time.time())
            raw = telegram_init_data(token, now)
            distances = {1.0: 250.0, 2.0: 280.0, 3.0: 360.0, 4.0: 200.0}
            app = build_api_app(
                token=token, allowed_origin="https://web.example",
                office_lat=1.0, office_lon=2.0, office_radius_m=300,
                temp_dir=tempfile.mkdtemp(), get_user=lambda uid: {"face_path": "portrait.jpg"},
                get_event=lambda uid, kind: None, get_explanation=lambda uid: True,
                add_event=lambda *args: None, compare_face=lambda *args: True,
                notify_admins=lambda text: None, format_name=lambda user: "Test",
                late_minutes=lambda value: 0, hhmm=lambda value: "08:00",
                now_local=lambda: datetime(2026, 1, 1, 8, 0),
                distance_from_office=lambda lat, lon: distances[lat], clock=lambda: now,
                liveness_check=lambda paths: True,
            )
            async with TestClient(TestServer(app)) as client:
                headers = {"Origin": "https://web.example", "X-Telegram-Init-Data": raw}
                inside = await client.post("/api/location-check", headers=headers,
                                           json={"latitude": 1.0, "longitude": 2.0, "accuracy_m": 50})
                self.assertEqual(inside.status, 200)
                self.assertEqual(await inside.json(), {"ok": True, "distance_m": 250, "accuracy_m": 50})

                uncertain = await client.post("/api/location-check", headers=headers,
                                              json={"latitude": 2.0, "longitude": 2.0, "accuracy_m": 50})
                self.assertEqual(uncertain.status, 422)
                self.assertEqual((await uncertain.json())["error"], "location_accuracy_insufficient")

                outside = await client.post("/api/location-check", headers=headers,
                                            json={"latitude": 3.0, "longitude": 2.0,
                                                  "accuracy_m": 50, "distance_m": 0})
                self.assertEqual(outside.status, 403)
                outside_payload = await outside.json()
                self.assertEqual(outside_payload["error"], "outside_office_radius")
                self.assertEqual(outside_payload["distance_m"], 360)
                self.assertEqual(outside_payload["accuracy_m"], 50)

                missing_accuracy = await client.post("/api/location-check", headers=headers,
                                                     json={"latitude": 4.0, "longitude": 2.0})
                self.assertEqual(missing_accuracy.status, 422)
                self.assertEqual((await missing_accuracy.json())["error"], "location_accuracy_insufficient")

        asyncio.run(scenario())

    def test_attendance_rechecks_accuracy_and_records_only_server_distance(self):
        async def scenario():
            token, now = "56789:final-location-accuracy-token", int(time.time())
            raw = telegram_init_data(token, now)
            current_distance = [320.0]
            recorded = []
            def get_event(uid, kind):
                return {"happened_at": "2026-01-01T08:05:00"} if recorded else None
            app = build_api_app(
                token=token, allowed_origin="https://web.example",
                office_lat=1.0, office_lon=2.0, office_radius_m=300,
                temp_dir=tempfile.mkdtemp(), get_user=lambda uid: {"face_path": "portrait.jpg"},
                get_event=get_event, get_explanation=lambda uid: True,
                add_event=lambda *args: recorded.append(args), compare_face=lambda *args: True,
                notify_admins=lambda text: None, format_name=lambda user: "Test",
                late_minutes=lambda value: 0, hhmm=lambda value: "08:05",
                now_local=lambda: datetime(2026, 1, 1, 8, 0),
                distance_from_office=lambda lat, lon: current_distance[0], clock=lambda: now,
                liveness_check=lambda paths: True,
            )
            async with TestClient(TestServer(app)) as client:
                headers = {"Origin": "https://web.example", "X-Telegram-Init-Data": raw}

                async def submit(accuracy):
                    challenge_response = await client.get("/api/challenge?action=checkin", headers=headers)
                    cid = (await challenge_response.json())["challenge_id"]
                    form = FormData()
                    for name, value in (("challenge_id", cid), ("action", "checkin"),
                                        ("latitude", "1.0"), ("longitude", "2.0"),
                                        ("accuracy_m", str(accuracy))):
                        form.add_field(name, value)
                    for index in range(3):
                        form.add_field("frames", b"test-frame", filename=f"frame-{index}.jpg", content_type="image/jpeg")
                    return await client.post("/api/attendance", data=form, headers=headers)

                uncertain = await submit(40)
                self.assertEqual(uncertain.status, 422)
                self.assertEqual((await uncertain.json())["error"], "location_accuracy_insufficient")
                self.assertEqual(recorded, [])

                current_distance[0] = 230.0
                accepted = await submit(50)
                self.assertEqual(accepted.status, 200)
                result = await accepted.json()
                self.assertEqual(result["distance_m"], 230)
                self.assertEqual(recorded[0][4], 230.0)

        asyncio.run(scenario())

    def test_same_origin_static_and_api_routes_require_trusted_origin(self):
        async def scenario():
            token, now = "54321:origin-test-token", int(time.time())
            origin = "https://api.orivasmarket.uz:8443"
            raw = telegram_init_data(token, now)
            app = build_api_app(
                token=token, allowed_origin=origin,
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
                trusted = {"X-Cue-Origin": origin}
                page = await client.get("/", headers=trusted)
                self.assertEqual(page.status, 200)
                self.assertIn("Davomat", await page.text())
                asset = await client.get("/config.js", headers=trusted)
                self.assertEqual(asset.status, 200)
                self.assertIn("window.location.origin", await asset.text())
                detector = await client.get(
                    "/face-detection/v0.4.1646425229/face_detection.js", headers=trusted
                )
                self.assertEqual(detector.status, 200)
                self.assertEqual(detector.headers.get("Content-Encoding"), "gzip")
                self.assertEqual(detector.headers.get("Cache-Control"), "public, max-age=31536000, immutable")
                detector_bytes = await detector.read()
                if detector_bytes.startswith(b"\x1f\x8b"):
                    detector_bytes = gzip.decompress(detector_bytes)
                self.assertIn(b"FaceDetection", detector_bytes)
                wasm = await client.get(
                    "/face-detection/v0.4.1646425229/face_detection_solution_wasm_bin.wasm", headers=trusted
                )
                self.assertEqual(wasm.status, 200)
                self.assertEqual(wasm.headers.get("Content-Type"), "application/wasm")
                wasm_bytes = await wasm.read()
                if wasm_bytes.startswith(b"\x1f\x8b"):
                    wasm_bytes = gzip.decompress(wasm_bytes)
                self.assertEqual(wasm_bytes[:4], b"\x00asm")
                denied_asset = await client.get(
                    "/face-detection/v0.4.1646425229/unknown.bin", headers=trusted
                )
                self.assertEqual(denied_asset.status, 404)
                headers = {**trusted, "X-Telegram-Init-Data": raw}
                challenge = await client.get("/api/challenge?action=checkin", headers=headers)
                self.assertEqual(challenge.status, 200)
                wrong_origin = await client.get(
                    "/api/challenge?action=checkin",
                    headers={**headers, "Origin": "https://evil.example"},
                )
                self.assertEqual(wrong_origin.status, 403)
                missing_proxy_origin = await client.get("/", headers={})
                self.assertEqual(missing_proxy_origin.status, 403)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
