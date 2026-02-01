import os
import random
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

from aiohttp import web

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, KeyboardButton, Message
from aiogram.filters import Command

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

print("DEBUG CHANNEL_ID =", CHANNEL_ID)

if not TOKEN:
    raise SystemExit("BOT_TOKEN не задан в .env")
if not CHANNEL_ID:
    raise SystemExit("CHANNEL_ID не задан в .env")


TOKEN = "8597860061:AAGavbkgq6THU-73tkzwzbR-pRXwIJr56Nc"
CHANNEL_ID = -1003592743906

print("DEBUG CHANNEL_ID =", CHANNEL_ID)

if not TOKEN:
    raise SystemExit("BOT_TOKEN не задан в .env")
if not CHANNEL_ID:
    raise SystemExit("CHANNEL_ID не задан в .env")
bot = Bot(token=TOKEN)
dp = Dispatcher()
keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌅 Доброе утро")],
        [KeyboardButton(text="🌆 Добрый вечер")],
        [KeyboardButton(text="🌙 Спокойной ночи")],
        [KeyboardButton(text="🎂 С днём рождения")]
    ],
    resize_keyboard=True
)# ===== НАСТРОЙКИ ОТКРЫТОК И РАСПИСАНИЯ =====

BASE_DIR = Path(__file__).parent
CARDS_DIR = BASE_DIR / "cards"

SUPPORTED_EXT = {".png", ".jpg", ".jpeg", ".webp"}

# ВРЕМЯ РАССЫЛКИ (по времени Windows)
MORNING_TIME = "07:30"
EVENING_TIME = "18:30"
NIGHT_TIME   = "22:30"
BIRTHDAY_CHECK_TIME = "12:00"

# birthday: примерно раз в 3–4 дня
BIRTHDAY_MIN_H = 72
BIRTHDAY_MAX_H = 96
BIRTHDAY_P = 0.60

sent_cache = {
    "morning": set(),
    "evening": set(),
    "night": set(),
    "birthday": set()
}

last_birthday_utc = None

TZ = ZoneInfo("Etc/GMT-3")  # UTC+3
scheduler = AsyncIOScheduler(timezone=TZ)
DB_PATH = BASE_DIR / "bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sent (
            category TEXT NOT NULL,
            file TEXT NOT NULL,
            PRIMARY KEY (category, file)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()

def list_images(category: str):
    folder = CARDS_DIR / category
    if not folder.exists():
        return []
    files = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXT
    ]
    files.sort()
    return files

from aiohttp import web

async def start_web_server():
    app = web.Application()

    async def health(request):
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"[OK] Web server listening on port {port}")

def parse_hhmm(hhmm: str):
    h, m = hhmm.split(":")
    return int(h), int(m)


def now_utc():
    return datetime.now(timezone.utc)


async def send_random(category: str):
    files = list_images(category)
    if not files:
        print(f"[WARN] Папка cards/{category} пуста")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # какие файлы уже отправляли
    cur.execute(
        "SELECT file FROM sent WHERE category=?",
        (category,)
    )
    sent_files = {row[0] for row in cur.fetchall()}

    # выбираем неотправленные
    unsent = [p for p in files if str(p) not in sent_files]

    # если всё отправили — начинаем новый круг
    if not unsent:
        cur.execute("DELETE FROM sent WHERE category=?", (category,))
        conn.commit()
        unsent = files[:]

    chosen = random.choice(unsent)

    silent = category in ("night", "birthday")
    await bot.send_photo(
        CHANNEL_ID,
        FSInputFile(chosen),
        disable_notification=silent
    )

    cur.execute(
        "INSERT OR IGNORE INTO sent(category, file) VALUES (?, ?)",
        (category, str(chosen))
    )
    conn.commit()
    conn.close()

    print(f"[OK] Отправлено {category}: {chosen.name}")


async def birthday_should_send():
    global last_birthday_utc

    if last_birthday_utc is None:
        return True

    elapsed = now_utc() - last_birthday_utc

    if elapsed >= timedelta(hours=BIRTHDAY_MAX_H):
        return True

    if elapsed < timedelta(hours=BIRTHDAY_MIN_H):
        return False

    return random.random() < BIRTHDAY_P


def get_last_birthday():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key='last_birthday'")
    row = cur.fetchone()
    conn.close()
    if row:
        return datetime.fromisoformat(row[0])
    return None


def set_last_birthday(dt: datetime):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('last_birthday', ?)",
        (dt.isoformat(),)
    )
    conn.commit()
    conn.close()


async def send_birthday_if_due():
    last = get_last_birthday()
    now = datetime.now(timezone.utc)

    if last:
        elapsed = now - last
        if elapsed < timedelta(hours=72):
            return
        if elapsed < timedelta(hours=96) and random.random() > 0.6:
            return

    await send_random("birthday")
    set_last_birthday(now)

def setup_schedule():
    h, m = parse_hhmm(MORNING_TIME)
    scheduler.add_job(
        send_random,
        trigger=CronTrigger(hour=h, minute=m, timezone=TZ),
        args=("morning",),
        id="morning",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    h, m = parse_hhmm(EVENING_TIME)
    scheduler.add_job(
        send_random,
        trigger=CronTrigger(hour=h, minute=m, timezone=TZ),
        args=("evening",),
        id="evening",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    h, m = parse_hhmm(NIGHT_TIME)
    scheduler.add_job(
        send_random,
        trigger=CronTrigger(hour=h, minute=m, timezone=TZ),
        args=("night",),
        id="night",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    h, m = parse_hhmm(BIRTHDAY_CHECK_TIME)
    scheduler.add_job(
        send_birthday_if_due,
        trigger=CronTrigger(hour=h, minute=m, timezone=TZ),
        id="birthday_check",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    scheduler.start()
    print("[OK] Планировщик запущен, TZ =", TZ)
    for job in scheduler.get_jobs():
        print("[JOB]", job.id, "next:", job.next_run_time)

@dp.message(Command("test_night"))
async def test_night(message: Message):
    await send_random("night")
    await message.answer("Тест: отправила ночную открытку в канал 🌙")

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Выбери категорию открыток 👇",
        reply_markup=keyboard
    )
@dp.message(lambda msg: msg.text == "🌅 Доброе утро")
async def morning(message: Message):
    await message.answer("Ты нажала: Доброе утро 🌅")

@dp.message(lambda msg: msg.text == "🌆 Добрый вечер")
async def evening(message: Message):
    await message.answer("Ты нажала: Добрый вечер 🌆")

@dp.message(lambda msg: msg.text == "🌙 Спокойной ночи")
async def night(message: Message):
    await message.answer("Ты нажала: Спокойной ночи 🌙")

@dp.message(lambda msg: msg.text == "🎂 С днём рождения")
async def birthday(message: Message):
    await message.answer("Ты нажала: С днём рождения 🎂")
async def main():
    init_db()
    setup_schedule()
    await start_web_server() 
    await dp.start_polling(bot)



if __name__ == "__main__":
    asyncio.run(main())

