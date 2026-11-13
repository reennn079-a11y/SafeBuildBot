"""
Super yuklovchi bot: yuklash + qo'shiq topish + MP3 — hammasi bir botda.
(Video yuklovchi botdagi barcha funksiyalar saqlangan: admin panel, xabar yuborish, kirish matni, video osti matni,
majburiy obuna, /music qidiruvi, 2000 MB Local Bot API — va yangilari qo'shilgan.)

Talablar: pip install aiogram yt-dlp shazamio aiohttp; ffmpeg o'rnatilgan bo'lsin.
Muhit: BOT_TOKEN, ADMIN_IDS (konstruktor beradi). Ixtiyoriy:
  IG_COOKIES   — Instagram hikoyalari va yopiq postlar uchun cookies.txt yo'li (Netscape format)
  LOCAL_API_URL— Local Bot API (2000 MB gacha)
  FREE_DAILY   — bepul foydalanuvchiga kuniga nechta yangi yuklash (0 = cheksiz, standart 10)
  PLUS_STARS   — Plus narxi Stars bilan: "30:150,90:400,365:1300"
  JAMENDO_ID   — Creative Commons musiqa zaxira manbasi
  WEB_PORT, WEB_KEY, WEB_PUBLIC_URL — web admin panel (konstruktor beradi, bo'lmasa o'chiq)
Inline qidiruv uchun @BotFather: /setinline, hamda /setinlinefeedback → Enabled (100%).
"""
import asyncio
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.types import (CallbackQuery, ChosenInlineResult, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           InlineQuery, InlineQueryResultArticle, InlineQueryResultCachedAudio, InputMediaAudio,
                           InputMediaPhoto, InputMediaVideo, InputTextMessageContent, LabeledPrice, Message,
                           PreCheckoutQuery, ReactionTypeEmoji)
from yt_dlp import YoutubeDL

import yuklovchi_core as core
from yuklovchi_tx import EN, RU, UZ

try:
    import botlang
except Exception:                                   # til moduli bo'lmasa — faqat o'zbekcha
    botlang = None

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
LOCAL_API = os.getenv("LOCAL_API_URL")
MAX_MB = 2000 if LOCAL_API else 50
MAX_BYTES = MAX_MB * 1024 * 1024
COOKIES = os.getenv("IG_COOKIES")
JAMENDO_ID = os.getenv("JAMENDO_ID", "")
DL_SLOTS = asyncio.Semaphore(int(os.getenv("DL_CONCURRENCY", "3")))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("yuklovchi")
dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari (+ Pro va yuqorida til tanlash)

# ====================================================================== baza
DB = sqlite3.connect("bot.db", check_same_thread=False)
DB.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, joined INTEGER DEFAULT 0, plus_until INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS channels(chat TEXT PRIMARY KEY, title TEXT, link TEXT);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, kind TEXT, file_id TEXT, title TEXT, performer TEXT, duration INTEGER);
CREATE TABLE IF NOT EXISTS links(id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT);
CREATE TABLE IF NOT EXISTS songs(id INTEGER PRIMARY KEY AUTOINCREMENT, q TEXT, title TEXT, artist TEXT, lyrics TEXT);
CREATE TABLE IF NOT EXISTS usage(day TEXT, uid INTEGER, n INTEGER DEFAULT 0, PRIMARY KEY(day, uid));
CREATE TABLE IF NOT EXISTS ads(id INTEGER PRIMARY KEY AUTOINCREMENT, src_chat INTEGER, src_msg INTEGER, btn_text TEXT, btn_url TEXT,
                               active INTEGER DEFAULT 1, views INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, stars INTEGER, days INTEGER, at INTEGER);
CREATE TABLE IF NOT EXISTS counters(uid INTEGER PRIMARY KEY, n INTEGER DEFAULT 0);
""")
for _a in ADMIN_IDS:
    DB.execute("INSERT OR IGNORE INTO admins VALUES(?)", (_a,))
DB.commit()


def is_admin(uid: int) -> bool:
    return DB.execute("SELECT 1 FROM admins WHERE id=?", (uid,)).fetchone() is not None


def get_setting(key, default=""):
    r = DB.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(key, value):
    DB.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))
    DB.commit()


def free_daily() -> int:
    try:
        return int(get_setting("free_daily", os.getenv("FREE_DAILY", "10")))
    except ValueError:
        return 10


def ad_every() -> int:
    try:
        return int(get_setting("ad_every", "3"))
    except ValueError:
        return 3


def plus_prices() -> dict:
    raw = get_setting("plus_prices", os.getenv("PLUS_STARS", "30:150,90:400,365:1300"))
    out = {}
    for part in raw.split(","):
        if ":" in part:
            d, s = part.split(":", 1)
            if d.strip().isdigit() and s.strip().isdigit():
                out[int(d)] = int(s)
    return out or {30: 150}


def is_plus(uid: int) -> bool:
    r = DB.execute("SELECT plus_until FROM users WHERE id=?", (uid,)).fetchone()
    return bool(r and r[0] > time.time())


def plus_until(uid: int) -> int:
    r = DB.execute("SELECT plus_until FROM users WHERE id=?", (uid,)).fetchone()
    return r[0] if r else 0


def grant_plus(uid: int, days: int):
    DB.execute("INSERT OR IGNORE INTO users(id, joined) VALUES(?,?)", (uid, int(time.time())))
    base = max(plus_until(uid), int(time.time()))
    DB.execute("UPDATE users SET plus_until=? WHERE id=?", (base + days * 86400, uid))
    DB.commit()
    return base + days * 86400


def used_today(uid: int) -> int:
    r = DB.execute("SELECT n FROM usage WHERE day=? AND uid=?", (time.strftime("%Y-%m-%d"), uid)).fetchone()
    return r[0] if r else 0


def can_download(uid: int) -> bool:
    lim = free_daily()
    return is_admin(uid) or is_plus(uid) or lim <= 0 or used_today(uid) < lim


def consume(uid: int):
    DB.execute("INSERT INTO usage(day, uid, n) VALUES(?,?,1) ON CONFLICT(day, uid) DO UPDATE SET n=n+1", (time.strftime("%Y-%m-%d"), uid))
    DB.execute("DELETE FROM usage WHERE day < ?", (time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400)),))
    DB.commit()


# ====================================================================== matnlar (4 til)
def lang_of(uid) -> str:
    return botlang.lang_of(uid) if botlang else "uz"


def T(uid, key, **kw) -> str:
    lang = lang_of(uid)
    if lang == "ru":
        tpl = RU[key]
    elif lang == "en":
        tpl = EN[key]
    elif lang == "uzc" and botlang:
        tpl = botlang.translit(UZ[key])
    else:
        tpl = UZ[key]
    return tpl.format(**kw) if kw else tpl


def caption_or_none():
    return get_setting("caption") or None


def welcome_text(uid) -> str:
    return get_setting("welcome") or T(uid, "welcome")


def date_str(ts: int) -> str:
    return time.strftime("%d.%m.%Y", time.localtime(ts))


# ====================================================================== foydalanuvchilarni hisobga olish
class TrackUsers(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        if event.from_user and event.chat.type == "private":
            DB.execute("INSERT OR IGNORE INTO users(id, joined) VALUES(?,?)", (event.from_user.id, int(time.time())))
            DB.commit()
        return await handler(event, data)


dp.message.outer_middleware(TrackUsers())


# ====================================================================== majburiy obuna
def channels():
    return DB.execute("SELECT chat, title, link FROM channels").fetchall()


async def _is_member(bot: Bot, chat: str, uid: int) -> bool:
    try:
        cm = await bot.get_chat_member(int(chat), uid)
    except Exception:
        logging.warning("Obunani tekshirib bo'lmadi (%s). Bot kanalda admin ekanini tekshiring.", chat)
        return True
    if cm.status in ("member", "administrator", "creator"):
        return True
    return cm.status == "restricted" and getattr(cm, "is_member", False)


async def missing_channels(bot: Bot, uid: int):
    return [c for c in channels() if not await _is_member(bot, c[0], uid)]


def sub_kb(uid, missing):
    rows = [[InlineKeyboardButton(text=f"📢 {t}", url=link)] for _, t, link in missing]
    rows.append([InlineKeyboardButton(text=T(uid, "check"), callback_data="chk")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class ForceSub(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        is_cb = isinstance(event, CallbackQuery)
        private = is_cb or event.chat.type == "private"
        if (not user or not private or is_admin(user.id) or (is_cb and event.data in ("chk",)) or not channels()
                or (not is_cb and getattr(event, "successful_payment", None))
                or (is_cb and event.data.startswith("bl:"))):
            return await handler(event, data)
        missing = await missing_channels(data["bot"], user.id)
        if not missing:
            return await handler(event, data)
        if is_cb:
            await event.answer(T(user.id, "sub_first"), show_alert=True)
            return await event.message.answer(T(user.id, "subscribe"), reply_markup=sub_kb(user.id, missing))
        return await event.answer(T(user.id, "subscribe"), reply_markup=sub_kb(user.id, missing))


dp.message.outer_middleware(ForceSub())
dp.callback_query.outer_middleware(ForceSub())


@dp.callback_query(F.data == "chk")
async def check_sub(c: CallbackQuery):
    missing = await missing_channels(c.bot, c.from_user.id)
    if missing:
        return await c.answer(T(c.from_user.id, "sub_not_all"), show_alert=True)
    await c.message.delete()
    await c.message.answer(welcome_text(c.from_user.id), parse_mode="HTML")
    await c.answer()


# ====================================================================== yt-dlp yordamchilari (bloklovchi, thread'da)
def _common(folder: str) -> dict:
    o = {"outtmpl": f"{folder}/%(id)s.%(ext)s", "quiet": True, "no_warnings": True, "noplaylist": True,
         "max_filesize": MAX_BYTES, "socket_timeout": 30, "retries": 3}
    if COOKIES and Path(COOKIES).exists():
        o["cookiefile"] = COOKIES
    return o


def _files(folder: str, exts) -> list[Path]:
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in exts and p.is_file())


def _http_get(url: str, dest: Path) -> Path:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def _image_fallback(info: dict, folder: str) -> list[Path]:
    """yt-dlp rasmli postlarda video format topmasa: rasm havolalarini o'zimiz yuklaymiz."""
    out = []
    entries = info.get("entries") or [info]
    for i, e in enumerate(entries[:10]):
        if not e:
            continue
        u = e.get("url") if Path(str(e.get("url", "")).split("?")[0]).suffix.lower() in core.IMG_EXT else None
        if not u and e.get("thumbnails"):
            u = max(e["thumbnails"], key=lambda t: (t.get("width") or 0) * (t.get("height") or 0)).get("url")
        if u:
            try:
                out.append(_http_get(u, Path(folder) / f"img{i}.jpg"))
            except Exception:
                log.exception("image fallback")
    return out


def dl_media(url: str, folder: str, height: str | None = None):
    """Video/rasm/albom yuklaydi. -> (fayllar ro'yxati, info)."""
    pf = core.platform(url)
    o = _common(folder)
    o["noplaylist"] = pf == "youtube"
    o["playlistend"] = 10
    o["ignoreerrors"] = False
    if pf == "youtube":
        h = height or "720"
        o["format"] = (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}][ext=mp4]/b[height<={h}]/b")
        o["merge_output_format"] = "mp4"
        url = f"https://www.youtube.com/watch?v={core.yt_id(url)}"
    else:   # TikTok: suv belgili («download») format tanlanmaydi
        o["format"] = (f"best[format_id!*=download][ext=mp4][filesize<=?{MAX_MB}M]/best[format_id!*=download][filesize<=?{MAX_MB}M]/best[ext=mp4]/best")
    with YoutubeDL(o) as ydl:
        info = ydl.extract_info(url, download=True)
    files = _files(folder, core.VID_EXT) + _files(folder, core.IMG_EXT)
    if not files:
        files = _image_fallback(info, folder)
    return files, info


def yt_info(url: str) -> dict:
    o = _common("/tmp")
    o["skip_download"] = True
    with YoutubeDL(o) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={core.yt_id(url)}", download=False)


def _retag(path: Path, title: str, artist: str):
    """MP3 ID3 teglarini yozadi (muqova saqlanadi)."""
    out = path.with_name("tagged.mp3")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-map", "0", "-c", "copy", "-id3v2_version", "3",
           "-metadata", f"title={title}", "-metadata", f"artist={artist}", str(out)]
    if subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0 and out.exists():
        out.replace(path)


def dl_mp3(target: str, folder: str, title: str = "", artist: str = ""):
    """Muqova, ijrochi va nomi bilan toza MP3. target — YouTube havola yoki «ytsearch1:...».
    -> (mp3 yo'li, muqova yo'li yoki None, nom, ijrochi, davomiylik)"""
    o = _common(folder)
    o.update({"format": "bestaudio/best", "writethumbnail": True,
              "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                                 {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                                 {"key": "EmbedThumbnail"}]})
    with YoutubeDL(o) as ydl:
        info = ydl.extract_info(target, download=True)
        if info.get("entries"):
            info = info["entries"][0]
    mp3 = next(iter(_files(folder, {".mp3"})), None)
    if mp3 is None:
        raise RuntimeError("mp3 topilmadi")
    if not (title and artist):
        a, n = core.split_title(info.get("title", ""), info.get("uploader", ""), info.get("artist", "") or "", info.get("track", "") or "")
        title, artist = title or n, artist or a
    try:
        _retag(mp3, title, artist)
    except Exception:
        log.exception("retag")
    thumb = next(iter(_files(folder, {".jpg", ".jpeg", ".png", ".webp"})), None)
    return mp3, thumb, title, artist, int(info.get("duration") or 0)


def yt_search(query: str, n: int) -> list[dict]:
    o = {"quiet": True, "skip_download": True, "extract_flat": True, "no_warnings": True}
    with YoutubeDL(o) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    out = []
    for e in info.get("entries") or []:
        vid = (e or {}).get("id") or ""
        dur = e.get("duration") or 0
        if len(vid) == 11 and e.get("title") and (not dur or dur <= 20 * 60):
            out.append({"id": vid, "title": e["title"], "duration": dur, "uploader": e.get("uploader") or e.get("channel") or ""})
    return out


def jamendo_search(query: str) -> list[dict]:
    if not JAMENDO_ID:
        return []
    q = urlencode({"client_id": JAMENDO_ID, "format": "json", "search": query, "limit": 30, "audiodownload_allowed": "true"})
    with urlopen(Request(f"https://api.jamendo.com/v3.0/tracks/?{q}", headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
        res = json.load(r).get("results", [])
    return [{"id": "j" + t["id"], "title": f'{t["artist_name"]} - {t["name"]}', "duration": t.get("duration"), "uploader": ""} for t in res]


def dl_jamendo(track_id: str, folder: str):
    q = urlencode({"client_id": JAMENDO_ID, "format": "json", "id": track_id})
    with urlopen(Request(f"https://api.jamendo.com/v3.0/tracks/?{q}", headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
        t = json.load(r)["results"][0]
    path = _http_get(t["audio"], Path(folder) / f"{track_id}.mp3")
    return path, None, t["name"], t["artist_name"], int(t.get("duration") or 0)


def cut_for_recognition(src: str, dst: str, seconds: int = 25):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", str(seconds), "-vn", "-ac", "1", "-ar", "44100", dst],
                   capture_output=True, timeout=120, check=True)


# ====================================================================== kesh («bir marta yuklangan keyingi safar bepul»)
def cache_get(key):
    return DB.execute("SELECT kind, file_id, title, performer, duration FROM cache WHERE key=?", (key,)).fetchone()


def cache_put(key, kind, file_id, title="", performer="", duration=0):
    DB.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?)", (key, kind, file_id, title, performer, duration))
    DB.commit()


def _fid(msg: Message):
    if msg.video:
        return "video", msg.video.file_id
    if msg.audio:
        return "audio", msg.audio.file_id
    if msg.photo:
        return "photo", msg.photo[-1].file_id
    if msg.document:
        return "document", msg.document.file_id
    return None, None


def tt_kb(uid, url):
    lid = DB.execute("INSERT INTO links(url) VALUES(?)", (url,)).lastrowid
    DB.commit()
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(uid, "tt_music"), callback_data=f"tm:{lid}")]])


# ====================================================================== reklama
async def maybe_ad(bot: Bot, chat_id: int, uid: int):
    """Plus bo'lmaganlarga har N-yuklashdan so'ng reklama ko'rsatadi."""
    n_every = ad_every()
    if n_every <= 0 or is_plus(uid) or is_admin(uid):
        return
    DB.execute("INSERT INTO counters(uid, n) VALUES(?,1) ON CONFLICT(uid) DO UPDATE SET n=n+1", (uid,))
    n = DB.execute("SELECT n FROM counters WHERE uid=?", (uid,)).fetchone()[0]
    DB.commit()
    if n % n_every:
        return
    ads = DB.execute("SELECT id, src_chat, src_msg, btn_text, btn_url FROM ads WHERE active=1").fetchall()
    if not ads:
        return
    aid, sc, sm, bt, bu = random.choice(ads)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=bt, url=bu)]]) if bt and bu else None
    try:
        await bot.copy_message(chat_id, sc, sm, reply_markup=kb)
        DB.execute("UPDATE ads SET views=views+1 WHERE id=?", (aid,))
        DB.commit()
    except Exception:
        log.exception("ad")


# ====================================================================== yuborish yordamchilari
async def send_files(bot: Bot, chat_id: int, files: list[Path], reply_to: int | None, caption, kb=None):
    """Bitta fayl — o'z turida, ko'p fayl — albom. -> (tur, file_id) yoki (None, None)."""
    if len(files) == 1:
        f = files[0]
        if f.suffix.lower() in core.IMG_EXT:
            m = await bot.send_photo(chat_id, FSInputFile(f), caption=caption, parse_mode="HTML", reply_to_message_id=reply_to, reply_markup=kb)
        else:
            m = await bot.send_video(chat_id, FSInputFile(f), caption=caption, parse_mode="HTML", supports_streaming=True,
                                     reply_to_message_id=reply_to, reply_markup=kb)
        return _fid(m)
    first = None
    for part in core.chunk(files, 10):
        media = []
        for i, f in enumerate(part):
            cls = InputMediaPhoto if f.suffix.lower() in core.IMG_EXT else InputMediaVideo
            media.append(cls(media=FSInputFile(f), caption=caption if (i == 0 and first is None) else None, parse_mode="HTML"))
        await bot.send_media_group(chat_id, media, reply_to_message_id=reply_to)
        first = first or True
    return None, None            # albomlar keshlanmaydi


async def send_cached(bot: Bot, chat_id: int, row, reply_to, caption, kb=None):
    kind, fid, title, perf, dur = row
    if kind == "video":
        await bot.send_video(chat_id, fid, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to, reply_markup=kb)
    elif kind == "audio":
        await bot.send_audio(chat_id, fid, caption=caption, parse_mode="HTML", title=title or None, performer=perf or None,
                             reply_to_message_id=reply_to, reply_markup=kb)
    elif kind == "photo":
        await bot.send_photo(chat_id, fid, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to, reply_markup=kb)
    else:
        await bot.send_document(chat_id, fid, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to, reply_markup=kb)


async def send_mp3(bot: Bot, chat_id: int, uid: int, target: str, cache_key: str, reply_to=None, title="", artist=""):
    """MP3 ni keshdan yoki yangidan yuboradi. True — muvaffaqiyatli. Yangi yuklash limitga hisoblanadi."""
    row = cache_get(cache_key)
    if row:
        await send_cached(bot, chat_id, row, reply_to, caption_or_none())
        return True
    if not can_download(uid):
        await bot.send_message(chat_id, T(uid, "limit", n=free_daily()))
        return False
    async with DL_SLOTS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                if target.startswith("jamendo:"):
                    res = await asyncio.to_thread(dl_jamendo, target[8:], tmp)
                else:
                    res = await asyncio.to_thread(dl_mp3, target, tmp, title, artist)
                path, thumb, ttl, art, dur = res
                if path.stat().st_size > MAX_BYTES:
                    await bot.send_message(chat_id, T(uid, "too_big", mb=MAX_MB))
                    return False
                m = await bot.send_audio(chat_id, FSInputFile(path), title=ttl, performer=art, duration=dur or None,
                                         thumbnail=FSInputFile(thumb) if thumb else None, caption=caption_or_none(),
                                         parse_mode="HTML", reply_to_message_id=reply_to)
                cache_put(cache_key, "audio", m.audio.file_id, ttl, art, dur)
                consume(uid)
                await maybe_ad(bot, chat_id, uid)
                return True
            except Exception:
                log.exception("mp3 error")
                await bot.send_message(chat_id, T(uid, "audio_fail"))
                return False


# ====================================================================== bot handlerlari
@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(welcome_text(m.from_user.id), parse_mode="HTML")


@dp.message(Command("plus"))
async def plus_cmd(m: Message):
    uid = m.from_user.id
    kb = [[InlineKeyboardButton(text=T(uid, "plus_btn", days=d, stars=s), callback_data=f"plus:{d}")] for d, s in plus_prices().items()]
    text = T(uid, "plus_info")
    if is_plus(uid):
        text = T(uid, "plus_active", date=date_str(plus_until(uid))) + "\n\n" + text
    await m.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("plus:"))
async def plus_buy(c: CallbackQuery):
    uid, days = c.from_user.id, int(c.data[5:])
    stars = plus_prices().get(days)
    if not stars:
        return await c.answer()
    await c.bot.send_invoice(uid, title=f"⭐ Plus — {days}", description=T(uid, "plus_info").replace("<b>", "").replace("</b>", "")[:200],
                             payload=f"plus:{days}", currency="XTR", prices=[LabeledPrice(label=f"Plus {days}", amount=stars)],
                             provider_token="")
    await c.answer()


@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment
    days = int(sp.invoice_payload.split(":")[1])
    until = grant_plus(m.from_user.id, days)
    DB.execute("INSERT INTO payments(uid, stars, days, at) VALUES(?,?,?,?)", (m.from_user.id, sp.total_amount, days, int(time.time())))
    DB.commit()
    await m.answer(T(m.from_user.id, "plus_ok", date=date_str(until)))


# ------------------------------------------------------------------ YouTube: sifat tanlash
@dp.callback_query(F.data.startswith("yt:"))
async def yt_pick(c: CallbackQuery):
    uid = c.from_user.id
    _, vid, what = c.data.split(":")
    await c.answer(T(uid, "downloading"))
    if what == "mp3":
        ok = await send_mp3(c.bot, c.message.chat.id, uid, f"https://www.youtube.com/watch?v={vid}", f"mp3:{vid}")
    else:
        ok = await do_video(c.bot, c.message.chat.id, uid, f"https://www.youtube.com/watch?v={vid}", None, what)
    if ok:
        try:
            await c.message.delete()
        except Exception:
            pass


async def do_video(bot: Bot, chat_id: int, uid: int, url: str, reply_to, height=None, notify=True) -> bool:
    """Havola bo'yicha video/rasm yuklab yuboradi (kesh, limit, reklama bilan)."""
    pf = core.platform(url)
    key = (f"{core.norm_url(url)}:{height}" if pf == "youtube" else core.norm_url(url))
    kb = tt_kb(uid, url) if pf == "tiktok" else None
    row = cache_get(key)
    if row:
        await send_cached(bot, chat_id, row, reply_to, caption_or_none(), kb)
        return True
    if not can_download(uid):
        await bot.send_message(chat_id, T(uid, "limit", n=free_daily()))
        return False
    async with DL_SLOTS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                files, info = await asyncio.to_thread(dl_media, url, tmp, height)
                if not files:
                    raise RuntimeError("fayl topilmadi")
                if any(f.stat().st_size > MAX_BYTES for f in files):
                    if notify:
                        await bot.send_message(chat_id, T(uid, "too_big", mb=MAX_MB))
                    return False
                kind, fid = await send_files(bot, chat_id, files, reply_to, caption_or_none(), kb)
                if fid:
                    cache_put(key, kind, fid)
                consume(uid)
                await maybe_ad(bot, chat_id, uid)
                return True
            except Exception as e:
                log.exception("video error")
                if notify:
                    txt = T(uid, "fail_story") if core.is_story(url) else T(uid, "fail")
                    if is_admin(uid):
                        txt += f"\n\n🛠 {str(e)[:300]}"
                    await bot.send_message(chat_id, txt)
                return False


# ------------------------------------------------------------------ TikTok musiqasi (bepul)
@dp.callback_query(F.data.startswith("tm:"))
async def tiktok_music(c: CallbackQuery):
    uid = c.from_user.id
    r = DB.execute("SELECT url FROM links WHERE id=?", (int(c.data[3:]),)).fetchone()
    if not r:
        return await c.answer(T(uid, "expired"), show_alert=True)
    await c.answer(T(uid, "downloading"))
    key = "tm:" + core.norm_url(r[0])
    row = cache_get(key)
    if row:
        return await send_cached(c.bot, c.message.chat.id, row, c.message.message_id, caption_or_none())
    async with DL_SLOTS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                path, thumb, ttl, art, dur = await asyncio.to_thread(dl_mp3, r[0], tmp)
                m = await c.message.answer_audio(FSInputFile(path), title=ttl, performer=art, duration=dur or None,
                                                 thumbnail=FSInputFile(thumb) if thumb else None, caption=caption_or_none(), parse_mode="HTML")
                cache_put(key, "audio", m.audio.file_id, ttl, art, dur)           # bepul: limitga hisoblanmaydi
            except Exception:
                log.exception("tiktok music")
                await c.message.answer(T(uid, "audio_fail"))


# ------------------------------------------------------------------ qo'shiq tanish (ovoz, video, audio, dumaloq video)
async def shazam_recognize(path: str):
    from shazamio import Shazam
    sh = Shazam()
    fn = getattr(sh, "recognize", None) or getattr(sh, "recognize_song")
    return await fn(path)


def _lyrics(track: dict) -> str:
    for s in track.get("sections") or []:
        if s.get("type") == "LYRICS" and s.get("text"):
            return "\n".join(s["text"])
    return ""


@dp.message(F.voice | F.audio | F.video | F.video_note, F.chat.type == "private", StateFilter(default_state))
async def recognize(m: Message):
    uid = m.from_user.id
    media = m.voice or m.audio or m.video or m.video_note
    if media.file_size and media.file_size > MAX_BYTES:
        return await m.answer(T(uid, "rec_big"))
    wait = await m.answer(T(uid, "rec_wait"))
    async with DL_SLOTS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                src, dst = f"{tmp}/src", f"{tmp}/clip.wav"
                await m.bot.download(media, destination=src)
                await asyncio.to_thread(cut_for_recognition, src, dst)
                out = await shazam_recognize(dst)
            except ImportError:
                return await wait.edit_text(T(uid, "rec_off"))
            except Exception:
                log.exception("recognize")
                return await wait.edit_text(T(uid, "rec_none"))
    track = (out or {}).get("track")
    if not track:
        return await wait.edit_text(T(uid, "rec_none"))
    title, artist = track.get("title", ""), track.get("subtitle", "")
    lyr = _lyrics(track)
    sid = DB.execute("INSERT INTO songs(q, title, artist, lyrics) VALUES(?,?,?,?)", (f"{artist} {title}", title, artist, lyr)).lastrowid
    DB.commit()
    rows = [[InlineKeyboardButton(text=T(uid, "mp3"), callback_data=f"sm:{sid}")]]
    if lyr:
        rows.append([InlineKeyboardButton(text=T(uid, "lyrics"), callback_data=f"sl:{sid}")])
    cover = (track.get("images") or {}).get("coverarthq") or (track.get("images") or {}).get("coverart")
    cap = f"🎵 <b>{_e(title)}</b>\n👤 {_e(artist)}"
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await wait.delete()
    if cover:
        try:
            return await m.answer_photo(cover, caption=cap, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
    await m.answer(cap, parse_mode="HTML", reply_markup=kb)


def _e(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dp.callback_query(F.data.startswith("sm:"))
async def song_mp3(c: CallbackQuery):
    uid = c.from_user.id
    r = DB.execute("SELECT q, title, artist FROM songs WHERE id=?", (int(c.data[3:]),)).fetchone()
    if not r:
        return await c.answer(T(uid, "expired"), show_alert=True)
    await c.answer(T(uid, "downloading"))
    q, title, artist = r
    await send_mp3(c.bot, c.message.chat.id, uid, f"ytsearch1:{q}", "sm:" + re.sub(r"\W+", "", q.lower())[:60], title=title, artist=artist)


@dp.callback_query(F.data.startswith("sl:"))
async def song_lyrics(c: CallbackQuery):
    uid = c.from_user.id
    r = DB.execute("SELECT lyrics FROM songs WHERE id=?", (int(c.data[3:]),)).fetchone()
    if not r or not r[0]:
        return await c.answer(T(uid, "no_lyrics"), show_alert=True)
    await c.answer()
    for part in core.chunk(r[0], 3900):
        await c.message.answer(_e(part))


# ------------------------------------------------------------------ /top
@dp.message(Command("top"))
async def top(m: Message):
    uid = m.from_user.id
    wait = await m.answer(T(uid, "searching"))
    tracks, region = [], "top_uz"
    try:
        from shazamio import Shazam
        sh = Shazam()
        for region, fn in (("top_uz", lambda: sh.top_country_tracks("UZ", 10)), ("top_world", lambda: sh.top_world_tracks(10))):
            data = await fn()
            tracks = (data or {}).get("tracks") or []
            if tracks:
                break
    except Exception:
        log.exception("top")
    if not tracks:
        return await wait.edit_text(T(uid, "top_off"))
    lines, rows = [T(uid, "top_title", region=T(uid, region)), ""], []
    for i, t in enumerate(tracks[:10], 1):
        title, artist = t.get("title", ""), t.get("subtitle", "")
        lines.append(f"{i}. {artist} — {title}")
        sid = DB.execute("INSERT INTO songs(q, title, artist, lyrics) VALUES(?,?,?,?)", (f"{artist} {title}", title, artist, "")).lastrowid
        rows.append(InlineKeyboardButton(text=str(i), callback_data=f"sm:{sid}"))
    DB.commit()
    await wait.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=core.chunk(rows, 5)))


# ------------------------------------------------------------------ qo'shiq nomi bo'yicha qidiruv («Yana» tugmasi bilan)
PER_PAGE = 10
SEARCHES: dict[tuple, dict] = {}


def _render(uid, st: dict):
    tracks, page = st["tracks"], st["page"]
    chunk_ = tracks[page * PER_PAGE:(page + 1) * PER_PAGE]
    lines = [f"🔍 {st['query']}", ""] + [f"{i}. {t['title']}{core.fmt_dur(t.get('duration'))}" for i, t in enumerate(chunk_, 1)]
    nums = [InlineKeyboardButton(text=str(i), callback_data=f"n:{page * PER_PAGE + i - 1}") for i in range(1, len(chunk_) + 1)]
    rows = core.chunk(nums, 5)
    nav = [InlineKeyboardButton(text="⬅️", callback_data="p:-1"), InlineKeyboardButton(text=T(uid, "close"), callback_data="x"),
           InlineKeyboardButton(text="➡️", callback_data="p:1")]
    rows.append(nav)
    if (page + 1) * PER_PAGE >= len(tracks) and len(tracks) < 50 and not st.get("end"):
        rows.append([InlineKeyboardButton(text=T(uid, "more"), callback_data="mr")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def do_search(m: Message, query: str):
    uid = m.from_user.id
    wait = await m.answer(T(uid, "searching"))
    try:
        tracks = await asyncio.to_thread(yt_search, query, PER_PAGE)
    except Exception:
        log.exception("search")
        tracks = []
    if not tracks:
        try:
            tracks = await asyncio.to_thread(jamendo_search, query)
        except Exception:
            tracks = []
    if not tracks:
        return await wait.edit_text(T(uid, "no_results"))
    st = {"query": query, "tracks": tracks, "page": 0}
    if len(SEARCHES) > 500:
        SEARCHES.pop(next(iter(SEARCHES)))
    SEARCHES[(wait.chat.id, wait.message_id)] = st
    text, kb = _render(uid, st)
    await wait.edit_text(text, reply_markup=kb)


@dp.message(Command("music"))
async def music(m: Message, command: CommandObject):
    q = (command.args or "").strip()
    if not q:
        return await m.answer(T(m.from_user.id, "no_results"))
    await do_search(m, q)


@dp.callback_query(F.data.startswith("p:"))
async def paginate(c: CallbackQuery):
    uid = c.from_user.id
    st = SEARCHES.get((c.message.chat.id, c.message.message_id))
    if not st:
        return await c.answer(T(uid, "expired"), show_alert=True)
    pages = (len(st["tracks"]) - 1) // PER_PAGE + 1
    st["page"] = (st["page"] + int(c.data[2:])) % pages
    text, kb = _render(uid, st)
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data == "mr")
async def more(c: CallbackQuery):
    uid = c.from_user.id
    st = SEARCHES.get((c.message.chat.id, c.message.message_id))
    if not st:
        return await c.answer(T(uid, "expired"), show_alert=True)
    await c.answer(T(uid, "searching"))
    try:
        found = await asyncio.to_thread(yt_search, st["query"], min(50, len(st["tracks"]) + PER_PAGE))
    except Exception:
        found = []
    have = {t["id"] for t in st["tracks"]}
    new = [t for t in found if t["id"] not in have]
    if not new:
        st["end"] = True
    else:
        st["tracks"] += new
        st["page"] = (len(st["tracks"]) - len(new)) // PER_PAGE
    text, kb = _render(uid, st)
    await c.message.edit_text(text, reply_markup=kb)


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
    uid = c.from_user.id
    st = SEARCHES.get((c.message.chat.id, c.message.message_id))
    if not st:
        return await c.answer(T(uid, "expired"), show_alert=True)
    t = st["tracks"][int(c.data[2:])]
    await c.answer(T(uid, "downloading"))
    if t["id"].startswith("j"):
        await send_mp3(c.bot, c.message.chat.id, uid, "jamendo:" + t["id"][1:], "mp3:" + t["id"])
    else:
        await send_mp3(c.bot, c.message.chat.id, uid, f"https://www.youtube.com/watch?v={t['id']}", f"mp3:{t['id']}")


# ------------------------------------------------------------------ inline qidiruv: istalgan chatda @bot qo'shiq nomi
@dp.inline_query()
async def inline_search(q: InlineQuery):
    uid, text = q.from_user.id, (q.query or "").strip()
    if len(text) < 2:
        return await q.answer([], cache_time=5, is_personal=True)
    try:
        tracks = await asyncio.to_thread(yt_search, text, 15)
    except Exception:
        tracks = []
    results = []
    for t in tracks[:20]:
        row = cache_get(f"mp3:{t['id']}")
        if row:                                                     # avval yuklangan — darrov audio sifatida
            results.append(InlineQueryResultCachedAudio(id=f"c{t['id']}", audio_file_id=row[1]))
            continue
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(uid, "inline_wait"), callback_data="noop")]])
        results.append(InlineQueryResultArticle(
            id=t["id"], title=t["title"][:100], description=(t.get("uploader") or "") + core.fmt_dur(t.get("duration")),
            thumbnail_url=f"https://i.ytimg.com/vi/{t['id']}/mqdefault.jpg",
            input_message_content=InputTextMessageContent(message_text=f"🎵 {t['title']}"), reply_markup=kb))
    await q.answer(results, cache_time=30, is_personal=True)


@dp.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


@dp.chosen_inline_result()
async def inline_chosen(r: ChosenInlineResult, bot: Bot):
    """Tanlangan natijani MP3 qilib, inline xabarni audio bilan almashtiradi."""
    uid, vid = r.from_user.id, r.result_id
    if vid.startswith("c") or not r.inline_message_id:
        return
    row = cache_get(f"mp3:{vid}")
    if not row:
        if not await send_mp3(bot, uid, uid, f"https://www.youtube.com/watch?v={vid}", f"mp3:{vid}"):
            return
        row = cache_get(f"mp3:{vid}")
    try:
        await bot.edit_message_media(inline_message_id=r.inline_message_id, media=InputMediaAudio(media=row[1], title=row[2], performer=row[3]))
    except Exception:
        log.exception("inline edit")
        try:
            await bot.edit_message_text(T(uid, "inline_fail"), inline_message_id=r.inline_message_id)
        except Exception:
            pass


# ------------------------------------------------------------------ havola yuborilganda: shaxsiy chat
@dp.message(F.text.regexp(core.URL_RE), F.chat.type == "private", StateFilter(default_state))
async def link_private(m: Message):
    uid = m.from_user.id
    url = core.first_url(m.text)
    if not url or not core.platform(url):
        return await m.answer(T(uid, "fail"))
    if core.platform(url) == "youtube":
        if not core.yt_id(url):
            return await m.answer(T(uid, "fail"))
        wait = await m.answer(T(uid, "wait"))
        try:
            info = await asyncio.to_thread(yt_info, url)
            opts = core.build_options(info, MAX_BYTES)
        except Exception as e:
            log.exception("yt info")
            txt = T(uid, "fail") + (f"\n\n🛠 {str(e)[:300]}" if is_admin(uid) else "")
            return await wait.edit_text(txt)
        if not opts:
            return await wait.edit_text(T(uid, "no_options"))
        vid = core.yt_id(url)
        btns = [InlineKeyboardButton(text=o["label"], callback_data=f"yt:{vid}:{o['data']}") for o in opts]
        rows = core.chunk(btns, 2)
        rows.append([InlineKeyboardButton(text=T(uid, "close"), callback_data="x")])
        return await wait.edit_text(T(uid, "pick_quality", title=info.get("title", "")),
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    wait = await m.answer(T(uid, "wait"))
    await do_video(m.bot, m.chat.id, uid, url, m.message_id)
    try:
        await wait.delete()
    except Exception:
        pass


# ------------------------------------------------------------------ guruhlarda: havola tashlansa 👀 qo'yib, videoni yuboradi
@dp.message(F.text.regexp(core.URL_RE), F.chat.type.in_({"group", "supergroup"}))
async def link_group(m: Message):
    url = core.first_url(m.text)
    if not url or not core.platform(url) or not m.from_user:
        return
    uid = m.from_user.id
    DB.execute("INSERT OR IGNORE INTO users(id, joined) VALUES(?,?)", (uid, int(time.time())))
    DB.commit()
    try:
        await m.bot.set_message_reaction(m.chat.id, m.message_id, reaction=[ReactionTypeEmoji(emoji="👀")])
    except Exception:
        pass
    height = None
    if core.platform(url) == "youtube":
        if not core.yt_id(url):
            return
        try:
            info = await asyncio.to_thread(yt_info, url)
            height = core.pick_group_height(core.build_options(info, MAX_BYTES))
        except Exception:
            height = "360"
        if not height:
            return
    if not can_download(uid) and not cache_get(f"{core.norm_url(url)}:{height}" if height else core.norm_url(url)):
        await m.reply(T(uid, "group_limit"))
    else:
        await do_video(m.bot, m.chat.id, uid, url, m.message_id, height, notify=False)
    try:
        await m.bot.set_message_reaction(m.chat.id, m.message_id, reaction=[])
    except Exception:
        pass


# ------------------------------------------------------------------ oddiy matn — qo'shiq qidirish
@dp.message(F.text & ~F.text.startswith("/"), F.chat.type == "private", StateFilter(default_state))
async def text_search(m: Message):
    await do_search(m, m.text.strip())


# ====================================================================== ADMIN PANEL (faqat adminlarga)
ADMIN = F.from_user.id.func(is_admin)
BG_TASKS: set = set()


class AdminSt(StatesGroup):
    broadcast = State()
    confirm = State()
    new_admin = State()
    welcome = State()
    channel = State()
    caption = State()
    ad_msg = State()
    ad_btn = State()
    free = State()
    adfreq = State()
    prices = State()
    grant = State()


def panel_kb() -> InlineKeyboardMarkup:
    b = lambda t, d: [InlineKeyboardButton(text=t, callback_data=d)]
    return InlineKeyboardMarkup(inline_keyboard=[
        b("📢 Xabar yuborish", "ad:bc"), b("👋 Kirish matni", "ad:welcome"), b("✍️ Video osti matni", "ad:cap"),
        b("📣 Majburiy obuna", "ad:ch"), b("📺 Reklamalar", "ad:ads"), b("⭐ Plus va limitlar", "ad:plus"),
        b("➕ Admin qo'shish", "ad:add"), b("📊 Statistika", "ad:stats")])


@dp.message(Command("admin"), ADMIN)
async def admin_panel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 Admin panel", reply_markup=panel_kb())


@dp.message(Command("cancel"), ADMIN)
async def admin_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.", reply_markup=panel_kb())


@dp.message(Command("grant"), ADMIN)
async def grant_cmd(m: Message, command: CommandObject):
    a = (command.args or "").split()
    if len(a) != 2 or not all(x.isdigit() for x in a):
        return await m.answer("Format: /grant FOYDALANUVCHI_ID KUN")
    until = grant_plus(int(a[0]), int(a[1]))
    await m.answer(f"✅ {a[0]} ga Plus berildi: {date_str(until)} gacha.")


@dp.message(Command("web"), ADMIN)
async def web_cmd(m: Message):
    port, key, pub = os.getenv("WEB_PORT"), os.getenv("WEB_KEY"), os.getenv("WEB_PUBLIC_URL")
    if not (port and key):
        return await m.answer("Web panel bu serverda yoqilmagan (WEB_PORT/WEB_KEY).")
    base = (pub or "http://SERVER:{port}").replace("{port}", port)
    await m.answer(f"📱 Web admin panel:\n{base}/?key={key}")


@dp.callback_query(F.data == "ad:stats", ADMIN)
async def ad_stats(c: CallbackQuery):
    q1 = lambda s, *a: DB.execute(s, a).fetchone()[0]
    day = time.strftime("%Y-%m-%d")
    await c.message.answer(
        f"👥 Foydalanuvchilar: {q1('SELECT COUNT(*) FROM users')}\n🛠 Adminlar: {q1('SELECT COUNT(*) FROM admins')}\n"
        f"⭐ Plus: {q1('SELECT COUNT(*) FROM users WHERE plus_until>?', int(time.time()))}\n"
        f"📥 Bugun yuklaganlar: {q1('SELECT COUNT(*) FROM usage WHERE day=?', day)}\n"
        f"📥 Bugungi yuklashlar: {q1('SELECT COALESCE(SUM(n),0) FROM usage WHERE day=?', day)}\n"
        f"🔵 Keshdagi fayllar: {q1('SELECT COUNT(*) FROM cache')}\n"
        f"💳 Stars tushumi: {q1('SELECT COALESCE(SUM(stars),0) FROM payments')} ⭐")
    await c.answer()


# --- xabar yuborish
@dp.callback_query(F.data == "ad:bc", ADMIN)
async def ad_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.broadcast)
    await c.message.answer("Barcha foydalanuvchilarga yuboriladigan xabarni yuboring (matn, rasm, video, audio, fayl, ovozli xabar va h.k.).\nBekor qilish: /cancel")
    await c.answer()


@dp.message(AdminSt.broadcast, ADMIN)
async def ad_bc_preview(m: Message, state: FSMContext):
    await state.update_data(chat_id=m.chat.id, msg_id=m.message_id)
    await state.set_state(AdminSt.confirm)
    await m.answer("👆 Shu xabar hammaga yuboriladi. Tasdiqlaysizmi?")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yuborish", callback_data="ad:go"),
                                                InlineKeyboardButton(text="❌ Bekor", callback_data="ad:no")]])
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
            fail += 1
        await asyncio.sleep(0.05)
    await bot.send_message(admin_id, f"✅ Yuborildi: {ok}\n❌ Yetib bormadi: {fail}")


@dp.callback_query(F.data == "ad:go", AdminSt.confirm, ADMIN)
async def ad_go(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await c.message.edit_text("📤 Yuborish boshlandi, tugagach xabar beraman.")
    task = asyncio.create_task(_broadcast(c.bot, c.from_user.id, data["chat_id"], data["msg_id"]))
    BG_TASKS.add(task)
    task.add_done_callback(BG_TASKS.discard)
    await c.answer()


@dp.callback_query(F.data == "ad:no", ADMIN)
async def ad_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Bekor qilindi.")
    await c.answer()


# --- admin qo'shish
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


# --- kirish matni
@dp.callback_query(F.data == "ad:welcome", ADMIN)
async def ad_welcome(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.welcome)
    await c.message.answer("Yangi kirish (/start) matnini yuboring (barcha tillar uchun bir xil chiqadi).\n"
                           "Qalin, qiya kabi formatlashdan foydalansangiz bo'ladi.\nStandart matnga qaytish uchun «-» yuboring. Bekor qilish: /cancel")
    await c.answer()


@dp.message(AdminSt.welcome, ADMIN, F.text)
async def ad_welcome_save(m: Message, state: FSMContext):
    set_setting("welcome", "" if m.text.strip() == "-" else m.html_text)
    await state.clear()
    await m.answer("✅ Kirish matni saqlandi.", reply_markup=panel_kb())


# --- video/audio osti matni
@dp.callback_query(F.data == "ad:cap", ADMIN)
async def ad_caption(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.caption)
    await c.message.answer(f"Hozirgi matn: {get_setting('caption') or 'yoq'}\n\n"
                           "Yuklangan video va audio ostida chiqadigan yangi matnni yuboring (masalan: «📥 @botingiz orqali yuklandi»). "
                           "Formatlash ishlaydi, maksimum 1000 belgi.\nO'chirish uchun «-» yuboring. Bekor qilish: /cancel")
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


# --- majburiy obuna kanallari
def _channels_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"🗑 {t}", callback_data=f"ad:chdel:{chat}")] for chat, t, _ in channels()]
    rows.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="ad:chadd")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "ad:ch", ADMIN)
async def ad_channels(c: CallbackQuery):
    await c.message.answer(f"📣 Majburiy obuna kanallari: {len(channels())}\nKanal o'chirish uchun 🗑 tugmasini bosing.", reply_markup=_channels_kb())
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
    await c.message.answer("Avval botni kanalga ADMIN qilib qo'shing, so'ng yuboring:\n\n• Ochiq kanal: @kanal_username\n"
                           "• Yopiq kanal: -1001234567890 https://t.me/+taklif_havola\n\nBekor qilish: /cancel")
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
    DB.execute("INSERT OR REPLACE INTO channels VALUES(?,?,?)", (str(chat.id), chat.title or ref, link))
    DB.commit()
    await state.clear()
    await m.answer(f"✅ «{chat.title}» qo'shildi.", reply_markup=panel_kb())


# --- reklamalar
def _ads_kb():
    rows = []
    for aid, act, views, bt in DB.execute("SELECT id, active, views, btn_text FROM ads ORDER BY id").fetchall():
        rows.append([InlineKeyboardButton(text=f"{'🟢' if act else '⚪️'} #{aid} · 👁 {views} · {bt or 'tugmasiz'}", callback_data=f"ad:at:{aid}"),
                     InlineKeyboardButton(text="🗑", callback_data=f"ad:ad:{aid}")])
    rows.append([InlineKeyboardButton(text="➕ Reklama qo'shish", callback_data="ad:aadd")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "ad:ads", ADMIN)
async def ad_ads(c: CallbackQuery):
    await c.message.answer(f"📺 Reklamalar (har {ad_every()}-yuklashdan so'ng, Plus bo'lmaganlarga ko'rsatiladi).\n🟢 — yoqilgan, bosib o'chirish/yoqish mumkin.", reply_markup=_ads_kb())
    await c.answer()


@dp.callback_query(F.data.startswith("ad:at:"), ADMIN)
async def ad_toggle(c: CallbackQuery):
    DB.execute("UPDATE ads SET active=1-active WHERE id=?", (int(c.data.split(":")[2]),))
    DB.commit()
    await c.message.edit_reply_markup(reply_markup=_ads_kb())
    await c.answer()


@dp.callback_query(F.data.startswith("ad:ad:"), ADMIN)
async def ad_delete(c: CallbackQuery):
    DB.execute("DELETE FROM ads WHERE id=?", (int(c.data.split(":")[2]),))
    DB.commit()
    await c.message.edit_reply_markup(reply_markup=_ads_kb())
    await c.answer("O'chirildi")


@dp.callback_query(F.data == "ad:aadd", ADMIN)
async def ad_ad_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.ad_msg)
    await c.message.answer("Reklama xabarini yuboring (matn, rasm yoki video — izoh bilan).\nBekor qilish: /cancel")
    await c.answer()


@dp.message(AdminSt.ad_msg, ADMIN)
async def ad_msg_got(m: Message, state: FSMContext):
    await state.update_data(src_chat=m.chat.id, src_msg=m.message_id)
    await state.set_state(AdminSt.ad_btn)
    await m.answer("Tugma kerakmi? «Matn | https://havola» ko'rinishida yuboring, yoki tugmasiz uchun «-».")


@dp.message(AdminSt.ad_btn, ADMIN, F.text)
async def ad_btn_got(m: Message, state: FSMContext):
    d = await state.get_data()
    bt = bu = None
    if m.text.strip() != "-":
        if "|" not in m.text or not m.text.split("|", 1)[1].strip().startswith("http"):
            return await m.answer("Format: Matn | https://havola   (yoki «-»)")
        bt, bu = (x.strip() for x in m.text.split("|", 1))
    DB.execute("INSERT INTO ads(src_chat, src_msg, btn_text, btn_url) VALUES(?,?,?,?)", (d["src_chat"], d["src_msg"], bt, bu))
    DB.commit()
    await state.clear()
    await m.answer("✅ Reklama qo'shildi.", reply_markup=panel_kb())


# --- Plus va limitlar
@dp.callback_query(F.data == "ad:plus", ADMIN)
async def ad_plus(c: CallbackQuery):
    rows = [[InlineKeyboardButton(text=f"📥 Bepul kunlik limit: {free_daily() or 'cheksiz'}", callback_data="ad:free")],
            [InlineKeyboardButton(text=f"📺 Reklama chastotasi: har {ad_every()}-yuklash", callback_data="ad:adfreq")],
            [InlineKeyboardButton(text="💰 Plus narxlari (Stars): " + ", ".join(f"{d}k={s}" for d, s in plus_prices().items()), callback_data="ad:prices")],
            [InlineKeyboardButton(text="🎁 Foydalanuvchiga Plus berish", callback_data="ad:grant")]]
    await c.message.answer("⭐ Plus va limitlar", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


def _ask(name, state_, text):
    @dp.callback_query(F.data == f"ad:{name}", ADMIN)
    async def _h(c: CallbackQuery, state: FSMContext):
        await state.set_state(state_)
        await c.message.answer(text + "\nBekor qilish: /cancel")
        await c.answer()


_ask("free", AdminSt.free, "Bepul foydalanuvchi uchun kuniga nechta yangi yuklash? (0 = cheksiz). Keshdagi fayllar hisobga olinmaydi.")
_ask("adfreq", AdminSt.adfreq, "Reklama har nechanchi yuklashdan so'ng chiqsin? (0 = reklamasiz)")
_ask("prices", AdminSt.prices, "Plus narxlari «kun:stars» ko'rinishida, vergul bilan. Masalan: 30:150,90:400,365:1300")
_ask("grant", AdminSt.grant, "«ID KUN» yuboring. Masalan: 123456789 30")


@dp.message(AdminSt.free, ADMIN, F.text)
async def s_free(m: Message, state: FSMContext):
    if not m.text.strip().isdigit():
        return await m.answer("Raqam yuboring.")
    set_setting("free_daily", m.text.strip())
    await state.clear()
    await m.answer("✅ Saqlandi.", reply_markup=panel_kb())


@dp.message(AdminSt.adfreq, ADMIN, F.text)
async def s_adfreq(m: Message, state: FSMContext):
    if not m.text.strip().isdigit():
        return await m.answer("Raqam yuboring.")
    set_setting("ad_every", m.text.strip())
    await state.clear()
    await m.answer("✅ Saqlandi.", reply_markup=panel_kb())


@dp.message(AdminSt.prices, ADMIN, F.text)
async def s_prices(m: Message, state: FSMContext):
    if not re.fullmatch(r"\d+:\d+(,\d+:\d+)*", m.text.replace(" ", "")):
        return await m.answer("Format: 30:150,90:400")
    set_setting("plus_prices", m.text.replace(" ", ""))
    await state.clear()
    await m.answer("✅ Saqlandi.", reply_markup=panel_kb())


@dp.message(AdminSt.grant, ADMIN, F.text)
async def s_grant(m: Message, state: FSMContext):
    a = m.text.split()
    if len(a) != 2 or not all(x.isdigit() for x in a):
        return await m.answer("Format: ID KUN")
    until = grant_plus(int(a[0]), int(a[1]))
    await state.clear()
    await m.answer(f"✅ {a[0]} ga Plus: {date_str(until)} gacha.", reply_markup=panel_kb())


# ====================================================================== ishga tushirish
async def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")
    session = None
    if LOCAL_API:
        session = AiohttpSession(api=TelegramAPIServer.from_base(LOCAL_API, is_local=True), timeout=3600)
    bot = Bot(TOKEN, session=session)
    if os.getenv("WEB_PORT") and os.getenv("WEB_KEY"):
        try:
            import yuklovchi_web
            await yuklovchi_web.start(DB, int(os.getenv("WEB_PORT")), os.getenv("WEB_KEY"), grant_plus, get_setting, set_setting)
        except Exception:
            log.exception("web panel")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result", "pre_checkout_query"])


if __name__ == "__main__":
    asyncio.run(main())
