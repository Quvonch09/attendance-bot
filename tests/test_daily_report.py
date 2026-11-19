import unittest
from unittest.mock import patch

import bot


class DailyReportTests(unittest.TestCase):
    def test_repeated_names_remain_separate_and_report_all_attendance_fields(self):
        users = [
            {"telegram_id": 8048583227, "full_name": "Quvonchbek Bobomurodov"},
            {"telegram_id": 1002, "full_name": "Quvonchbek Bobomurodov"},
            {"telegram_id": 1001, "full_name": "Quvonchbek Bobomurodov"},
        ]
        events = [
            {"kind": "checkin", "happened_at": "2026-11-19T09:37:00+05:00"},
            {"kind": "lunch_out", "happened_at": "2026-11-19T12:00:00+05:00"},
            {"kind": "lunch_in", "happened_at": "2026-11-19T14:30:00+05:00"},
            {"kind": "checkout", "happened_at": "2026-11-19T18:00:00+05:00"},
        ]

        def events_for(uid, _day):
            return events if uid == 8048583227 else []

        def explanation_for(uid, _day):
            return {"kind": "matn", "text": "Ishga borgim kelmadi"} if uid == 8048583227 else None

        with (
            patch.object(bot, "all_users", return_value=users),
            patch.object(bot, "events_for_user", side_effect=events_for),
            patch.object(bot, "get_explanation", side_effect=explanation_for),
        ):
            report = bot.build_report("2026-11-19")

        self.assertIn("Xodimlar: 3 ta", report)
        self.assertEqual(report.count("👤 Xodim #"), 3)
        self.assertEqual(report.count("Quvonchbek Bobomurodov"), 3)
        self.assertIn("ID oxiri: 1001", report)
        self.assertIn("ID oxiri: 1002", report)
        self.assertIn("ID oxiri: 3227", report)
        self.assertIn("Kelish: 09:37", report)
        self.assertIn("Kechikish: 37 daqiqa kechikdi", report)
        self.assertIn("Tushlikka chiqish: 12:00", report)
        self.assertIn("Tushlikdan qaytish: 14:30", report)
        self.assertIn("Ishdan chiqish: 18:00", report)
        self.assertIn("Sof ish vaqti: 5 soat 53 daqiqa", report)
        self.assertIn("Yetishmayotgan tasdiqlar: yo‘q", report)
        self.assertIn("12:00 tasdiq yo‘q; 14:30 tasdiq yo‘q; 18:00 chiqish qayd etilmagan", report)
        self.assertIn("Kechikish izohi (matn): Ishga borgim kelmadi", report)

    def test_long_report_chunks_keep_employee_sections_whole_under_limit(self):
        blocks = [f"👤 Xodim #{index}\n" + ("x" * 650) for index in range(1, 9)]
        report = "📊 Kunlik hisobot\n\n" + "\n\n".join(blocks)
        chunks = bot.split_message(report, max_length=1500)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1500 for chunk in chunks))
        for block in blocks:
            self.assertTrue(any(block in chunk for chunk in chunks), block[:30])
        self.assertTrue(chunks[1].startswith("↪️ Davomi"))


class AdminNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_notification_targets_each_reviewer(self):
        class FakeBot:
            def __init__(self):
                self.sent = []

            async def send_message(self, chat_id, text):
                self.sent.append((chat_id, text))

        fake_bot = FakeBot()
        with patch.object(bot, "REVIEWER_IDS", {8048583227, 1922170479}):
            await bot.notify_admins(fake_bot, "Test report")

        self.assertCountEqual([chat_id for chat_id, _ in fake_bot.sent], [8048583227, 1922170479])
        self.assertTrue(all(text == "Test report" for _, text in fake_bot.sent))


if __name__ == "__main__":
    unittest.main()
