import asyncio
import json
import logging
import math
import os
import random
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime, date, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("attendance-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "8048583227,1922170479").split(",") if x.strip()}
CONTROLLER_IDS = {int(x.strip()) for x in os.getenv("CONTROLLER_IDS", "8048583227").split(",") if x.strip()}
REVIEWER_IDS = ADMIN_IDS | CONTROLLER_IDS
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
WEBAPP_ORIGIN = os.getenv("WEBAPP_ORIGIN", "").strip().rstrip("/")
WEBAPP_API_ENABLED = os.getenv("WEBAPP_API_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
WEBAPP_API_HOST = os.getenv("WEBAPP_API_HOST", "127.0.0.1").strip()
WEBAPP_API_PORT = int(os.getenv("WEBAPP_API_PORT", "8765"))
OFFICE_LAT = float(os.getenv("OFFICE_LAT", "38.8557873"))
OFFICE_LON = float(os.getenv("OFFICE_LON", "65.8002788"))
# Fixed by the owner; ignore any legacy GEOFENCE_METERS value so it cannot widen the server-side fence.
GEOFENCE_METERS = 100.0
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


EMPLOYEE_PAGE_SIZE = 8


def admin_employee_counts():
    with db_connect() as con:
        row = con.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(CASE WHEN active=1 THEN 1 ELSE 0 END),0) AS active,
                      COALESCE(SUM(CASE WHEN active=1 AND approved=1 THEN 1 ELSE 0 END),0) AS approved,
                      COALESCE(SUM(CASE WHEN active=1 AND approved=0 THEN 1 ELSE 0 END),0) AS pending,
                      COALESCE(SUM(CASE WHEN active=0 THEN 1 ELSE 0 END),0) AS inactive
               FROM users"""
        ).fetchone()
    return {key: int(row[key] or 0) for key in ("total", "active", "approved", "pending", "inactive")}


def admin_employee_page(page: int = 0):
    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0
    with db_connect() as con:
        total = int(con.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        page_count = max(1, (total + EMPLOYEE_PAGE_SIZE - 1) // EMPLOYEE_PAGE_SIZE)
        page = min(page, page_count - 1)
        rows = con.execute(
            "SELECT * FROM users ORDER BY active DESC,approved DESC,full_name COLLATE NOCASE,telegram_id LIMIT ? OFFSET ?",
            (EMPLOYEE_PAGE_SIZE, page * EMPLOYEE_PAGE_SIZE),
        ).fetchall()
    return page, total, rows


def employee_profile_access_error(uid: int, chat_type):
    if uid not in REVIEWER_IDS:
        return "Bu bo‘lim faqat nazoratchilar uchun."
    if getattr(chat_type, "value", chat_type) != "private":
        return "Xodim profillari faqat bot bilan shaxsiy chatda ko‘rsatiladi."
    return None


def employee_status(row) -> str:
    if not row["active"]:
        return "Faol emas"
    return "Tasdiqlangan" if row["approved"] else "Tasdiq kutmoqda"


def employee_face_file(row):
    try:
        photo = Path(row["face_path"]).resolve(strict=True)
        photo.relative_to(FACES_DIR.resolve())
        return photo if photo.is_file() else None
    except (OSError, TypeError, ValueError):
        return None


def employee_profile_text(row) -> str:
    lines = [
        "👤 Xodim profili",
        f"Ism: {row['full_name']}",
        f"Telegram ID: {row['telegram_id']}",
        f"Holati: {employee_status(row)}",
        f"Ro‘yxatdan o‘tgan: {row['created_at'] or '—'}",
    ]
    if row["approved"]:
        lines.append(f"Tasdiqlagan nazoratchi ID: {row['approved_by'] or '—'}")
        lines.append(f"Tasdiqlangan vaqt: {row['approved_at'] or '—'}")
    return "\n".join(lines)


def employee_profile_keyboard(page: int, list_message_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Xodimlar ro‘yxatiga qaytish", callback_data=f"emp:back:{page}:{list_message_id}")
    ]])


def employee_directory_view(page: int = 0):
    page, total, rows = admin_employee_page(page)
    counts = admin_employee_counts()
    page_count = max(1, (total + EMPLOYEE_PAGE_SIZE - 1) // EMPLOYEE_PAGE_SIZE)
    lines = [
        f"👥 Jami ro‘yxatda: {counts['total']} xodim",
        f"Faol: {counts['active']} · tasdiqlangan: {counts['approved']} · kutmoqda: {counts['pending']}",
        f"Faol emas: {counts['inactive']}",
        "",
        f"Profilni tanlang ({page + 1}/{page_count}):" if rows else "Xodimlar ro‘yxati bo‘sh.",
    ]
    keyboard = []
    for row in rows:
        badge = "⛔" if not row["active"] else ("✅" if row["approved"] else "⏳")
        name = " ".join(str(row["full_name"]).split())[:28]
        keyboard.append([InlineKeyboardButton(
            text=f"{badge} {name} · {row['telegram_id']}",
            callback_data=f"emp:profile:{row['telegram_id']}:{page}",
        )])
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"emp:list:{page - 1}"))
    if page + 1 < page_count:
        navigation.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"emp:list:{page + 1}"))
    if navigation:
        keyboard.append(navigation)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


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


def add_webapp_event(uid: int, kind: str, lat=None, lon=None, distance=None, face_ok=1, live_ok=1):
    """Atomically enforce the attendance action sequence and persist verified event."""
    order = {"checkin": 0, "lunch_out": 1, "lunch_in": 2, "checkout": 3}
    if kind not in order:
        raise ValueError("invalid attendance action")
    with db_connect() as con:
        con.execute("BEGIN IMMEDIATE")
        today = day_key()
        if con.execute("SELECT 1 FROM events WHERE telegram_id=? AND work_day=? AND kind=?", (uid, today, kind)).fetchone():
            raise sqlite3.IntegrityError("duplicate action")
        previous = ["checkin", "lunch_out", "lunch_in", "checkout"][:order[kind]]
        if any(not con.execute("SELECT 1 FROM events WHERE telegram_id=? AND work_day=? AND kind=?", (uid, today, item)).fetchone() for item in previous):
            raise ValueError("attendance sequence invalid")
        con.execute("INSERT INTO events(telegram_id,work_day,kind,happened_at,latitude,longitude,distance_m,face_ok,liveness_ok) VALUES(?,?,?,?,?,?,?,?,?)",
                    (uid, today, kind, stamp(), lat, lon, distance, face_ok, live_ok))
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


def classify_telegram_location(distance_m: float, accuracy_m: float | None) -> str | None:
    """Apply the same conservative geofence and GPS-accuracy rule to Bot API locations."""
    from webapp_api import classify_location

    return classify_location(distance_m, accuracy_m, GEOFENCE_METERS)


def telegram_location_accuracy_retry_message(accuracy_m: float | None) -> str:
    if isinstance(accuracy_m, (int, float)) and not isinstance(accuracy_m, bool) and math.isfinite(accuracy_m) and accuracy_m > 0:
        accuracy_text = f" Telegram bildirgan aniqlik taxminan ±{round(accuracy_m)} m."
    else:
        accuracy_text = " Telegram bu safar aniqlik qiymatini bermadi."
    return (
        "Telegram yuborgan lokatsiya juda noaniq."
        f"{accuracy_text} Davomat qayd etilmadi. "
        "Joylashuv va Aniq joylashuv ruxsatini yoqing, kirish eshigiga yaqin joyda "
        "Telegram’dagi «Joriy lokatsiyani yuborish» tugmasini qayta bosing."
    )


def main_keyboard(uid: int | None = None):
    keyboard = [
        [KeyboardButton(text="✅ Ishga keldim")],
        [KeyboardButton(text="🍽 Tushlikka"), KeyboardButton(text="🔙 Tushlikdan qaytdim")],
        [KeyboardButton(text="🏁 Ishdan chiqdim")],
    ]
    if uid is not None and uid in REVIEWER_IDS:
        keyboard.extend([
            [KeyboardButton(text="📊 Bugungi hisobot"), KeyboardButton(text="📍 Jonli holat")],
            [KeyboardButton(text="👥 Xodimlar"), KeyboardButton(text="🧑‍💼 Yuz tasdiqlari")],
        ])
    return ReplyKeyboardMarkup(keyboard=keyboard, is_persistent=True, resize_keyboard=True)


def webapp_action_url(action: str, native_location_token: str | None = None) -> str:
    parsed = urlsplit(WEBAPP_URL)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["action"] = action
    fragment = parsed.fragment
    if native_location_token:
        fragment_values = dict(parse_qsl(fragment, keep_blank_values=True))
        fragment_values["native_location_token"] = native_location_token
        fragment_values["autostart"] = "1"
        fragment = urlencode(fragment_values)
    return urlunsplit(parsed._replace(query=urlencode(query), fragment=fragment))


def menu_for(message: Message):
    return main_keyboard(message.from_user.id)


async def send_webapp_prompt(message: Message, action: str):
    if not WEBAPP_API_ENABLED or not WEBAPP_URL:
        await message.answer("Davomat Web App hali xavfsiz HTTPS/API manzili bilan sozlanmagan. Hozircha botning mavjud tugmalaridan foydalaning.")
        return
    if not await require_approved(message):
        return
    if action == "checkin" and now_local().time() >= time(9, 30) and not get_explanation(message.from_user.id):
        set_pending(message.from_user.id, "webapp_late_reason", "reason")
        await message.answer("09:30 dan keyin kelayotganingiz uchun avval kechikish sababini matn yoki ovozli xabar bilan yuboring. Izoh nazoratchilarga yuboriladi; so‘ng kelish tugmasini qayta bosing.")
        return
    labels = {"checkin": "ishga kelish", "lunch_out": "tushlikka chiqish", "lunch_in": "tushlikdan qaytish", "checkout": "ishdan chiqish"}
    if action not in labels:
        await message.answer("Davomat amali tanilmadi. Asosiy tugmalardan qayta tanlang.", reply_markup=main_keyboard(message.from_user.id))
        return
    set_pending(message.from_user.id, "webapp_attendance", "location", {"action": action})
    await message.answer(
        f"{labels[action].capitalize()} uchun avval Telegram chatdagi «Joriy lokatsiyani yuborish» tugmasini bosing. Server lokatsiyani 100 m radius va aniqlik bilan tekshiradi; mos kelsa kamera tugmasi chiqadi.",
        reply_markup=location_keyboard(),
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
    users = sorted(all_users(), key=lambda user: (str(user["full_name"]).casefold(), int(user["telegram_id"])))
    sections = [
        "📊 BUGUNGI DAVOMAT HISOBOTI\n"
        f"📅 Sana: {work_day}\n"
        f"👥 Xodimlar: {len(users)} ta"
    ]
    if not users:
        sections.append("Faol va tasdiqlangan xodim topilmadi.")
        return "\n\n".join(sections)

    for index, user in enumerate(users, start=1):
        uid = int(user["telegram_id"])
        name = " ".join(str(user["full_name"]).split())
        rows = events_for_user(user["telegram_id"], work_day)
        by_kind = {r["kind"]: r for r in rows}
        checkin = by_kind.get("checkin")
        lunch_out = by_kind.get("lunch_out")
        lunch_in = by_kind.get("lunch_in")
        checkout = by_kind.get("checkout")
        delay = late_minutes(checkin["happened_at"] if checkin else None)
        delay_label = f"{delay} daqiqa kechikdi" if checkin and delay else ("o‘z vaqtida" if checkin else "kelish qayd etilmagan")
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
            reason_text = str(explanation["text"] or "ovozli izoh (nazoratchilarga yuborilgan)").strip()
            reason_text = reason_text.replace("\n", "\n   ")
            reason_line = f"📝 Kechikish izohi ({explanation['kind']}): {reason_text}"
        else:
            reason_line = "📝 Kechikish izohi: yo‘q"
        sections.append(
            "\n".join(
                [
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"👤 Xodim #{index} — {name} (Telegram ID oxiri: {str(uid)[-4:].zfill(4)})",
                    f"✅ Kelish: {hhmm(checkin['happened_at']) if checkin else '—'}",
                    f"⏱ Kechikish: {delay_label}",
                    f"🍽 Tushlikka chiqish: {hhmm(lunch_out['happened_at']) if lunch_out else '—'}",
                    f"🔙 Tushlikdan qaytish: {hhmm(lunch_in['happened_at']) if lunch_in else '—'}",
                    f"🏁 Ishdan chiqish: {hhmm(checkout['happened_at']) if checkout else '—'}",
                    f"🕒 Sof ish vaqti: {fmt_duration(net)}",
                    f"⚠️ Yetishmayotgan tasdiqlar: {'; '.join(missing) if missing else 'yo‘q'}",
                    reason_line,
                ]
            )
        )
    return "\n\n".join(sections)


def split_message(text: str, max_length: int = 3800) -> list[str]:
    text = str(text or "")
    if len(text) <= max_length:
        return [text]
    if max_length <= len("↪️ Davomi\n\n"):
        raise ValueError("max_length is too small for a continuation label")
    chunks = []
    remaining = text
    while remaining:
        prefix = "" if not chunks else "↪️ Davomi\n\n"
        capacity = max_length - len(prefix)
        if len(remaining) <= capacity:
            chunks.append(prefix + remaining)
            break
        split_at = remaining.rfind("\n\n", 0, capacity + 1)
        if split_at <= 0:
            split_at = remaining.rfind("\n", 0, capacity + 1)
        if split_at <= 0:
            split_at = capacity
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            split_at = capacity
            chunk = remaining[:split_at]
        chunks.append(prefix + chunk)
        remaining = remaining[split_at:].lstrip("\n")
    return chunks or [""]


async def send_report(message: Message, work_day: str, reply_markup=None):
    chunks = split_message(build_report(work_day))
    for index, chunk in enumerate(chunks):
        kwargs = {"reply_markup": reply_markup} if reply_markup is not None and index == len(chunks) - 1 else {}
        await message.answer(chunk, **kwargs)


async def notify_admins(bot: Bot, text: str):
    chunks = split_message(text)
    for admin_id in REVIEWER_IDS:
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


FACE_MODEL_NAME = "Facenet512"
# opencv is fast but misses many real faces (angle, low light); retinaface (ships with deepface) and
# mtcnn (if installed) are used as fallbacks.
FACE_DETECTOR_BACKENDS = ("opencv", "retinaface", "mtcnn")
_FACE_LOCK = threading.Lock()


def _face_verify_sync(reference: str | Path, candidate: str | Path) -> bool:
    from deepface import DeepFace
    import cv2

    image = cv2.imread(str(candidate))
    if image is None:
        return False
    # Web App frames are mirrored like a selfie preview, while the enrollment
    # photo may not be; try both orientations of the candidate frame.
    variants = (image, cv2.flip(image, 1))
    with _FACE_LOCK:
        for backend in FACE_DETECTOR_BACKENDS:
            faces_found = False
            for variant in variants:
                try:
                    result = DeepFace.verify(
                        img1_path=str(reference),
                        img2_path=variant,
                        model_name=FACE_MODEL_NAME,
                        detector_backend=backend,
                        enforce_detection=True,
                        align=True,
                        silent=True,
                    )
                except ValueError:
                    # A face was not detected in one of the images by this backend.
                    continue
                except Exception as exc:
                    LOG.warning("Face detector %s unavailable or failed: %s", backend, exc)
                    break
                faces_found = True
                if result.get("verified"):
                    return True
            if faces_found:
                # Both faces were found and compared: this is a real mismatch.
                return False
    LOG.info("No face detected by any backend in %s or the enrollment photo", candidate)
    return False


async def compare_face(reference: str | Path, candidate: str | Path) -> bool:
    """Local DeepFace comparison. Model weights are downloaded on first use."""
    try:
        return await asyncio.to_thread(_face_verify_sync, reference, candidate)
    except Exception as exc:
        LOG.exception("Face comparison failed: %s", exc)
        return False


def _image_has_single_face_sync(path: str | Path) -> bool | None:
    """True/False when a detector ran; None when DeepFace itself is unavailable."""
    try:
        from deepface import DeepFace
    except Exception:
        return None
    with _FACE_LOCK:
        for backend in FACE_DETECTOR_BACKENDS:
            try:
                faces = DeepFace.extract_faces(img_path=str(path), detector_backend=backend, enforce_detection=True, align=True)
            except ValueError:
                continue
            except Exception as exc:
                LOG.warning("Face detector %s unavailable or failed: %s", backend, exc)
                continue
            confident = [face for face in faces if float(face.get("confidence") or 0) >= 0.5] or faces
            return len(confident) == 1
    return False


async def enrollment_photo_has_face(path: str | Path) -> bool:
    try:
        result = await asyncio.to_thread(_image_has_single_face_sync, path)
    except Exception:
        LOG.exception("Enrollment face check failed")
        return True
    return True if result is None else result


def warm_up_face_model():
    """Load the face model once at start-up so the first check-in is not slow."""
    try:
        from deepface import DeepFace
        with _FACE_LOCK:
            DeepFace.build_model(FACE_MODEL_NAME)
        LOG.info("Face model %s loaded", FACE_MODEL_NAME)
    except Exception as exc:
        LOG.warning("Face model warm-up failed (will retry on first use): %s", exc)


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


async def save_enrollment_photo(bot: Bot, message: Message, full_name: str, consent_at: str) -> bool:
    photo = message.photo[-1]
    path = FACES_DIR / f"{message.from_user.id}.jpg"
    candidate = TMP_DIR / f"enroll-{message.from_user.id}-{random.randrange(10**12)}.jpg"
    file = await bot.get_file(photo.file_id)
    await bot.download_file(file.file_path, destination=candidate)
    try:
        os.chmod(candidate, 0o600)
    except OSError:
        pass
    if not await enrollment_photo_has_face(candidate):
        candidate.unlink(missing_ok=True)
        return False
    os.replace(candidate, path)
    with db_connect() as con:
        con.execute(
            "INSERT INTO users(telegram_id,full_name,face_path,consent_at,created_at,active,approved) VALUES(?,?,?,?,?,1,0) "
            "ON CONFLICT(telegram_id) DO UPDATE SET full_name=excluded.full_name,face_path=excluded.face_path,consent_at=excluded.consent_at,active=1,approved=0,approved_by=NULL,approved_at=NULL",
            (message.from_user.id, full_name, str(path), consent_at, stamp()),
        )
    clear_pending(message.from_user.id)
    return True


def format_name(user_row):
    return user_row["full_name"] if user_row else "Noma’lum xodim"


async def require_approved(message: Message) -> bool:
    uid = message.from_user.id
    if get_user(uid):
        return True
    row = get_any_user(uid)
    if row and row["active"] and not row["approved"]:
        await message.answer("Ro‘yxatdagi yuz suratingiz nazoratchi tasdig‘ini kutmoqda. Tasdiqlangach bot sizga xabar beradi.")
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
    reviewers = REVIEWER_IDS - {uid}
    if not reviewers:
        LOG.warning("Cannot review face enrollment for %s: no other reviewer ID is configured; add another ADMIN_IDS or CONTROLLER_IDS member", uid)
        await send_to_user(bot, uid, "Ro‘yxatingiz qabul qilindi, ammo uni tasdiqlaydigan boshqa nazoratchi sozlanmagan. O‘zingizning yuzingizni tasdiqlay olmaysiz; administrator boshqa nazoratchi ID sini sozlashi kerak.")
        return
    for reviewer_id in reviewers:
        try:
            await bot.send_photo(reviewer_id, FSInputFile(user_row["face_path"]), caption=caption, reply_markup=keyboard)
        except Exception as exc:
            LOG.warning("Could not send enrollment approval to reviewer %s: %s", reviewer_id, exc)


async def start_checkin(bot: Bot, message: Message):
    uid = message.from_user.id
    if WEBAPP_API_ENABLED:
        await send_webapp_prompt(message, "checkin")
        return
    if not await require_approved(message):
        return
    if parse_day_event(uid, "checkin"):
        await message.answer("Bugungi kelishingiz allaqachon qayd etilgan.", reply_markup=main_keyboard(message.from_user.id))
        return
    if now_local().time() > time(9, 30) and not get_explanation(uid):
        set_pending(uid, "reason_then_checkin", "reason")
        await message.answer(
            "09:30 dan keyin kelayotganingiz uchun avval kechikish sababini matn yoki ovozli xabar bilan yuboring. Izoh nazoratchilarga yuboriladi."
        )
        return
    set_pending(uid, "checkin", "location")
    await message.answer(
        f"Ishga kelishni belgilash uchun joriy lokatsiyani yuboring (ofis nuqtasidan {int(GEOFENCE_METERS)} m ichida bo‘lishi kerak).",
        reply_markup=location_keyboard(),
    )


async def begin_lunch_event(message: Message, kind: str):
    uid = message.from_user.id
    if WEBAPP_API_ENABLED:
        await send_webapp_prompt(message, kind)
        return
    if not await require_approved(message):
        return
    if parse_day_event(uid, kind):
        await message.answer("Bu tasdiq bugun allaqachon qayd etilgan.", reply_markup=main_keyboard(message.from_user.id))
        return
    try:
        add_event(uid, kind)
    except sqlite3.IntegrityError:
        pass
    user = get_user(uid)
    label = "tushlikka chiqdi" if kind == "lunch_out" else "tushlikdan qaytdi"
    event = parse_day_event(uid, kind)
    await message.answer(f"{label.capitalize()} deb qayd etildi: {hhmm(event['happened_at'])}.", reply_markup=main_keyboard(message.from_user.id))
    await notify_admins(message.bot, f"🔔 {format_name(user)} {hhmm(event['happened_at'])} da {label}.")


async def begin_checkout(message: Message):
    uid = message.from_user.id
    if WEBAPP_API_ENABLED:
        await send_webapp_prompt(message, "checkout")
        return
    if not await require_approved(message):
        return
    if parse_day_event(uid, "checkout"):
        await message.answer("Bugungi ishdan chiqish allaqachon qayd etilgan.", reply_markup=main_keyboard(message.from_user.id))
        return
    try:
        add_event(uid, "checkout")
    except sqlite3.IntegrityError:
        pass
    event = parse_day_event(uid, "checkout")
    await message.answer(f"Ishdan chiqish qayd etildi: {hhmm(event['happened_at'])}.", reply_markup=main_keyboard(message.from_user.id))
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
        for admin_id in REVIEWER_IDS:
            try:
                await message.bot.copy_message(admin_id, uid, message.message_id)
            except Exception as exc:
                LOG.warning("Could not copy voice explanation: %s", exc)
    if continue_checkin:
        set_pending(uid, "checkin", "location")
        await message.answer("Rahmat, izoh nazoratchilarga yuborildi. Endi ofisga kelganingizni tasdiqlash uchun joriy lokatsiyani yuboring.", reply_markup=location_keyboard())
    else:
        clear_pending(uid)
        await message.answer("Izoh nazoratchilarga yuborildi. Ishga kelishni belgilash uchun «✅ Ishga keldim» tugmasini bosing.", reply_markup=main_keyboard(message.from_user.id))


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
        text = "🍽 Tushlikka chiqishdan oldin «Tushlikka» tugmasini bosing."
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
            await bot.send_message(user["telegram_id"], text, reply_markup=main_keyboard(user["telegram_id"]))
        except Exception as exc:
            LOG.warning("Could not send scheduled reminder to %s: %s", user["telegram_id"], exc)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required. Copy .env.example to .env and set the token from BotFather.")
    init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    async def send_employee_directory(message: Message, page: int = 0):
        denied = employee_profile_access_error(message.from_user.id, message.chat.type)
        if denied:
            await message.answer(denied)
            return
        text, keyboard = employee_directory_view(page)
        await message.answer(text, reply_markup=keyboard)

    @dp.message(CommandStart())
    async def command_start(message: Message):
        uid = message.from_user.id
        user = get_user(uid)
        if user:
            await message.answer(f"Salom, {user['full_name']}! Davomat botiga xush kelibsiz.", reply_markup=main_keyboard(message.from_user.id))
            return
        existing = get_any_user(uid)
        if existing and existing["active"] and not existing["approved"]:
            await message.answer("Ro‘yxatdagi yuz suratingiz nazoratchi tasdig‘ini kutmoqda. Tasdiqlangach bot sizga xabar beradi.")
            return
        set_pending(uid, "enroll", "consent", {})
        await message.answer(
            "Ro‘yxatdan o‘tishdan oldin muhim ma’lumot: yuz suratingiz qurilmadagi mahalliy bazada saqlanadi, nazoratchilarga bir martalik tasdiq uchun yuboriladi va keyingi kelishlarda video-selfi bilan avtomatik solishtiriladi. Bot roziligingiz va vaqtini qayd etadi. Har tekshiruv videosi tekshiruv tugagach vaqtinchalik fayldan o‘chiriladi. Rozilik bermasangiz, biometrik ro‘yxatdan o‘tolmaysiz; ish beruvchi bilan boshqa davomat usulini kelishishingiz mumkin.\n\nRozimisiz?",
            reply_markup=consent_keyboard(),
        )

    @dp.message(Command("help"))
    async def help_command(message: Message):
        await message.answer(
            "Davomat tugmalari: ishga kelish (lokatsiya + video-selfi), tushlikka chiqish, tushlikdan qaytish, ishdan chiqish. Nazoratchilar /today, /live va /pending orqali nazorat qiladi. Shaxsiy ma’lumotlarni o‘chirish uchun /delete_me buyrug‘idan foydalaning."
        )

    @dp.message(Command("employees"))
    async def employees_command(message: Message):
        await send_employee_directory(message)

    @dp.message(Command("today"))
    async def today_command(message: Message):
        if message.from_user.id not in REVIEWER_IDS:
            return
        await send_report(message, day_key())

    @dp.message(Command("live"))
    async def live_command(message: Message):
        if message.from_user.id not in REVIEWER_IDS:
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
        if message.from_user.id not in REVIEWER_IDS:
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
        if callback.from_user.id not in REVIEWER_IDS:
            await callback.answer("Bu amal faqat nazoratchilar uchun.", show_alert=True)
            return
        try:
            _, action, uid_text = callback.data.split(":", 2)
            employee_id = int(uid_text)
        except (ValueError, AttributeError):
            await callback.answer("Tasdiq so‘rovi noto‘g‘ri.", show_alert=True)
            return
        if callback.from_user.id == employee_id:
            await callback.answer("O‘z yuzingizni tasdiqlay olmaysiz. Boshqa nazoratchi ko‘rib chiqishi kerak.", show_alert=True)
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
                await callback.bot.send_message(employee_id, "✅ Nazoratchi yuz suratingizni tasdiqladi. Endi bot davomat tugmalarini ochdi.", reply_markup=main_keyboard(employee_id))
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
                await callback.bot.send_message(employee_id, "Nazoratchi ro‘yxatdagi yuz suratingizni tasdiqlamadi. Qayta ro‘yxatdan o‘tib, aniqroq selfi yuboring yoki admin bilan bog‘laning.")
                final_caption = f"❌ Yuz surati rad etildi: {row['full_name']} (Telegram ID {employee_id}). Xodim qayta ro‘yxatdan o‘tishi mumkin."
            else:
                final_caption = "Bu yuz tasdig‘i avvalroq ko‘rib chiqilgan."
        else:
            await callback.answer("Noma’lum amal.", show_alert=True)
            return
        if callback.message:
            await callback.message.edit_caption(caption=final_caption, reply_markup=None)
        await callback.answer("Bajarildi.")

    @dp.callback_query(F.data.startswith("emp:list:"))
    async def employee_list_page_callback(callback: CallbackQuery):
        chat_type = callback.message.chat.type if callback.message else None
        denied = employee_profile_access_error(callback.from_user.id, chat_type)
        if denied:
            await callback.answer(denied, show_alert=True)
            return
        try:
            page = int(callback.data.split(":", 2)[2])
        except (ValueError, IndexError):
            await callback.answer("Ro‘yxat so‘rovi noto‘g‘ri.", show_alert=True)
            return
        text, keyboard = employee_directory_view(page)
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.answer("Xodimlar ro‘yxatini yangilab bo‘lmadi.", show_alert=True)
                return
        await callback.answer()

    @dp.callback_query(F.data.startswith("emp:profile:"))
    async def employee_profile_callback(callback: CallbackQuery):
        chat_type = callback.message.chat.type if callback.message else None
        denied = employee_profile_access_error(callback.from_user.id, chat_type)
        if denied:
            await callback.answer(denied, show_alert=True)
            return
        try:
            _, _, uid_text, page_text = callback.data.split(":", 3)
            employee_id, page = int(uid_text), max(0, int(page_text))
        except (ValueError, IndexError):
            await callback.answer("Xodim profili so‘rovi noto‘g‘ri.", show_alert=True)
            return
        row = get_any_user(employee_id)
        if not row:
            await callback.answer("Xodim profili topilmadi.", show_alert=True)
            return
        profile = employee_profile_text(row)
        keyboard = employee_profile_keyboard(page, callback.message.message_id)
        photo = employee_face_file(row)
        if photo:
            await callback.bot.send_photo(callback.message.chat.id, FSInputFile(photo), caption=profile, reply_markup=keyboard)
        else:
            await callback.bot.send_message(callback.message.chat.id, profile + "\n\nYuz surati topilmadi.", reply_markup=keyboard)
        await callback.answer()

    @dp.callback_query(F.data.startswith("emp:back:"))
    async def employee_profile_back_callback(callback: CallbackQuery):
        chat_type = callback.message.chat.type if callback.message else None
        denied = employee_profile_access_error(callback.from_user.id, chat_type)
        if denied:
            await callback.answer(denied, show_alert=True)
            return
        try:
            _, _, page_text, message_text = callback.data.split(":", 3)
            page, list_message_id = max(0, int(page_text)), int(message_text)
        except (ValueError, IndexError):
            await callback.answer("Ro‘yxatga qaytish so‘rovi noto‘g‘ri.", show_alert=True)
            return
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        text, keyboard = employee_directory_view(page)
        try:
            await callback.bot.edit_message_text(
                text, chat_id=callback.message.chat.id, message_id=list_message_id, reply_markup=keyboard
            )
        except TelegramBadRequest:
            await callback.bot.send_message(callback.message.chat.id, text, reply_markup=keyboard)
        await callback.answer()

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
        if WEBAPP_API_ENABLED:
            state = get_pending(uid)
            if not state or state["task"] != "webapp_attendance" or state["step"] != "location":
                await message.answer("Avval kerakli davomat amalini tanlang, keyin shu chatdagi joriy lokatsiya tugmasidan foydalaning.", reply_markup=main_keyboard(uid))
                return
            if (getattr(message.chat.type, "value", message.chat.type) != "private" or getattr(message, "forward_origin", None)
                    or getattr(message, "venue", None)):
                await message.answer("Faqat bot bilan shaxsiy chatdan yuborilgan, boshqa chatdan ko‘chirilmagan joriy lokatsiya qabul qilinadi.", reply_markup=location_keyboard())
                return
            if not await require_approved(message):
                clear_pending(uid)
                return
            action = state["payload"].get("action")
            labels = {"checkin": "ishga kelish", "lunch_out": "tushlikka chiqish", "lunch_in": "tushlikdan qaytish", "checkout": "ishdan chiqish"}
            if action not in labels:
                clear_pending(uid)
                await message.answer("Davomat amali muddati tugadi. Asosiy tugmadan qayta boshlang.", reply_markup=main_keyboard(uid))
                return
            loc = message.location
            distance = distance_from_office(loc.latitude, loc.longitude)
            accuracy = getattr(loc, "horizontal_accuracy", None)
            location_status = classify_telegram_location(distance, accuracy)
            if location_status == "outside_office_radius":
                await message.answer(
                    f"Lokatsiya ofisning 100 m radiusidan tashqarida (server hisoblagan masofa taxminan {round(distance)} m). Ofisda bo‘lsangiz, Telegram joriy lokatsiya tugmasidan qayta yuboring. Davomat qayd etilmadi.",
                    reply_markup=location_keyboard(),
                )
                return
            if location_status == "location_accuracy_insufficient":
                await message.answer(
                    telegram_location_accuracy_retry_message(accuracy),
                    reply_markup=location_keyboard(),
                )
                return
            if location_status:
                await message.answer("Telegram joriy lokatsiyasini tekshirib bo‘lmadi. Tugmadan qayta yuboring.", reply_markup=location_keyboard())
                return
            from webapp_api import issue_native_location_token
            try:
                native_token = issue_native_location_token(
                    uid, action, loc.latitude, loc.longitude, accuracy, distance,
                    radius_m=GEOFENCE_METERS,
                )
            except ValueError:
                await message.answer("Lokatsiya server tasdiqlashiga mos kelmadi. Telegram tugmasidan qayta yuboring.", reply_markup=location_keyboard())
                return
            clear_pending(uid)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📷 Kamerani ochish va yuzni tekshirish",
                    web_app=WebAppInfo(url=webapp_action_url(action, native_token)),
                )
            ]])
            await message.answer(
                f"✅ Telegram chatdagi lokatsiya serverda tasdiqlandi (ofisdan taxminan {round(distance)} m). Endi quyidagi tugmani bosing: Mini App kamerani avtomatik so‘raydi; qurilma ruxsat so‘rasa, ruxsat bering.",
                reply_markup=keyboard,
            )
            return
        state = get_pending(uid)
        if not state:
            existing = get_any_user(uid)
            if existing and existing["active"] and not existing["approved"]:
                await require_approved(message)
                return
            await message.answer("Lokatsiya qabul qilindi. Davomat belgilash uchun tegishli tugmani bosing.", reply_markup=main_keyboard(message.from_user.id))
            return
        if state["task"] != "checkin" or state["step"] != "location":
            await message.answer("Hozir lokatsiya kerak emas. Davomat tugmalaridan foydalaning.", reply_markup=main_keyboard(message.from_user.id))
            return
        loc = message.location
        distance = distance_from_office(loc.latitude, loc.longitude)
        accuracy = getattr(loc, "horizontal_accuracy", None)
        location_status = classify_telegram_location(distance, accuracy)
        if location_status == "outside_office_radius":
            await message.answer(
                f"GPS o‘lchovi aniqlik oralig‘ini hisobga olganda ham ofisning 100 m radiusidan tashqarida (masofa taxminan {round(distance)} m). Agar ofisda bo‘lsangiz, ochiq eshikka yaqin joyda lokatsiyani qayta yuboring.",
                reply_markup=location_keyboard(),
            )
            return
        if location_status == "location_accuracy_insufficient":
            await message.answer(
                telegram_location_accuracy_retry_message(accuracy),
                reply_markup=location_keyboard(),
            )
            return
        if location_status:
            await message.answer("GPS lokatsiyasini aniqlab bo‘lmadi. Joriy lokatsiyani qayta yuboring.", reply_markup=location_keyboard())
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
            if not await save_enrollment_photo(message.bot, message, full_name, state["payload"].get("consent_at", stamp())):
                await message.answer("Suratda bitta yuz aniq topilmadi. Yorug‘ joyda, yuzingiz to‘liq va to‘g‘ri ko‘rinadigan (ko‘zoynak/niqobsiz) yangi selfi yuboring.")
                return
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
        if WEBAPP_API_ENABLED:
            await message.answer("Video-selfi faqat davomat Web App ichida yuborilgan topshiriq uchun qabul qilinadi.", reply_markup=main_keyboard(uid))
            return
        state = get_pending(uid)
        if not state or state["task"] != "checkin" or state["step"] != "video":
            if not await require_approved(message):
                return
            await message.answer("Video hozir kerak emas. Ishga kelishni belgilash uchun «✅ Ishga keldim» tugmasini bosing.", reply_markup=main_keyboard(message.from_user.id))
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
                reply_markup=main_keyboard(uid),
            )
            await notify_admins(message.bot, f"⚠️ {format_name(user)} ning kelish selfi-video tekshiruvi muvaffaqiyatsiz bo‘ldi.")
            return
        payload = state["payload"]
        try:
            add_event(uid, "checkin", payload["lat"], payload["lon"], payload["distance"], 1, 1)
        except sqlite3.IntegrityError:
            clear_pending(uid)
            await message.answer("Bugungi kelishingiz allaqachon qayd etilgan.", reply_markup=main_keyboard(message.from_user.id))
            return
        clear_pending(uid)
        event = parse_day_event(uid, "checkin")
        delay = late_minutes(event["happened_at"])
        delay_text = f" {delay} daqiqa kechikish qayd etildi." if delay else " O‘z vaqtida keldingiz."
        await message.answer(f"Kelishingiz tasdiqlandi: {hhmm(event['happened_at'])}.{delay_text}", reply_markup=main_keyboard(message.from_user.id))
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
        await message.answer("Ovozli xabar faqat 09:30 dan keyingi kechikish sababini yuborish uchun qabul qilinadi.", reply_markup=main_keyboard(message.from_user.id))

    @dp.message(F.text == "✅ Ishga keldim")
    async def arrival_button(message: Message):
        await start_checkin(message.bot, message)
    @dp.message(F.text == "🌐 Davomat web-ilovasi")
    async def webapp_button(message: Message):
        if WEBAPP_URL and WEBAPP_API_ENABLED:
            await send_webapp_prompt(message, "checkin")
        else:
            await message.answer("Davomat Web App hali sozlanmagan. Hozircha botning mavjud tugmalaridan foydalaning.", reply_markup=main_keyboard(message.from_user.id))

    @dp.message(F.text.in_({"🍽 Tushlikka", "🍽 Tushlikka chiqyapman"}))
    async def lunch_out_button(message: Message):
        await begin_lunch_event(message, "lunch_out")

    @dp.message(F.text == "🔙 Tushlikdan qaytdim")
    async def lunch_in_button(message: Message):
        await begin_lunch_event(message, "lunch_in")

    @dp.message(F.text == "🏁 Ishdan chiqdim")
    async def checkout_button(message: Message):
        await begin_checkout(message)

    @dp.message(F.text == "📊 Bugungi hisobot")
    async def report_button(message: Message):
        if message.from_user.id not in REVIEWER_IDS:
            await message.answer("Bu bo‘lim faqat nazoratchilar uchun.")
            return
        await send_report(message, day_key(), reply_markup=main_keyboard(message.from_user.id))

    @dp.message(F.text == "📍 Jonli holat")
    async def live_button(message: Message):
        if message.from_user.id not in REVIEWER_IDS:
            await message.answer("Bu bo‘lim faqat nazoratchilar uchun.")
            return
        rows = attendance_rows(day_key())
        last = {row["telegram_id"]: row for row in rows}
        lines = [f"📍 Bugungi jonli holat — {day_key()}"]
        lines.extend(f"• {u['full_name']}: {last[u['telegram_id']]['kind']} {hhmm(last[u['telegram_id']]['happened_at'])}" if u["telegram_id"] in last else f"• {u['full_name']}: hali belgi yo‘q" for u in all_users())
        await message.answer("\n".join(lines), reply_markup=main_keyboard(message.from_user.id))

    @dp.message(F.text == "👥 Xodimlar")
    async def employees_button(message: Message):
        await send_employee_directory(message)

    @dp.message(F.text == "🧑‍💼 Yuz tasdiqlari")
    async def face_reviews_button(message: Message):
        if message.from_user.id not in REVIEWER_IDS:
            await message.answer("Bu bo‘lim faqat nazoratchilar uchun.")
            return
        rows = pending_users()
        if not rows:
            await message.answer("Yuz tasdig‘ini kutayotgan xodim yo‘q.", reply_markup=main_keyboard(message.from_user.id))
            return
        await message.answer("⏳ Yuz tasdig‘ini kutayotganlar:", reply_markup=main_keyboard(message.from_user.id))
        for row in rows:
            if row["telegram_id"] == message.from_user.id:
                await message.answer("O‘z yuzingizni tasdiqlay olmaysiz. Boshqa nazoratchi ko‘rib chiqadi.")
            await send_face_review(message.bot, row)

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
            await message.answer("Ro‘yxatdagi yuz suratingiz nazoratchi tasdig‘ini kutmoqda. Tasdiqlangach bot sizga xabar beradi.")
            return
        if now_local().time() >= time(9, 30) and not get_event(uid, "checkin") and not get_explanation(uid) and get_user(uid):
            await record_reason(message, continue_checkin=False)
            return
        await message.answer("Kerakli davomat tugmasini tanlang yoki /help buyrug‘ini yuboring.", reply_markup=main_keyboard(message.from_user.id))

    @dp.message()
    async def unknown_message(message: Message):
        await message.answer("Xabar turi tushunarsiz. /help buyrug‘idan foydalaning.")

    api_runner = None
    scheduler_task = asyncio.create_task(scheduler(bot))
    warmup_task = asyncio.create_task(asyncio.to_thread(warm_up_face_model))
    try:
        if WEBAPP_API_ENABLED:
            parsed_url = urlsplit(WEBAPP_URL)
            if parsed_url.scheme != "https" or not parsed_url.netloc:
                raise RuntimeError("WEBAPP_URL must be a public HTTPS URL before enabling the Web App API.")
            expected_origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
            api_origin = WEBAPP_ORIGIN or expected_origin
            if api_origin != expected_origin:
                raise RuntimeError("WEBAPP_ORIGIN must exactly match the HTTPS origin in WEBAPP_URL.")
            if WEBAPP_API_HOST not in {"127.0.0.1", "localhost", "::1"}:
                raise RuntimeError("WEBAPP_API_HOST must remain on loopback; expose it only through the approved HTTPS proxy.")
            from webapp_api import build_api_app, start_api
            api_app = build_api_app(
                token=BOT_TOKEN,
                allowed_origin=api_origin,
                office_lat=OFFICE_LAT,
                office_lon=OFFICE_LON,
                office_radius_m=GEOFENCE_METERS,
                temp_dir=TMP_DIR,
                get_user=get_user,
                get_event=get_event,
                get_explanation=get_explanation,
                add_event=add_webapp_event,
                compare_face=compare_face,
                notify_admins=lambda text: notify_admins(bot, text),
                format_name=format_name,
                late_minutes=late_minutes,
                hhmm=hhmm,
                now_local=now_local,
                distance_from_office=distance_from_office,
            )
            api_runner = await start_api(api_app, host=WEBAPP_API_HOST, port=WEBAPP_API_PORT)
            LOG.info("Web App API listening on %s:%s (loopback)", WEBAPP_API_HOST, WEBAPP_API_PORT)
        try:
            await bot.set_my_commands([])
        except Exception as exc:
            LOG.warning("Could not clear Telegram slash-command menu: %s", exc)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler_task.cancel()
        if api_runner is not None:
            await api_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    init_db()
    asyncio.run(main())
