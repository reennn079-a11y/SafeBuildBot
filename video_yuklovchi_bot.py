"""
Telegram bot: video yuklash (Instagram, YouTube, TikTok va boshqalar)
va qo'shiq qidirish (faqat Creative Commons litsenziyali YouTube audiolari).

O'rnatish:
    pip install aiogram yt-dlp
    ffmpeg o'rnatilgan bo'lishi kerak (sudo apt install ffmpeg)

Ishga tushirish:
    export BOT_TOKEN="@BotFather bergan token"
    python bot.py
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from yt_dlp import YoutubeDL

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
# Oddiy Telegram serveri: 50 MB. O'z Local Bot API serveringiz bo'lsa: 2000 MB (bot uchun maksimum).
LOCAL_API = os.getenv("LOCAL_API_URL")  # masalan: http://localhost:8081
MAX_MB = 2000 if LOCAL_API else 50
MAX_BYTES = MAX_MB * 1024 * 1024
URL_RE = re.compile(r"https?://\S+")

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari


# ---------- yt-dlp yordamchilari (bloklovchi, thread'da ishlaydi) ----------

def _normalize(url: str) -> str:
    m = re.search(r"youtube\.com/shorts/([\w-]{11})", url)
    return f"https://www.youtube.com/watch?v={m.group(1)}" if m else url


def _download_video(url: str, folder: str) -> Path:
    url = _normalize(url)
    opts = {
        "outtmpl": f"{folder}/%(id)s.%(ext)s",
        "format": f"best[ext=mp4][filesize<=?{MAX_MB}M]/best[filesize<=?{MAX_MB}M]/best[ext=mp4]/best",
        "max_filesize": MAX_BYTES,
        "noplaylist": True,
        "quiet": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


JAMENDO_ID = os.getenv("JAMENDO_ID", "BU_YERGA_CLIENT_ID")  # devportal.jamendo.com


def _jamendo(**params) -> list[dict]:
    q = urlencode({"client_id": JAMENDO_ID, "format": "json", **params})
    req = Request(f"https://api.jamendo.com/v3.0/tracks/?{q}", headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=20) as r:
        return json.load(r).get("results", [])


def _search_jamendo(query: str) -> list[dict]:
    """Jamendo: qonuniy bepul musiqalar."""
    tracks = _jamendo(search=query, limit=30, audiodownload_allowed="true")
    return [
        {"id": "j" + t["id"], "title": f'{t["artist_name"]} - {t["name"]}', "duration": t.get("duration")}
        for t in tracks
    ]


def _download_jamendo(track_id: str, folder: str) -> tuple[Path, str]:
    t = _jamendo(id=track_id)[0]
    path = Path(folder) / f"{track_id}.mp3"
    req = Request(t["audio"], headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=60) as r, open(path, "wb") as f:
        f.write(r.read())
    return path, f'{t["artist_name"]} - {t["name"]}'


def _search_youtube_cc(query: str) -> list[dict]:
    """YouTube'ning Creative Commons filtri bilan qidiradi."""
    url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIwAQ%3D%3D"
    opts = {"quiet": True, "skip_download": True, "extract_flat": True, "playlistend": 40}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    result = []
    for e in info.get("entries", []) or []:
        vid = e.get("id") or ""
        if len(vid) == 11 and e.get("title"):
            result.append({"id": "y" + vid, "title": e["title"], "duration": e.get("duration")})
        if len(result) == 30:
            break
    return result


def _download_youtube_audio(video_id: str, folder: str) -> tuple[Path, str]:
    opts = {
        "outtmpl": f"{folder}/%(id)s.%(ext)s",
        "format": "bestaudio[ext=m4a]/bestaudio",
        "max_filesize": MAX_BYTES,
        "quiet": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        path = Path(ydl.prepare_filename(info))
    return path, info.get("title", "audio")


def _search_cc_music(query: str) -> list[dict]:
    tracks = []
    try:
        tracks = _search_youtube_cc(query)
    except Exception:
        logging.exception("youtube search error")
    if not tracks and not JAMENDO_ID.startswith("BU_YERGA"):
        tracks = _search_jamendo(query)
    return tracks


def _download_audio(track_id: str, folder: str) -> tuple[Path, str]:
    if track_id.startswith("y"):
        return _download_youtube_audio(track_id[1:], folder)
    return _download_jamendo(track_id[1:], folder)


# ---------- Baza (SQLite), adminlar, sozlamalar ----------

DB = sqlite3.connect("bot.db", check_same_thread=False)
DB.executescript(
    """
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS channels(chat TEXT PRIMARY KEY, title TEXT, link TEXT);
    """
)
for _admin_id in ADMIN_IDS:
    DB.execute("INSERT OR IGNORE INTO admins VALUES(?)", (_admin_id,))
DB.commit()

DEFAULT_WELCOME = (
    "Salom! 👋\n\n"
    "🎬 Video yuklash: Instagram, YouTube, TikTok va boshqa havolani yuboring.\n"
    "🎵 Qo'shiq qidirish: shunchaki qo'shiq nomini yozing\n\n"
    "⚠️ Faqat o'zingizga tegishli yoki yuklashga ruxsat berilgan kontentni "
    "yuklang. Musiqa qidiruvi faqat Creative Commons litsenziyali "
    "audiolarni topadi."
)


def is_admin(uid: int) -> bool:
    return DB.execute("SELECT 1 FROM admins WHERE id=?", (uid,)).fetchone() is not None


def get_setting(key: str, default: str = "") -> str:
    row = DB.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str):
    DB.execute("INSERT OR REPLACE INTO settings VALUES(?, ?)", (key, value))
    DB.commit()


class TrackUsers(BaseMiddleware):
    """Botga yozgan har bir foydalanuvchini bazaga qo'shadi."""

    async def __call__(self, handler, event: Message, data):
        if event.from_user and event.chat.type == "private":
            DB.execute("INSERT OR IGNORE INTO users VALUES(?)", (event.from_user.id,))
            DB.commit()
        return await handler(event, data)


dp.message.outer_middleware(TrackUsers())

# ---------- Admin panel (faqat adminlarga ko'rinadi) ----------

ADMIN = F.from_user.id.func(is_admin)
BG_TASKS: set = set()


class AdminSt(StatesGroup):
    broadcast = State()
    confirm = State()
    new_admin = State()
    welcome = State()
    channel = State()
    caption = State()


def panel_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="ad:bc")],
        [InlineKeyboardButton(text="👋 Kirish matni", callback_data="ad:welcome")],
        [InlineKeyboardButton(text="✍️ Video osti matni", callback_data="ad:cap")],
        [InlineKeyboardButton(text="📣 Majburiy obuna", callback_data="ad:ch")],
        [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="ad:add")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="ad:stats")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("admin"), ADMIN)
async def admin_panel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 Admin panel", reply_markup=panel_kb())


@dp.message(Command("cancel"), ADMIN)
async def admin_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.", reply_markup=panel_kb())


@dp.callback_query(F.data == "ad:stats", ADMIN)
async def ad_stats(c: CallbackQuery):
    users = DB.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    admins = DB.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    await c.message.answer(f"👥 Foydalanuvchilar: {users}\n🛠 Adminlar: {admins}")
    await c.answer()


# --- Xabar yuborish (istalgan turdagi media) ---

@dp.callback_query(F.data == "ad:bc", ADMIN)
async def ad_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.broadcast)
    await c.message.answer(
        "Barcha foydalanuvchilarga yuboriladigan xabarni yuboring "
        "(matn, rasm, video, audio, fayl, ovozli xabar va h.k.).\n"
        "Bekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.broadcast, ADMIN)
async def ad_bc_preview(m: Message, state: FSMContext):
    await state.update_data(chat_id=m.chat.id, msg_id=m.message_id)
    await state.set_state(AdminSt.confirm)
    await m.answer("👆 Shu xabar hammaga yuboriladi. Tasdiqlaysizmi?")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Yuborish", callback_data="ad:go"),
            InlineKeyboardButton(text="❌ Bekor", callback_data="ad:no"),
        ]]
    )
    await m.answer("Tasdiqlang:", reply_markup=kb)


async def _broadcast(bot: Bot, admin_id: int, from_chat: int, msg_id: int):
    ok = fail = 0
    for (uid,) in DB.execute("SELECT id FROM users").fetchall():
        try:
            await bot.copy_message(uid, from_chat, msg_id)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(uid, from_chat, msg_id)
                ok += 1
            except Exception:
                fail += 1
        except Exception:
            fail += 1  # bot bloklangan yoki chat topilmadi
        await asyncio.sleep(0.05)
    await bot.send_message(admin_id, f"✅ Yuborildi: {ok}\n❌ Yetib bormadi: {fail}")


@dp.callback_query(F.data == "ad:go", AdminSt.confirm, ADMIN)
async def ad_go(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await c.message.edit_text("📤 Yuborish boshlandi, tugagach xabar beraman.")
    task = asyncio.create_task(
        _broadcast(c.bot, c.from_user.id, data["chat_id"], data["msg_id"])
    )
    BG_TASKS.add(task)
    task.add_done_callback(BG_TASKS.discard)
    await c.answer()


@dp.callback_query(F.data == "ad:no", ADMIN)
async def ad_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Bekor qilindi.")
    await c.answer()


# --- Admin qo'shish ---

@dp.callback_query(F.data == "ad:add", ADMIN)
async def ad_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.new_admin)
    await c.message.answer("Yangi adminning Telegram ID raqamini yuboring.\nBekor qilish: /cancel")
    await c.answer()


@dp.message(AdminSt.new_admin, ADMIN)
async def ad_add_save(m: Message, state: FSMContext):
    if not m.text or not m.text.strip().isdigit():
        return await m.answer("ID faqat raqamlardan iborat bo'lishi kerak. Qayta yuboring.")
    DB.execute("INSERT OR IGNORE INTO admins VALUES(?)", (int(m.text.strip()),))
    DB.commit()
    await state.clear()
    await m.answer(f"✅ {m.text.strip()} admin qilindi.", reply_markup=panel_kb())


# --- Kirish matni ---

@dp.callback_query(F.data == "ad:welcome", ADMIN)
async def ad_welcome(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.welcome)
    await c.message.answer(
        "Yangi kirish (/start) matnini yuboring.\n"
        "Qalin, qiya kabi formatlashdan foydalansangiz bo'ladi.\n"
        "Bekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.welcome, ADMIN, F.text)
async def ad_welcome_save(m: Message, state: FSMContext):
    set_setting("welcome", m.html_text)
    await state.clear()
    await m.answer("✅ Kirish matni saqlandi.", reply_markup=panel_kb())


# --- Video/audio ostidagi matn (caption) ---

def caption_or_none() -> str | None:
    return get_setting("caption") or None


@dp.callback_query(F.data == "ad:cap", ADMIN)
async def ad_caption(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.caption)
    current = get_setting("caption") or "yo'q"
    await c.message.answer(
        f"Hozirgi matn: {current}\n\n"
        "Yuklangan video va audio ostida chiqadigan yangi matnni yuboring "
        "(masalan: «📥 @botingiz orqali yuklandi»). Formatlash ishlaydi, "
        "maksimum 1000 belgi.\n"
        "O'chirish uchun «-» yuboring. Bekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.caption, ADMIN, F.text)
async def ad_caption_save(m: Message, state: FSMContext):
    if m.text.strip() == "-":
        set_setting("caption", "")
        await state.clear()
        return await m.answer("✅ Video osti matni o'chirildi.", reply_markup=panel_kb())
    if len(m.text) > 1000:
        return await m.answer("Matn juda uzun (1000 belgidan oshmasin). Qayta yuboring.")
    set_setting("caption", m.html_text)
    await state.clear()
    await m.answer("✅ Saqlandi. Endi yuklangan videolar ostida shu matn chiqadi.", reply_markup=panel_kb())


# ---------- Majburiy obuna ----------

def channels() -> list[tuple]:
    return DB.execute("SELECT chat, title, link FROM channels").fetchall()


async def _is_member(bot: Bot, chat: str, uid: int) -> bool:
    try:
        cm = await bot.get_chat_member(int(chat), uid)
    except Exception:
        logging.warning("Obunani tekshirib bo'lmadi (%s). Bot kanalda admin ekanini tekshiring.", chat)
        return True  # tekshirib bo'lmasa, foydalanuvchini bloklamaymiz
    if cm.status in ("member", "administrator", "creator"):
        return True
    return cm.status == "restricted" and getattr(cm, "is_member", False)


async def missing_channels(bot: Bot, uid: int) -> list[tuple]:
    return [c for c in channels() if not await _is_member(bot, c[0], uid)]


def sub_kb(missing: list[tuple]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📢 {t}", url=link)] for _, t, link in missing]
    rows.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="chk")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SUB_TEXT = "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling, so'ng «Tekshirish» tugmasini bosing:"


class ForceSub(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        is_cb = isinstance(event, CallbackQuery)
        private = is_cb or event.chat.type == "private"
        if (
            not user
            or not private
            or is_admin(user.id)
            or (is_cb and event.data == "chk")
            or not channels()
        ):
            return await handler(event, data)
        missing = await missing_channels(data["bot"], user.id)
        if not missing:
            return await handler(event, data)
        if is_cb:
            await event.answer("Avval kanallarga obuna bo'ling.", show_alert=True)
            return await event.message.answer(SUB_TEXT, reply_markup=sub_kb(missing))
        return await event.answer(SUB_TEXT, reply_markup=sub_kb(missing))


dp.message.outer_middleware(ForceSub())
dp.callback_query.outer_middleware(ForceSub())


@dp.callback_query(F.data == "chk")
async def check_sub(c: CallbackQuery):
    missing = await missing_channels(c.bot, c.from_user.id)
    if missing:
        return await c.answer("Hali hamma kanalga obuna bo'lmagansiz.", show_alert=True)
    await c.message.delete()
    await c.message.answer(get_setting("welcome", DEFAULT_WELCOME), parse_mode="HTML")
    await c.answer()


# --- Adminlar uchun: kanal qo'shish / o'chirish ---

def _channels_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🗑 {t}", callback_data=f"ad:chdel:{chat}")]
        for chat, t, _ in channels()
    ]
    rows.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="ad:chadd")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "ad:ch", ADMIN)
async def ad_channels(c: CallbackQuery):
    n = len(channels())
    await c.message.answer(
        f"📣 Majburiy obuna kanallari: {n}\n"
        "Kanal o'chirish uchun 🗑 tugmasini bosing.",
        reply_markup=_channels_kb(),
    )
    await c.answer()


@dp.callback_query(F.data.startswith("ad:chdel:"), ADMIN)
async def ad_channel_del(c: CallbackQuery):
    DB.execute("DELETE FROM channels WHERE chat=?", (c.data.split(":", 2)[2],))
    DB.commit()
    await c.message.edit_reply_markup(reply_markup=_channels_kb())
    await c.answer("O'chirildi")


@dp.callback_query(F.data == "ad:chadd", ADMIN)
async def ad_channel_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.channel)
    await c.message.answer(
        "Avval botni kanalga ADMIN qilib qo'shing, so'ng yuboring:\n\n"
        "• Ochiq kanal: @kanal_username\n"
        "• Yopiq kanal: -1001234567890 https://t.me/+taklif_havola\n\n"
        "Bekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.channel, ADMIN, F.text)
async def ad_channel_save(m: Message, state: FSMContext):
    parts = m.text.split()
    ref = parts[0]
    link = parts[1] if len(parts) > 1 else (f"https://t.me/{ref[1:]}" if ref.startswith("@") else None)
    if not link:
        return await m.answer("Yopiq kanal uchun taklif havolasini ham yozing. Qayta yuboring.")
    try:
        chat = await m.bot.get_chat(ref if ref.startswith("@") else int(ref))
        me = await m.bot.get_chat_member(chat.id, (await m.bot.me()).id)
        if me.status not in ("administrator", "creator"):
            return await m.answer("Bot bu kanalda admin emas. Admin qiling va qayta yuboring.")
    except Exception as e:
        return await m.answer(f"Kanal topilmadi yoki bot unda yo'q: {str(e)[:150]}")
    DB.execute(
        "INSERT OR REPLACE INTO channels VALUES(?, ?, ?)", (str(chat.id), chat.title or ref, link)
    )
    DB.commit()
    await state.clear()
    await m.answer(f"✅ «{chat.title}» qo'shildi.", reply_markup=panel_kb())


# ---------- Bot handlerlari ----------

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(get_setting("welcome", DEFAULT_WELCOME), parse_mode="HTML")


@dp.message(Command("music"))
async def music(m: Message, command):
    query = (command.args or "").strip()
    if not query:
        return await m.answer("Qo'shiq nomini yozing, masalan: lofi chill")
    await do_search(m, query)


PER_PAGE = 10
SEARCHES: dict[tuple, dict] = {}  # (chat id, xabar id) -> {"query", "tracks", "page"}


def _fmt(sec) -> str:
    if not sec:
        return ""
    return f" {int(sec) // 60}:{int(sec) % 60:02d}"


def _render(state: dict):
    tracks, page = state["tracks"], state["page"]
    chunk = tracks[page * PER_PAGE:(page + 1) * PER_PAGE]
    lines = [f"🔍 {state['query']}", ""]
    lines += [f"{i}. {t['title']}{_fmt(t.get('duration'))}" for i, t in enumerate(chunk, 1)]
    nums = [
        InlineKeyboardButton(text=str(i), callback_data=f"n:{page * PER_PAGE + i - 1}")
        for i in range(1, len(chunk) + 1)
    ]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    rows.append([
        InlineKeyboardButton(text="⬅️", callback_data="p:-1"),
        InlineKeyboardButton(text="❌", callback_data="x"),
        InlineKeyboardButton(text="➡️", callback_data="p:1"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def do_search(m: Message, query: str):
    wait = await m.answer("🔎 Qidirilmoqda...")
    try:
        tracks = await asyncio.to_thread(_search_cc_music, query)
    except Exception as e:
        logging.exception("search error")
        return await wait.edit_text(f"Qidirishda xatolik: {str(e)[:200]}")
    if not tracks:
        return await wait.edit_text("Natija topilmadi. Boshqa so'z bilan urinib ko'ring.")
    state = {"query": query, "tracks": tracks, "page": 0}
    if len(SEARCHES) > 500:                      # xotira to'lib ketmasin
        SEARCHES.pop(next(iter(SEARCHES)))
    SEARCHES[(wait.chat.id, wait.message_id)] = state
    text, kb = _render(state)
    await wait.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("p:"))
async def paginate(c: CallbackQuery):
    state = SEARCHES.get((c.message.chat.id, c.message.message_id))
    if not state:
        return await c.answer("Qidiruv eskirgan, qaytadan yozing.", show_alert=True)
    pages = (len(state["tracks"]) - 1) // PER_PAGE + 1
    state["page"] = (state["page"] + int(c.data[2:])) % pages
    text, kb = _render(state)
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data == "x")
async def close(c: CallbackQuery):
    SEARCHES.pop((c.message.chat.id, c.message.message_id), None)
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.answer()


@dp.callback_query(F.data.startswith("n:"))
async def pick(c: CallbackQuery):
    state = SEARCHES.get((c.message.chat.id, c.message.message_id))
    if not state:
        return await c.answer("Qidiruv eskirgan, qaytadan yozing.", show_alert=True)
    track = state["tracks"][int(c.data[2:])]
    await c.answer("Yuklanmoqda...")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path, title = await asyncio.to_thread(_download_audio, track["id"], tmp)
            await c.message.answer_audio(FSInputFile(path), title=title, caption=caption_or_none(), parse_mode="HTML")
        except Exception:
            logging.exception("audio error")
            await c.message.answer("Audioni yuklab bo'lmadi (hajmi katta bo'lishi mumkin).")


@dp.message(F.text.regexp(URL_RE))
async def video(m: Message):
    url = URL_RE.search(m.text).group(0)
    wait = await m.answer("⏳ Yuklanmoqda...")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = await asyncio.to_thread(_download_video, url, tmp)
            if path.stat().st_size > MAX_BYTES:
                return await wait.edit_text(f"Video {MAX_MB} MB dan katta, yubora olmayman.")
            await m.answer_video(FSInputFile(path), caption=caption_or_none(), parse_mode="HTML")
            await wait.delete()
        except Exception as e:
            logging.exception("video error")
            text = "Yuklab bo'lmadi. Havola ochiq (public) ekanini tekshiring."
            if is_admin(m.from_user.id):
                text += f"\n\n🛠 {str(e)[:300]}"
            await wait.edit_text(text)


@dp.message(F.text & ~F.text.startswith("/"))
async def text_search(m: Message):
    await do_search(m, m.text.strip())


async def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")
    session = None
    if LOCAL_API:
        session = AiohttpSession(api=TelegramAPIServer.from_base(LOCAL_API, is_local=True), timeout=3600)
    await dp.start_polling(Bot(TOKEN, session=session))


if __name__ == "__main__":
    asyncio.run(main())
