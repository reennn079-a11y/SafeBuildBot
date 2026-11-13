"""
Telegram bot: rasm fonini olib tashlash (+ admin panel, majburiy obuna, xabar yuborish).

O'rnatish:
    pip install aiogram

Fonni olib tashlash uchun remove.bg API kaliti kerak. Tayyor kalit O'RNATILMAGAN:
bot egasi (admin) kalitni remove.bg saytidan o'zi olib, botga yuboradi
(/admin -> «🔑 remove.bg kaliti» yoki /key). Kalit shu botning bazasida saqlanadi.
(Ixtiyoriy: REMOVEBG_KEY muhit o'zgaruvchisi yoki mahalliy rembg zaxira sifatida ishlaydi.)

Ishga tushirish:
    export BOT_TOKEN="..."
    python removebg_bot.py
"""
import asyncio
import logging
import os
import sqlite3
from io import BytesIO

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
REMOVEBG_KEY = os.getenv("REMOVEBG_KEY")
MAX_IMAGE_MB = 20

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari


# ---------- Baza (SQLite), adminlar, sozlamalar ----------

DB = sqlite3.connect("removebg_bot.db", check_same_thread=False)
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
    "🖼 Menga rasm yuboring, men uning fonini olib tashlayman.\n"
    "💡 Eng yaxshi sifat uchun rasmni «Fayl» sifatida yuboring."
)


def is_admin(uid: int) -> bool:
    return DB.execute("SELECT 1 FROM admins WHERE id=?", (uid,)).fetchone() is not None


def get_setting(key: str, default: str = "") -> str:
    row = DB.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str):
    DB.execute("INSERT OR REPLACE INTO settings VALUES(?, ?)", (key, value))
    DB.commit()


def api_key() -> str:
    """Avval admin botga yuborgan kalit, bo'lmasa muhitdagi REMOVEBG_KEY."""
    return get_setting("removebg_key") or REMOVEBG_KEY or ""


KEY_HELP = (
    "🔑 <b>remove.bg API kalitini ulash</b>\n\n"
    "1️⃣ <a href=\"https://www.remove.bg/users/sign_up\">remove.bg</a> saytida ro'yxatdan o'ting (bepul).\n"
    "2️⃣ <a href=\"https://www.remove.bg/api#remove-background\">remove.bg/api</a> sahifasida "
    "«Get API Key» tugmasini bosing.\n"
    "3️⃣ Chiqqan kalitni nusxalang (<b>Copy</b>).\n"
    "4️⃣ Shu yerga yuboring — bot tekshirib, ulaydi.\n\n"
    "ℹ️ Bepul rejimda oyiga ~50 ta rasm (past sifatda). Ko'proq kerak bo'lsa, remove.bg'da kredit sotib oling.\n"
    "Bekor qilish: /cancel"
)


async def check_key(key: str) -> tuple[bool, str]:
    """Kalitni remove.bg hisob so'rovi bilan tekshiradi (kredit sarflamaydi)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get("https://api.remove.bg/v1.0/account", headers={"X-Api-Key": key}) as r:
                if r.status == 200:
                    j = await r.json()
                    a = (j.get("data") or {}).get("attributes") or {}
                    cr = (a.get("credits") or {}).get("total")
                    free = (a.get("api") or {}).get("free_calls")
                    return True, f"Kredit: {cr}, bepul so'rov: {free}"
                if r.status in (401, 403):
                    return False, "Kalit noto'g'ri."
                return False, f"remove.bg javobi: {r.status}"
    except Exception as e:
        return False, f"Ulanib bo'lmadi: {str(e)[:100]}"


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
    apikey = State()


def panel_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔑 remove.bg kaliti" + (" ✅" if api_key() else " ❗"), callback_data="ad:key")],
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="ad:bc")],
        [InlineKeyboardButton(text="👋 Kirish matni", callback_data="ad:welcome")],
        [InlineKeyboardButton(text="✍️ Rasm osti matni", callback_data="ad:cap")],
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


# --- remove.bg kalitini ulash ---

@dp.callback_query(F.data == "ad:key", ADMIN)
async def ad_key(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.apikey)
    await c.message.answer(KEY_HELP, parse_mode="HTML", disable_web_page_preview=True)
    await c.answer()


@dp.message(Command("key"), ADMIN)
async def cmd_key(m: Message, state: FSMContext):
    await state.set_state(AdminSt.apikey)
    await m.answer(KEY_HELP, parse_mode="HTML", disable_web_page_preview=True)


@dp.message(AdminSt.apikey, ADMIN, F.text)
async def ad_key_save(m: Message, state: FSMContext):
    key = m.text.strip()
    try:
        await m.delete()  # kalit chatda ko'rinib qolmasin
    except Exception:
        pass
    if " " in key or len(key) < 16:
        return await m.answer("Bu API kalitga o'xshamaydi. remove.bg'dan nusxalab, qayta yuboring (yoki /cancel).")
    ok, info = await check_key(key)
    if not ok:
        return await m.answer(f"❌ {info}\nQayta yuboring (yoki /cancel).")
    set_setting("removebg_key", key)
    await state.clear()
    await m.answer(f"✅ Kalit ulandi, bot ishga tayyor!\n{info}\n\nEndi menga rasm yuborib sinab ko'ring.", reply_markup=panel_kb())


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


# --- Rasm ostidagi matn (caption) ---

def caption_or_none() -> str | None:
    return get_setting("caption") or None


@dp.callback_query(F.data == "ad:cap", ADMIN)
async def ad_caption(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.caption)
    current = get_setting("caption") or "yo'q"
    await c.message.answer(
        f"Hozirgi matn: {current}\n\n"
        "Tayyor rasm ostida chiqadigan yangi matnni yuboring "
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
        return await m.answer("✅ Rasm osti matni o'chirildi.", reply_markup=panel_kb())
    if len(m.text) > 1000:
        return await m.answer("Matn juda uzun (1000 belgidan oshmasin). Qayta yuboring.")
    set_setting("caption", m.html_text)
    await state.clear()
    await m.answer("✅ Saqlandi. Endi tayyor rasmlar ostida shu matn chiqadi.", reply_markup=panel_kb())


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


# ---------- Fonni olib tashlash ----------

SEM = asyncio.Semaphore(2)  # bir vaqtda ko'pi bilan 2 ta rasm


class NoKey(RuntimeError):
    pass


async def remove_bg(image: bytes) -> bytes:
    key = api_key()
    if key:
        form = aiohttp.FormData()
        form.add_field("image_file", image, filename="image.jpg")
        form.add_field("size", "auto")
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(
                "https://api.remove.bg/v1.0/removebg",
                data=form,
                headers={"X-Api-Key": key},
            ) as r:
                if r.status != 200:
                    raise RuntimeError(f"remove.bg {r.status}: {(await r.text())[:200]}")
                return await r.read()
    try:
        from rembg import remove
    except ImportError:
        raise NoKey("remove.bg kaliti ulanmagan")
    return await asyncio.to_thread(remove, image)


async def process(m: Message, file_obj, size: int | None):
    if size and size > MAX_IMAGE_MB * 1024 * 1024:
        return await m.answer(f"Rasm {MAX_IMAGE_MB} MB dan katta.")
    if not api_key():
        try:
            import rembg  # noqa: F401
        except ImportError:
            if is_admin(m.from_user.id):
                await m.answer("⚠️ remove.bg kaliti ulanmagan. Avval kalitni ulang:")
                return await m.answer(KEY_HELP, parse_mode="HTML", disable_web_page_preview=True,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                          InlineKeyboardButton(text="🔑 Kalitni yuborish", callback_data="ad:key")]]))
            return await m.answer("⚙️ Bot hali sozlanmagan. Iltimos, keyinroq urinib ko'ring.")
    wait = await m.answer("⏳ Fon olib tashlanmoqda...")
    try:
        buf: BytesIO = await m.bot.download(file_obj)
        async with SEM:
            result = await remove_bg(buf.getvalue())
        await m.answer_document(
            BufferedInputFile(result, filename="fon_olib_tashlandi.png"),
            caption=caption_or_none(),
            parse_mode="HTML",
        )
        await wait.delete()
    except Exception as e:
        logging.exception("removebg error")
        text = "Rasmni qayta ishlab bo'lmadi. Boshqa rasm bilan urinib ko'ring."
        if is_admin(m.from_user.id):
            msg = str(e)
            if "remove.bg 402" in msg:
                text += "\n\n🛠 remove.bg kreditingiz tugagan. remove.bg'da kredit sotib oling yoki yangi kalit yuboring: /key"
            elif "remove.bg 403" in msg or "remove.bg 401" in msg:
                text += "\n\n🛠 API kalit yaroqsiz. Yangi kalit yuboring: /key"
            else:
                text += f"\n\n🛠 {msg[:300]}"
        await wait.edit_text(text)


# ---------- Bot handlerlari ----------

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(get_setting("welcome", DEFAULT_WELCOME), parse_mode="HTML")
    if is_admin(m.from_user.id) and not api_key():
        await m.answer(
            "⚠️ Bot hali ishlamaydi: remove.bg API kaliti ulanmagan.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔑 Kalitni ulash", callback_data="ad:key")]]),
        )


@dp.message(F.photo)
async def on_photo(m: Message):
    p = m.photo[-1]
    await process(m, p, p.file_size)


@dp.message(F.document.mime_type.startswith("image/"))
async def on_image_file(m: Message):
    await process(m, m.document, m.document.file_size)


@dp.message()
async def other(m: Message):
    await m.answer("🖼 Fonini olib tashlash uchun rasm yuboring.")


async def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")
    await dp.start_polling(Bot(TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
