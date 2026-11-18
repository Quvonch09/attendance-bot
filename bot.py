import asyncio
import json
import logging
import math
import os
import random
import shutil
import sqlite3
import tempfile
from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("attendance-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "8048583227,1922170479").split(",") if x.strip()}
OFFICE_LAT = float(os.getenv("OFFICE_LAT", "38.8557873"))
OFFICE_LON = float(os.getenv("OFFICE_LON", "65.8002788"))
GEOFENCE_METERS = float(os.getenv("GEOFENCE_METERS", "300"))
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
FACES_DIR = DATA_DIR / "faces"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = DATA_DIR / "attendance.sqlite3"

DATA_DIR.mkdir(parents=True, exist_ok=True)
FACES_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# Keep permissions restrictive on Linux: face photos and the attendance DB are sensitive.
try:
    os.chmod(DATA_DIR, 0o700)
    os.chmod(FACES_DIR, 0o700)
    os.chmod(TMP_DIR, 0o700)
except OSError:
    pass


def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db_connect() as con:
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                face_path TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                approved INTEGER NOT NULL DEFAULT 0,
                approved_by INTEGER,
                approved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pending (
                telegram_id INTEGER PRIMARY KEY,
                task TEXT NOT NULL,
                step TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                work_day TEXT NOT NULL,
                kind TEXT NOT NULL,
                happened_at TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                distance_m REAL,
                face_ok INTEGER NOT NULL DEFAULT 0,
                liveness_ok INTEGER NOT NULL DEFAULT 0,
                UNIQUE(telegram_id, work_day, kind)
            );
            CREATE TABLE IF NOT EXISTS explanations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                work_day TEXT NOT NULL,
                happened_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT,
                telegram_file_id TEXT
            );
            CREATE TABLE IF NOT EXISTS sent_jobs (
                work_day TEXT NOT NULL,
                job TEXT NOT NULL,
                PRIMARY KEY(work_day, job)
            );
            """
        )
        columns = {row["name"] for row in con.execute("PRAGMA table_info(users)")}
        if "approved" not in columns:
            con.execute("ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")
        if "approved_by" not in columns:
            con.execute("ALTER TABLE users ADD COLUMN approved_by INTEGER")
        if "approved_at" not in columns:
            con.execute("ALTER TABLE users ADD COLUMN approved_at TEXT")


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def stamp(dt: datetime | None = None) -> str:
    return (dt or now_local()).isoformat(timespec="seconds")


def day_key(dt: datetime | None = None) -> str:
    return (dt or now_local()).date().isoformat()


def get_user(uid: int):
    with db_connect() as con:
        return con.execute("SELECT * FROM users WHERE telegram_id=? AND active=1 AND approved=1", (uid,)).fetchone()


def get_any_user(uid: int):
    with db_connect() as con:
        return con.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()


def all_users():
    with db_connect() as con:
        return con.execute("SELECT * FROM users WHERE active=1 AND approved=1 ORDER BY full_name").fetchall()


def pending_users():
    with db_connect() as con:
        return con.execute("SELECT * FROM users WHERE active=1 AND approved=0 ORDER BY created_at").fetchall()


def set_pending(uid: int, task: str, step: str, payload: dict | None = None):
    with db_connect() as con:
        con.execute(
            "INSERT INTO pending(telegram_id,task,step,payload) VALUES(?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET task=excluded.task,step=excluded.step,payload=excluded.payload",
            (uid, task, step, json.dumps(payload or {}, ensure_ascii=False)),
        )


def get_pending(uid: int):
    with db_connect() as con:
        row = con.execute("SELECT * FROM pending WHERE telegram_id=?", (uid,)).fetchone()
    if not row:
        return None
    return {"task": row["task"], "step": row["step"], "payload": json.loads(row["payload"] or "{}")}


def clear_pending(uid: int):
    with db_connect() as con:
        con.execute("DELETE FROM pending WHERE telegram_id=?", (uid,))


def get_event(uid: int, kind: str, work_day: str | None = None):
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM events WHERE telegram_id=? AND work_day=? AND kind=?",
            (uid, work_day or day_key(), kind),
        ).fetchone()


def add_event(uid: int, kind: str, lat=None, lon=None, distance=None, face_ok=0, live_ok=0):
    with db_connect() as con:
        con.execute(
            "INSERT INTO events(telegram_id,work_day,kind,happened_at,latitude,longitude,distance_m,face_ok,liveness_ok) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (uid, day_key(), kind, stamp(), lat, lon, distance, face_ok, live_ok),
        )


def is_job_done(job: str, work_day: str | None = None) -> bool:
    with db_connect() as con:
        return con.execute("SELECT 1 FROM sent_jobs WHERE work_day=? AND job=?", (work_day or day_key(), job)).fetchone() is not None


def mark_job_done(job: str, work_day: str | None = None):
    with db_connect() as con:
        con.execute("INSERT OR IGNORE INTO sent_jobs(work_day,job) VALUES(?,?)", (work_day or day_key(), job))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_from_office(lat: float, lon: float) -> float:
    return haversine_m(lat, lon, OFFICE_LAT, OFFICE_LON)


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Ishga keldim")],
            [KeyboardButton(text="🍽 Tushlikka chiqyapman")],
            [KeyboardButton(text="🔙 Tushlikdan qaytdim")],
            [KeyboardButton(text="🏁 Ishdan chiqdim")],
        ],
        resize_keyboard=True,
    )


def consent_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Roziman"), KeyboardButton(text="Rozi emasman")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Joriy lokatsiyani yuborish", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def parse_day_event(uid: int, kind: str):
    return get_event(uid, kind, day_key())


def hhmm(value: str | None) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(TIMEZONE).strftime("%H:%M")


def late_minutes(checkin_iso: str | None) -> int:
    if not checkin_iso:
        return 0
    dt = datetime.fromisoformat(checkin_iso).astimezone(TIMEZONE)
    start = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    return max(0, int((dt - start).total_seconds() // 60))


def attendance_rows(work_day: str):
    with db_connect() as con:
        return con.execute(
            "SELECT e.*,u.full_name FROM events e JOIN users u USING(telegram_id) WHERE e.work_day=? ORDER BY e.happened_at",
            (work_day,),
        ).fetchall()


def events_for_user(uid: int, work_day: str):
    with db_connect() as con:
        return con.execute("SELECT * FROM events WHERE telegram_id=? AND work_day=?", (uid, work_day)).fetchall()


def get_explanation(uid: int, work_day: str | None = None):
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM explanations WHERE telegram_id=? AND work_day=? ORDER BY id LIMIT 1",
            (uid, work_day or day_key()),
        ).fetchone()


def save_explanation(uid: int, kind: str, text: str | None = None, file_id: str | None = None):
    with db_connect() as con:
        con.execute(
            "INSERT INTO explanations(telegram_id,work_day,happened_at,kind,text,telegram_file_id) VALUES(?,?,?,?,?,?)",
            (uid, day_key(), stamp(), kind, text, file_id),
        )


def compute_net_work(events: dict[str, sqlite3.Row]):
    start = datetime.fromisoformat(events["checkin"]["happened_at"]) if events.get("checkin") else None
    end = datetime.fromisoformat(events["checkout"]["happened_at"]) if events.get("checkout") else None
    if not start or not end or end <= start:
        return None
    total = end - start
    lunch_out = datetime.fromisoformat(events["lunch_out"]["happened_at"]) if events.get("lunch_out") else None
    lunch_in = datetime.fromisoformat(events["lunch_in"]["happened_at"]) if events.get("lunch_in") else None
    if lunch_out and lunch_in and lunch_in > lunch_out:
        total -= (lunch_in - lunch_out)
    return max(timedelta(), total)


def fmt_duration(value: timedelta | None) -> str:
    if value is None:
        return "hisoblab bo‘lmadi (kelish/chiqish vaqti to‘liq emas)"
    minutes = int(value.total_seconds() // 60)
    return f"{minutes // 60} soat {minutes % 60} daqiqa"


def build_report(work_day: str):
    users = all_users()
    lines = [f"📊 Kunlik davomat hisoboti — {work_day}", ""]
    for user in users:
        rows = events_for_user(user["telegram_id"], work_day)
        by_kind = {r["kind"]: r for r in rows}
        checkin = by_kind.get("checkin")
        lunch_out = by_kind.get("lunch_out")
        lunch_in = by_kind.get("lunch_in")
        checkout = by_kind.get("checkout")
        delay = late_minutes(checkin["happened_at"] if checkin else None)
        delay_label = f"{delay} daqiqa" if checkin and delay else ("kechikmagan" if checkin else "kelish qayd etilmagan")
        net = compute_net_work(by_kind)
        missing = []
        if not lunch_out:
            missing.append("12:00 tasdiq yo‘q")
        if not lunch_in:
            missing.append("14:30 tasdiq yo‘q")
        if not checkout:
            missing.append("18:00 chiqish qayd etilmagan")
        explanation = get_explanation(user["telegram_id"], work_day)
        if explanation:
            reason_text = explanation["text"] or "ovozli izoh (adminlarga yuborilgan)"
            note = f"\n  Kechikish izohi ({explanation['kind']}): {reason_text}"
        else:
            note = ""
        lines.append(
            f"• {user['full_name']}\n"
            f"  Kelish: {hhmm(checkin['happened_at']) if checkin else '—'}; kechikish: {delay_label}\n"
            f"  Tushlik: {hhmm(lunch_out['happened_at']) if lunch_out else '—'}–{hhmm(lunch_in['happened_at']) if lunch_in else '—'}; "
            f"chiqish: {hhmm(checkout['happened_at']) if checkout else '—'}\n"
            f"  Ish vaqti: {fmt_duration(net)}"
            f"{'; ' + ', '.join(missing) if missing else ''}{note}"
        )
    return "\n".join(lines)


async def notify_admins(bot: Bot, text: str):
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]
    for admin_id in ADMIN_IDS:
        for chunk in chunks:
            try:
                await bot.send_message(admin_id, chunk)
            except Exception as exc:
                LOG.warning("Admin notification failed for %s: %s", admin_id, exc)


async def send_to_user(bot: Bot, uid: int, text: str, **kwargs):
    try:
        await bot.send_message(uid, text, **kwargs)
    except Exception as exc:
        LOG.warning("User notification failed for %s: %s", uid, exc)


async def download_file(bot: Bot, file_id: str, suffix: str) -> Path:
    file = await bot.get_file(file_id)
    target = TMP_DIR / f"{random.randrange(10**12)}{suffix}"
    await bot.download_file(file.file_path, destination=target)
    return target


async def compare_face(reference: str | Path, candidate: str | Path) -> bool:
    """Local DeepFace comparison. Model weights are downloaded on first use."""
    try:
        from deepface import DeepFace
        result = await asyncio.to_thread(
            DeepFace.verify,
            img1_path=str(reference),
            img2_path=str(candidate),
            model_name="Facenet512",
            detector_backend="opencv",
            enforce_detection=True,
            silent=True,
        )
        return bool(result.get("verified"))
    except Exception as exc:
        LOG.exception("Face comparison failed: %s", exc)
        return False


def basic_motion_liveness(video_path: str | Path) -> bool:
    """Prototype active-motion check, not certified anti-spoofing.

    It checks that multiple frames contain a face and that the face box changes
    during the requested head turn. A printed photo/video replay may still pass.
    Replace with a certified liveness provider before relying on this for payroll.
    """
    try:
        import cv2
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            return False
        cascade_file = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cascade_file)
        boxes = []
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = capture.get(cv2.CAP_PROP_FPS) or 15
        if frame_count < 2:
            capture.release()
            return False
        indices = sorted(set([0, frame_count // 4, frame_count // 2, (3 * frame_count) // 4, frame_count - 1]))
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
            if len(faces):
                x, y, w, h = max(faces, key=lambda b: b[2] * b[3])
                boxes.append((x + w / 2, y + h / 2, w, h))
        capture.release()
        if len(boxes) < 3:
            return False
        width_delta = max(b[2] for b in boxes) - min(b[2] for b in boxes)
        center_delta = max(b[0] for b in boxes) - min(b[0] for b in boxes)
        # Randomized head turn should cause a measurable face-box variation.
        return width_delta > 8 or center_delta > 18
    except Exception:
        LOG.exception("Liveness check failed")
        return False


async def save_enrollment_photo(bot: Bot, message: Message, full_name: str, consent_at: str):
    photo = message.photo[-1]
    path = FACES_DIR / f"{message.from_user.id}.jpg"
    file = await bot.get_file(photo.file_id)
    await bot.download_file(file.file_path, destination=path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    with db_connect() as con:
        con.execute(
            "INSERT INTO users(telegram_id,full_name,face_path,consent_at,created_at,active,approved) VALUES(?,?,?,?,?,1,0) "
            "ON CONFLICT(telegram_id) DO UPDATE SET full_name=excluded.full_name,face_path=excluded.face_path,consent_at=excluded.consent_at,active=1,approved=0,approved_by=NULL,approved_at=NULL",
            (message.from_user.id, full_name, str(path), consent_at, stamp()),
        )
    clear_pending(message.from_user.id)


def format_name(user_row):
    return user_row["full_name"] if user_row else "Noma’lum xodim"


async def require_approved(message: Message) -> bool:
    uid = message.from_user.id
    if uid in ADMIN_IDS:
        await message.answer("Admin rejimi faqat nazorat uchun. Buyruqlar: /today, /live, /pending")
        return False
    if get_user(uid):
        return True
    row = get_any_user(uid)
    if row and row["active"] and not row["approved"]:
        await message.answer("Ro‘yxatdagi yuz suratingiz admin tasdig‘ini kutmoqda. Tasdiqlangach bot sizga xabar beradi.")
    else:
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
    return False


async def send_face_review(bot: Bot, user_row):
    uid = user_row["telegram_id"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yuzni tasdiqlash", callback_data=f"face:approve:{uid}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"face:reject:{uid}"),
    ]])
    caption = (
        f"🧑‍💼 Yangi xodim yuzini bir marta tekshiring\n"
        f"Ism: {user_row['full_name']}\nTelegram ID: {uid}\n"
        "Xodim biometrik qayta ishlashga rozilik berdi."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, FSInputFile(user_row["face_path"]), caption=caption, reply_markup=keyboard)
        except Exception as exc:
            LOG.warning("Could not send enrollment approval to admin %s: %s", admin_id, exc)


async def start_checkin(bot: Bot, message: Message):
    uid = message.from_user.id
    if not await require_approved(message):
        return
    if parse_day_event(uid, "checkin"):
        await message.answer("Bugungi kelishingiz allaqachon qayd etilgan.", reply_markup=main_keyboard())
        return
    if now_local().time() > time(9, 30) and not get_explanation(uid):
        set_pending(uid, "reason_then_checkin", "reason")
        await message.answer(
            "09:30 dan keyin kelayotganingiz uchun avval kechikish sababini matn yoki ovozli xabar bilan yuboring. Izoh adminlarga boradi."
        )
        return
    set_pending(uid, "checkin", "location")
    await message.answer(
        f"Ishga kelishni belgilash uchun joriy lokatsiyani yuboring (ofis nuqtasidan {int(GEOFENCE_METERS)} m ichida bo‘lishi kerak).",
        reply_markup=location_keyboard(),
    )


async def begin_lunch_event(message: Message, kind: str):
    uid = message.from_user.id
    if not await require_approved(message):
        return
    if parse_day_event(uid, kind):
        await message.answer("Bu tasdiq bugun allaqachon qayd etilgan.", reply_markup=main_keyboard())
        return
    try:
        add_event(uid, kind)
    except sqlite3.IntegrityError:
        pass
    user = get_user(uid)
    label = "tushlikka chiqdi" if kind == "lunch_out" else "tushlikdan qaytdi"
    event = parse_day_event(uid, kind)
    await message.answer(f"{label.capitalize()} deb qayd etildi: {hhmm(event['happened_at'])}.", reply_markup=main_keyboard())
    await notify_admins(message.bot, f"🔔 {format_name(user)} {hhmm(event['happened_at'])} da {label}.")


async def begin_checkout(message: Message):
    uid = message.from_user.id
    if not await require_approved(message):
        return
    if parse_day_event(uid, "checkout"):
        await message.answer("Bugungi ishdan chiqish allaqachon qayd etilgan.", reply_markup=main_keyboard())
        return
    try:
        add_event(uid, "checkout")
    except sqlite3.IntegrityError:
        pass
    event = parse_day_event(uid, "checkout")
    await message.answer(f"Ishdan chiqish qayd etildi: {hhmm(event['happened_at'])}.", reply_markup=main_keyboard())
    await notify_admins(message.bot, f"🔔 {format_name(get_user(uid))} {hhmm(event['happened_at'])} da ishdan chiqdi.")


async def record_reason(message: Message, continue_checkin: bool = False):
    uid = message.from_user.id
    if message.text:
        save_explanation(uid, "matn", text=message.text)
        rendered = message.text
    elif message.voice:
        save_explanation(uid, "ovoz", file_id=message.voice.file_id)
        rendered = "Ovozli izoh yubordi."
    else:
        await message.answer("Iltimos, sababni matn yoki ovozli xabar qilib yuboring.")
        return
    user = get_user(uid)
    await notify_admins(message.bot, f"📝 Kechikish izohi — {format_name(user)} ({hhmm(stamp())}):\n{rendered}")
    if message.voice:
        for admin_id in ADMIN_IDS:
            try:
                await message.bot.copy_message(admin_id, uid, message.message_id)
            except Exception as exc:
                LOG.warning("Could not copy voice explanation: %s", exc)
    if continue_checkin:
        set_pending(uid, "checkin", "location")
        await message.answer("Rahmat, izoh adminlarga yuborildi. Endi ofisga kelganingizni tasdiqlash uchun joriy lokatsiyani yuboring.", reply_markup=location_keyboard())
    else:
        clear_pending(uid)
        await message.answer("Izoh adminlarga yuborildi. Ishga kelishni belgilash uchun «✅ Ishga keldim» tugmasini bosing.", reply_markup=main_keyboard())


async def scheduler(bot: Bot):
    schedule = {
        "09:00": "morning_reminder",
        "09:30": "late_reason_prompt",
        "12:00": "lunch_out_reminder",
        "12:10": "lunch_out_deadline",
        "14:30": "lunch_in_reminder",
        "14:40": "lunch_in_deadline",
        "18:00": "checkout_reminder",
        "18:15": "daily_report",
    }
    while True:
        now = now_local()
        current_day = day_key(now)
        hhmm_now = now.strftime("%H:%M")
        for scheduled_time, job in schedule.items():
            if hhmm_now < scheduled_time or is_job_done(job, current_day):
                continue
            try:
                await run_scheduled_job(bot, job, current_day)
            except Exception:
                LOG.exception("Scheduled job failed: %s", job)
            mark_job_done(job, current_day)
        await asyncio.sleep(30)


async def run_scheduled_job(bot: Bot, job: str, work_day: str):
    users = all_users()
    if job == "daily_report":
        report = build_report(work_day)
        await notify_admins(bot, report)
        return
    if job == "morning_reminder":
        text = "🌅 Xayrli tong! Ishga kelganingizni lokatsiya va qisqa video-selfi orqali tasdiqlang."
    elif job == "late_reason_prompt":
        text = "⏰ 09:30 bo‘ldi. Hali kelishni qayd etmagan bo‘lsangiz, kechikish sababini matn yoki ovozda yuboring."
    elif job == "lunch_out_reminder":
        text = "🍽 Tushlikka chiqishdan oldin «Tushlikka chiqyapman» tugmasini bosing."
    elif job == "lunch_in_reminder":
        text = "🔙 Tushlikdan qaytib ishga kelganingizni «Tushlikdan qaytdim» tugmasi bilan tasdiqlang."
    elif job == "checkout_reminder":
        text = "🏁 Ishdan chiqayotganda «Ishdan chiqdim» tugmasini bosing."
    elif job in {"lunch_out_deadline", "lunch_in_deadline"}:
        kind = "lunch_out" if job == "lunch_out_deadline" else "lunch_in"
        moment = "12:00 tushlikka chiqish" if kind == "lunch_out" else "14:30 ishga qaytish"
        for user in users:
            if not get_event(user["telegram_id"], kind, work_day):
                await notify_admins(bot, f"⚠️ {user['full_name']} {moment} eslatmasini tasdiqlamadi.")
        return
    else:
        return
    for user in users:
        try:
            await bot.send_message(user["telegram_id"], text, reply_markup=main_keyboard())
        except Exception as exc:
            LOG.warning("Could not send scheduled reminder to %s: %s", user["telegram_id"], exc)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required. Copy .env.example to .env and set the token from BotFather.")
    init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def command_start(message: Message):
        uid = message.from_user.id
        if uid in ADMIN_IDS:
            await message.answer("Admin nazorat paneli. Hisobot: /today\nJonli holat: /live\nYuz tasdig‘ini kutayotganlar: /pending")
            return
        user = get_user(uid)
        if user:
            await message.answer(f"Salom, {user['full_name']}! Davomat botiga xush kelibsiz.", reply_markup=main_keyboard())
            return
        existing = get_any_user(uid)
        if existing and existing["active"] and not existing["approved"]:
            await message.answer("Ro‘yxatdagi yuz suratingiz admin tasdig‘ini kutmoqda. Tasdiqlangach bot sizga xabar beradi.")
            return
        set_pending(uid, "enroll", "consent", {})
        await message.answer(
            "Ro‘yxatdan o‘tishdan oldin muhim ma’lumot: yuz suratingiz qurilmadagi mahalliy bazada saqlanadi, ikki adminga bir martalik tasdiq uchun yuboriladi va keyingi kelishlarda video-selfi bilan avtomatik solishtiriladi. Bot roziligingiz va vaqtini qayd etadi. Har tekshiruv videosi tekshiruv tugagach vaqtinchalik fayldan o‘chiriladi. Rozilik bermasangiz, biometrik ro‘yxatdan o‘tolmaysiz; ish beruvchi bilan boshqa davomat usulini kelishishingiz mumkin.\n\nRozimisiz?",
            reply_markup=consent_keyboard(),
        )

    @dp.message(Command("help"))
    async def help_command(message: Message):
        await message.answer(
            "Davomat tugmalari: ishga kelish (lokatsiya + video-selfi), tushlikka chiqish, tushlikdan qaytish, ishdan chiqish. Adminlar /today, /live va /pending orqali nazorat qiladi. Shaxsiy ma’lumotlarni o‘chirish uchun /delete_me buyrug‘idan foydalaning."
        )

    @dp.message(Command("today"))
    async def today_command(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer(build_report(day_key()))

    @dp.message(Command("live"))
    async def live_command(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        rows = attendance_rows(day_key())
        last = {}
        for row in rows:
            last[row["telegram_id"]] = row
        lines = [f"📍 Bugungi jonli holat — {day_key()}"]
        for user in all_users():
            row = last.get(user["telegram_id"])
            lines.append(f"• {user['full_name']}: {row['kind']} {hhmm(row['happened_at'])}" if row else f"• {user['full_name']}: hali belgi yo‘q")
        await message.answer("\n".join(lines))

    @dp.message(Command("pending"))
    async def pending_command(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        rows = pending_users()
        if not rows:
            await message.answer("Yuz tasdig‘ini kutayotgan xodim yo‘q.")
            return
        lines = ["⏳ Yuz tasdig‘ini kutayotganlar:"]
        lines.extend(f"• {row['full_name']} — Telegram ID {row['telegram_id']}" for row in rows)
        await message.answer("\n".join(lines))
        for row in rows:
            await send_face_review(message.bot, row)

    @dp.callback_query(F.data.startswith("face:"))
    async def review_enrollment_face(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Bu tasdiq faqat adminlar uchun.", show_alert=True)
            return
        try:
            _, action, uid_text = callback.data.split(":", 2)
            employee_id = int(uid_text)
        except (ValueError, AttributeError):
            await callback.answer("Tasdiq so‘rovi noto‘g‘ri.", show_alert=True)
            return
        row = get_any_user(employee_id)
        if not row or not row["active"] or row["approved"]:
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer("Bu yuz tasdig‘i allaqachon ko‘rib chiqilgan.", show_alert=True)
            return
        if action == "approve":
            with db_connect() as con:
                result = con.execute(
                    "UPDATE users SET approved=1,approved_by=?,approved_at=? WHERE telegram_id=? AND active=1 AND approved=0",
                    (callback.from_user.id, stamp(), employee_id),
                )
            if result.rowcount:
                await callback.bot.send_message(employee_id, "✅ Admin yuz suratingizni tasdiqladi. Endi bot davomat tugmalarini ochdi.", reply_markup=main_keyboard())
                final_caption = f"✅ Yuz surati tasdiqlandi: {row['full_name']} (Telegram ID {employee_id})."
            else:
                final_caption = "Bu yuz tasdig‘i avvalroq ko‘rib chiqilgan."
        elif action == "reject":
            with db_connect() as con:
                result = con.execute(
                    "UPDATE users SET active=0,approved=0,approved_by=?,approved_at=? WHERE telegram_id=? AND active=1 AND approved=0",
                    (callback.from_user.id, stamp(), employee_id),
                )
            if result.rowcount:
                try:
                    Path(row["face_path"]).unlink(missing_ok=True)
                except OSError:
                    LOG.exception("Could not delete rejected enrollment image for %s", employee_id)
                await callback.bot.send_message(employee_id, "Admin ro‘yxatdagi yuz suratingizni tasdiqlamadi. Qayta ro‘yxatdan o‘tib, aniqroq selfi yuboring yoki admin bilan bog‘laning.")
                final_caption = f"❌ Yuz surati rad etildi: {row['full_name']} (Telegram ID {employee_id}). Xodim qayta ro‘yxatdan o‘tishi mumkin."
            else:
                final_caption = "Bu yuz tasdig‘i avvalroq ko‘rib chiqilgan."
        else:
            await callback.answer("Noma’lum amal.", show_alert=True)
            return
        if callback.message:
            await callback.message.edit_caption(caption=final_caption, reply_markup=None)
        await callback.answer("Bajarildi.")

    @dp.message(Command("delete_me"))
    async def delete_me(message: Message):
        uid = message.from_user.id
        with db_connect() as con:
            row = con.execute("SELECT face_path FROM users WHERE telegram_id=?", (uid,)).fetchone()
            con.execute("DELETE FROM users WHERE telegram_id=?", (uid,))
            con.execute("DELETE FROM pending WHERE telegram_id=?", (uid,))
            con.execute("DELETE FROM events WHERE telegram_id=?", (uid,))
            con.execute("DELETE FROM explanations WHERE telegram_id=?", (uid,))
        if row:
            try:
                Path(row["face_path"]).unlink(missing_ok=True)
            except OSError:
                LOG.exception("Could not delete face file for %s", uid)
            await message.answer("Ro‘yxat va saqlangan yuz suratingiz o‘chirildi. Davomat tarixini ham o‘chirdik.", reply_markup=ReplyKeyboardRemove())
        else:
            await message.answer("Siz ro‘yxatda topilmadingiz.")

    @dp.message(F.text == "Roziman")
    async def consent_yes(message: Message):
        state = get_pending(message.from_user.id)
        if not state or state["task"] != "enroll":
            return
        set_pending(message.from_user.id, "enroll", "name", {"consent_at": stamp()})
        await message.answer("Iltimos, ism va familiyangizni to‘liq yozing.", reply_markup=ReplyKeyboardRemove())

    @dp.message(F.text == "Rozi emasman")
    async def consent_no(message: Message):
        uid = message.from_user.id
        clear_pending(uid)
        await message.answer("Tushunarli. Biometrik ro‘yxatdan o‘tkazmayman. Ish beruvchingiz bilan biometrik bo‘lmagan davomat usulini kelishishingiz mumkin.", reply_markup=ReplyKeyboardRemove())
        await notify_admins(message.bot, f"ℹ️ Telegram ID {uid} biometrik davomatga rozilik bermadi; ro‘yxatdan o‘tmadi.")

    @dp.message(F.location)
    async def on_location(message: Message):
        uid = message.from_user.id
        state = get_pending(uid)
        if not state:
            existing = get_any_user(uid)
            if existing and existing["active"] and not existing["approved"]:
                await require_approved(message)
                return
            await message.answer("Lokatsiya qabul qilindi. Davomat belgilash uchun tegishli tugmani bosing.", reply_markup=main_keyboard())
            return
        if state["task"] != "checkin" or state["step"] != "location":
            await message.answer("Hozir lokatsiya kerak emas. Davomat tugmalaridan foydalaning.", reply_markup=main_keyboard())
            return
        loc = message.location
        distance = distance_from_office(loc.latitude, loc.longitude)
        if distance > GEOFENCE_METERS:
            await message.answer(f"Siz ofisdan taxminan {round(distance)} m uzoqdasiz. Ruxsat etilgan radius {int(GEOFENCE_METERS)} m. Ofisga kelgach lokatsiyani qayta yuboring.", reply_markup=location_keyboard())
            return
        challenge = random.choice(["avval kameraga qarang, boshingizni chapga burib, keyin yana kameraga qarang", "avval kameraga qarang, boshingizni o‘ngga burib, keyin yana kameraga qarang"])
        payload = {"lat": loc.latitude, "lon": loc.longitude, "distance": distance, "challenge": challenge}
        set_pending(uid, "checkin", "video", payload)
        await message.answer(
            f"Lokatsiya qabul qilindi ({round(distance)} m). Endi 3–10 soniyali yangi video-selfi yuboring: {challenge}. Videoda yuzingiz yaxshi ko‘rinsin.",
            reply_markup=ReplyKeyboardRemove(),
        )

    @dp.message(F.photo)
    async def on_photo(message: Message):
        uid = message.from_user.id
        state = get_pending(uid)
        if state and state["task"] == "enroll" and state["step"] == "photo":
            full_name = state["payload"].get("full_name", "").strip()
            if not full_name:
                await message.answer("Ro‘yxatdan o‘tishda muammo bo‘ldi. Qayta /start bosing.")
                clear_pending(uid)
                return
            await save_enrollment_photo(message.bot, message, full_name, state["payload"].get("consent_at", stamp()))
            await message.answer("Ro‘yxat ma’lumotlaringiz va yuz suratingiz adminlarga bir martalik tasdiq uchun yuborildi. Tasdiqlangach bot sizga xabar beradi.", reply_markup=ReplyKeyboardRemove())
            await send_face_review(message.bot, get_any_user(uid))
            return
        existing = get_any_user(uid)
        if existing and existing["active"] and not existing["approved"]:
            await require_approved(message)
            return
        await message.answer("Selfi-surati ro‘yxatdan o‘tishda ishlatiladi. Ishga kelishni belgilash uchun qisqa video-selfi yuboring.")

    @dp.message(F.video)
    async def on_video(message: Message):
        uid = message.from_user.id
        state = get_pending(uid)
        if not state or state["task"] != "checkin" or state["step"] != "video":
            if not await require_approved(message):
                return
            await message.answer("Video hozir kerak emas. Ishga kelishni belgilash uchun «✅ Ishga keldim» tugmasini bosing.", reply_markup=main_keyboard())
            return
        user = get_user(uid)
        if not user:
            await require_approved(message)
            return
        if message.video.duration < 3 or message.video.duration > 10 or message.video.file_size and message.video.file_size > 20_000_000:
            await message.answer("Iltimos, 3–10 soniyalik va 20 MB dan kichik video yuboring.")
            return
        video_path = await download_file(message.bot, message.video.file_id, ".mp4")
        frame_path = TMP_DIR / f"face-{random.randrange(10**12)}.jpg"
        face_match = False
        live_match = False
        try:
            import cv2
            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                await message.answer("Videoni o‘qiy olmadim. Kameradan yangi video yuboring.")
                return
            frame_count = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count - 1)
            ok, frame = cap.read()
            cap.release()
            if ok:
                cv2.imwrite(str(frame_path), frame)
                face_match = await compare_face(user["face_path"], frame_path)
            live_match = await asyncio.to_thread(basic_motion_liveness, video_path)
        except Exception:
            LOG.exception("Video verification error")
        finally:
            video_path.unlink(missing_ok=True)
            frame_path.unlink(missing_ok=True)
        if now_local().time() > time(9, 30) and not get_explanation(uid):
            set_pending(uid, "reason_then_checkin", "reason")
            await message.answer("09:30 dan keyin kelayotganingiz uchun avval kechikish sababini matn yoki ovozda yuboring. So‘ng lokatsiya va video-selfini qayta tasdiqlang.")
            return
        if not face_match or not live_match:
            await message.answer(
                "Tekshiruv o‘tmadi. Yuzingiz aniq ko‘rinsin, ekranga qarab boshni so‘ralgan tomonga burib, 3–10 soniyalik yangi video yuboring. Bir necha urinishdan keyin ham o‘tmasa, admin bilan bog‘laning.",
                reply_markup=main_keyboard(),
            )
            await notify_admins(message.bot, f"⚠️ {format_name(user)} ning kelish selfi-video tekshiruvi muvaffaqiyatsiz bo‘ldi.")
            return
        payload = state["payload"]
        try:
            add_event(uid, "checkin", payload["lat"], payload["lon"], payload["distance"], 1, 1)
        except sqlite3.IntegrityError:
            clear_pending(uid)
            await message.answer("Bugungi kelishingiz allaqachon qayd etilgan.", reply_markup=main_keyboard())
            return
        clear_pending(uid)
        event = parse_day_event(uid, "checkin")
        delay = late_minutes(event["happened_at"])
        delay_text = f" {delay} daqiqa kechikish qayd etildi." if delay else " O‘z vaqtida keldingiz."
        await message.answer(f"Kelishingiz tasdiqlandi: {hhmm(event['happened_at'])}.{delay_text}", reply_markup=main_keyboard())
        await notify_admins(message.bot, f"✅ {format_name(user)} {hhmm(event['happened_at'])} da ishga keldi (ofisdan {round(payload['distance'])} m). Kechikish: {delay} daqiqa.")

    @dp.message(F.voice)
    async def on_voice(message: Message):
        uid = message.from_user.id
        state = get_pending(uid)
        if state and state["step"] == "reason":
            await record_reason(message, continue_checkin=(state["task"] == "reason_then_checkin"))
            return
        existing = get_any_user(uid)
        if existing and existing["active"] and not existing["approved"]:
            await require_approved(message)
            return
        if get_user(uid) and now_local().time() >= time(9, 30) and not get_event(uid, "checkin") and not get_explanation(uid):
            await record_reason(message, continue_checkin=False)
            return
        await message.answer("Ovozli xabar faqat 09:30 dan keyingi kechikish sababini yuborish uchun qabul qilinadi.", reply_markup=main_keyboard())

    @dp.message(F.text == "✅ Ishga keldim")
    async def arrival_button(message: Message):
        await start_checkin(message.bot, message)

    @dp.message(F.text == "🍽 Tushlikka chiqyapman")
    async def lunch_out_button(message: Message):
        await begin_lunch_event(message, "lunch_out")

    @dp.message(F.text == "🔙 Tushlikdan qaytdim")
    async def lunch_in_button(message: Message):
        await begin_lunch_event(message, "lunch_in")

    @dp.message(F.text == "🏁 Ishdan chiqdim")
    async def checkout_button(message: Message):
        await begin_checkout(message)

    @dp.message(F.text)
    async def on_text(message: Message):
        uid = message.from_user.id
        state = get_pending(uid)
        if state and state["step"] == "name" and state["task"] == "enroll":
            full_name = " ".join(message.text.split()).strip()
            if len(full_name) < 5 or len(full_name) > 100:
                await message.answer("Ism va familiyani to‘liq kiriting (kamida 5 ta belgi).")
                return
            payload = state["payload"]
            payload["full_name"] = full_name
            set_pending(uid, "enroll", "photo", payload)
            await message.answer("Endi yuzingiz aniq ko‘rinadigan selfi suratini yuboring. Surat faqat davomatdagi yuz tekshiruvi uchun saqlanadi.", reply_markup=ReplyKeyboardRemove())
            return
        if state and state["step"] == "reason":
            await record_reason(message, continue_checkin=(state["task"] == "reason_then_checkin"))
            return
        existing = get_any_user(uid)
        if existing and existing["active"] and not existing["approved"]:
            await message.answer("Ro‘yxatdagi yuz suratingiz admin tasdig‘ini kutmoqda. Tasdiqlangach bot sizga xabar beradi.")
            return
        if now_local().time() >= time(9, 30) and not get_event(uid, "checkin") and not get_explanation(uid) and get_user(uid):
            await record_reason(message, continue_checkin=False)
            return
        await message.answer("Kerakli davomat tugmasini tanlang yoki /help buyrug‘ini yuboring.", reply_markup=main_keyboard())

    @dp.message()
    async def unknown_message(message: Message):
        await message.answer("Xabar turi tushunarsiz. /help buyrug‘idan foydalaning.")

    scheduler_task = asyncio.create_task(scheduler(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    init_db()
    asyncio.run(main())
